"""Evaluator — offline and online evaluation pipelines for Knowledge Base Programs.

Both pipelines use multi-turn conversations where messages accumulate across steps,
matching the design document's specified interaction pattern.
"""

from __future__ import annotations

import collections
import concurrent.futures
import json
import re
from typing import Any, NamedTuple

import litellm
import weave

from programmaticmemory.evolution.prompts import (
    build_observation_generation_prompt,
    build_observation_with_feedback_prompt,
    build_query_generation_prompt,
    build_retrieved_memory_prompt,
)
from programmaticmemory.evolution.sandbox import (
    CompileError,
    compile_kb_program,
    extract_dataclass_schema,
)
from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig
from programmaticmemory.evolution.types import (
    DataItem,
    EvalResult,
    FailedCase,
    KBProgram,
    Scorer,
    TrainExample,
)
from programmaticmemory.logging.logger import get_logger

MEMORY_OP_TIMEOUT = 5.0
MEMORY_READ_MAX_CHARS = 1000


class RuntimeViolationError(Exception):
    """Raised when memory.write/read violates runtime constraints (timeout or output size)."""


def _guarded_write(kb: Any, obs: Any, timeout: float = MEMORY_OP_TIMEOUT) -> None:
    """Wrap kb.write(obs) with timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kb.write, obs)
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeViolationError(f"kb.write() timed out after {timeout}s")


def _guarded_read(
    kb: Any, query: Any, timeout: float = MEMORY_OP_TIMEOUT, max_chars: int = MEMORY_READ_MAX_CHARS
) -> Any:
    """Wrap kb.read(query) with timeout + output length check."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kb.read, query)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeViolationError(f"kb.read() timed out after {timeout}s")
    result_str = str(result) if result is not None else ""
    if len(result_str) > max_chars:
        raise RuntimeViolationError(f"kb.read() returned {len(result_str)} chars (limit: {max_chars})")
    return result


class ExactMatchScorer:
    """Containment-based matching with normalization."""

    def __call__(self, output: str, expected: str) -> float:
        output_norm = self._normalize(output)
        expected_norm = self._normalize(expected)
        if expected_norm in output_norm:
            return 1.0
        return 0.0

    @staticmethod
    def _normalize(text: str) -> str:
        text = text.lower().strip()
        text = re.sub(r"[^\w\s]", "", text)
        text = re.sub(r"\s+", " ", text)
        return text


class TokenF1Scorer:
    """Token-level F1 with SQuAD-style normalization (no stemming)."""

    def __call__(self, output: str, expected: str) -> float:
        out_tok = self._normalize_and_tokenize(output)
        exp_tok = self._normalize_and_tokenize(expected)
        if not exp_tok or not out_tok:
            return float(out_tok == exp_tok)
        common = collections.Counter(out_tok) & collections.Counter(exp_tok)
        num = sum(common.values())
        if num == 0:
            return 0.0
        p, r = num / len(out_tok), num / len(exp_tok)
        return 2 * p * r / (p + r)

    @staticmethod
    def _normalize_and_tokenize(text: str) -> list[str]:
        text = text.lower()
        text = re.sub(r"\b(a|an|the)\b", " ", text)
        text = re.sub(r"[^\w\s]", "", text)
        return text.split()


class LLMJudgeScorer:
    """LLM-as-judge scorer, returns 0.0 or 1.0."""

    def __init__(self, model: str) -> None:
        self.model = model

    def __call__(self, output: str, expected: str) -> float:
        response = litellm.completion(
            model=self.model,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "You are a strict judge. Determine if the output answers the question correctly "
                        "based on the expected answer. Reply ONLY with 1 (correct) or 0 (incorrect).\n\n"
                        f"Expected answer: {expected}\nActual output: {output}\n\nScore (0 or 1):"
                    ),
                },
            ],
            temperature=0.0,
            caching=True,
        )
        text = response.choices[0].message.content.strip()
        try:
            return float(int(text))
        except (ValueError, TypeError):
            return 0.0


def _parse_json_from_llm(text: str) -> dict:
    """Extract JSON from LLM output, handling markdown code blocks."""
    text = text.strip()
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if match:
        text = match.group(1).strip()
    return json.loads(text)


class _QuerySlot(NamedTuple):
    """Parsed result from a query generation + memory read step."""

    query: Any
    query_json: str  # raw assistant response
    retrieved_str: str  # str(memory.read(query)) or error message
    query_prompt: str  # built user prompt for query generation
    retrieved_prompt: str  # built user prompt for retrieved memory


class MemoryEvaluator:
    """Evaluates a KBProgram on a dataset using offline or online pipeline.

    Both pipelines use multi-turn conversations where messages accumulate
    across steps within each sample, as specified in the design document.
    """

    def __init__(
        self,
        scorer: Scorer | None = None,
        *,
        task_model: str,
        toolkit_config: ToolkitConfig,
    ) -> None:
        self.scorer = scorer or ExactMatchScorer()
        self.task_model = task_model
        self.toolkit_config = toolkit_config
        self.logger = get_logger()

    @weave.op()
    def evaluate(
        self,
        program: KBProgram,
        train_data: list[DataItem],
        val_data: list[DataItem],
    ) -> EvalResult:
        """Run evaluation pipeline and return results.

        Pipeline is inferred from train data: items with raw_text use batch
        observation ingestion; items without raw_text use interactive QA training.
        """
        compile_result = compile_kb_program(program.source_code)
        if isinstance(compile_result, CompileError):
            self.logger.log(f"Compile failed: {compile_result.message}", header="EVAL")
            return EvalResult(
                score=0.0,
                logs=[f"Compile error: {compile_result.message} — {compile_result.details}"],
            )

        compiled = compile_result
        obs_schema = extract_dataclass_schema(compiled.obs_cls)
        query_schema = extract_dataclass_schema(compiled.query_cls)

        toolkit = Toolkit(self.toolkit_config)
        try:
            kb = compiled.kb_cls(toolkit)
        except Exception as e:
            return EvalResult(score=0.0, logs=[f"KnowledgeBase instantiation failed: {e}"])

        try:
            if train_data and train_data[0].raw_text:
                self.logger.log(
                    f"Pipeline: offline (batch obs ingestion), train={len(train_data)}, val={len(val_data)}",
                    header="EVAL",
                )
                return self._evaluate_offline(
                    kb,
                    compiled.obs_cls,
                    compiled.query_cls,
                    obs_schema,
                    query_schema,
                    train_data,
                    val_data,
                    toolkit,
                    instruction_observation=compiled.instruction_observation,
                    instruction_query=compiled.instruction_query,
                    instruction_response=compiled.instruction_response,
                )
            else:
                self.logger.log(
                    f"Pipeline: online (interactive QA), train={len(train_data)}, val={len(val_data)}",
                    header="EVAL",
                )
                return self._evaluate_online(
                    kb,
                    compiled.obs_cls,
                    compiled.query_cls,
                    obs_schema,
                    query_schema,
                    train_data,
                    val_data,
                    toolkit,
                    instruction_observation=compiled.instruction_observation,
                    instruction_query=compiled.instruction_query,
                    instruction_response=compiled.instruction_response,
                )
        except RuntimeViolationError as e:
            self.logger.log(f"Runtime violation: {e}", header="EVAL")
            return EvalResult(score=0.0, logs=[f"Runtime violation: {e}"], runtime_violation=str(e))
        finally:
            toolkit.close()

    # ── Offline ─────────────────────────────────────────────────────────────

    def _evaluate_offline(
        self,
        kb: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
        *,
        instruction_observation: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
    ) -> EvalResult:
        """Offline: Batch ingest train (LLM generates observations), then evaluate val."""
        logs: list[str] = []

        # Batch all observation generation prompts in one call
        self.logger.log(f"Train: generating observations for {len(train_data)} items", header="EVAL")
        all_messages = [
            [
                {
                    "role": "user",
                    "content": build_observation_generation_prompt(item.raw_text, obs_schema, instruction_observation),
                }
            ]
            for item in train_data
        ]
        responses = self._batch_llm_call(all_messages, json_mode=True)

        train_examples = []
        write_count = 0
        fail_count = 0
        for idx, (msgs, item, content) in enumerate(zip(all_messages, train_data, responses, strict=True)):
            if idx < 3 and content is not None:
                train_examples.append(TrainExample(messages=[*msgs, {"role": "assistant", "content": content}]))
            if content is None:
                logs.append(f"Failed to generate observation for: {item.raw_text}")
                fail_count += 1
                continue
            try:
                obs = obs_cls(**_parse_json_from_llm(content))
                _guarded_write(kb, obs)
                write_count += 1
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Obs parse/write failed: {e}")
                fail_count += 1

        self.logger.log(f"Train: write phase complete — {write_count} written, {fail_count} failed", header="EVAL")

        # Val: multi-turn query → read → answer → score
        self.logger.log(f"Val: starting evaluation on {len(val_data)} items", header="EVAL")
        result = self._evaluate_val(
            kb,
            query_cls,
            query_schema,
            val_data,
            logs,
            toolkit,
            instruction_query=instruction_query,
            instruction_response=instruction_response,
        )
        result.train_examples = train_examples
        return result

    # ── Online ──────────────────────────────────────────────────────────────

    def _evaluate_online(
        self,
        kb: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
        *,
        instruction_observation: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
    ) -> EvalResult:
        """Online: Interleaved multi-turn train, then evaluate val."""
        logs: list[str] = []
        self.logger.log(f"Train: interactive QA for {len(train_data)} items (3 rounds)", header="EVAL")
        train_examples = self._online_train_batched(
            kb,
            obs_cls,
            query_cls,
            obs_schema,
            query_schema,
            train_data,
            logs,
            instruction_observation=instruction_observation,
            instruction_query=instruction_query,
            instruction_response=instruction_response,
        )
        self.logger.log("Train: interactive QA complete", header="EVAL")
        self.logger.log(f"Val: starting evaluation on {len(val_data)} items", header="EVAL")
        result = self._evaluate_val(
            kb,
            query_cls,
            query_schema,
            val_data,
            logs,
            toolkit,
            instruction_query=instruction_query,
            instruction_response=instruction_response,
        )
        result.train_examples = train_examples
        return result

    def _online_train_batched(
        self,
        kb: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        logs: list[str],
        *,
        instruction_observation: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
    ) -> list[TrainExample]:
        """Online train batched: 3 rounds of batch_completion, then serial writes."""
        if not train_data:
            return []

        # Round 1: query generation for all items
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema, instruction_query)}]
            for item in train_data
        ]
        round1_responses = self._batch_llm_call(round1_messages, json_mode=True)

        # Parse queries + serial reads
        slots = self._parse_queries_and_read(
            query_cls,
            kb,
            round1_messages,
            round1_responses,
            logs,
            log_prefix="Train",
            instruction_query=instruction_query,
            instruction_response=instruction_response,
        )

        # Round 2: answer generation for valid slots
        valid = [(i, s) for i, s in enumerate(slots) if s is not None]
        round2_messages = [
            [
                {"role": "user", "content": s.query_prompt},
                {"role": "assistant", "content": s.query_json},
                {"role": "user", "content": s.retrieved_prompt},
            ]
            for _i, s in valid
        ]
        round2_responses = self._batch_llm_call(round2_messages)

        # Score answers for feedback; build context for round 3
        round3_items: list[tuple[DataItem, list[dict], str]] = []
        for (i, _s), r2_msgs, answer in zip(valid, round2_messages, round2_responses, strict=True):
            if answer is None:
                logs.append("Train answer generation failed (batch error)")
                continue
            score = self.scorer(answer, train_data[i].expected_answer)
            evaluation_result = f"Score: {score:.1f} ({'correct' if score >= 1.0 else 'incorrect'})"
            msgs_so_far = r2_msgs + [{"role": "assistant", "content": answer}]
            round3_items.append((train_data[i], msgs_so_far, evaluation_result))

        # Round 3: observation generation with feedback
        round3_messages = [
            msgs_so_far
            + [
                {
                    "role": "user",
                    "content": build_observation_with_feedback_prompt(
                        evaluation_result, item.expected_answer, obs_schema, instruction_observation
                    ),
                }
            ]
            for item, msgs_so_far, evaluation_result in round3_items
        ]
        round3_responses = self._batch_llm_call(round3_messages, json_mode=True)

        # Serial writes + capture train examples
        train_examples: list[TrainExample] = []
        for r3_msgs, obs_content in zip(round3_messages, round3_responses, strict=True):
            if obs_content is None:
                logs.append("Train observation generation failed (batch error)")
                continue
            if len(train_examples) < 3:
                train_examples.append(TrainExample(messages=[*r3_msgs, {"role": "assistant", "content": obs_content}]))
            try:
                obs = obs_cls(**_parse_json_from_llm(obs_content))
                _guarded_write(kb, obs)
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Train observation parse/write failed: {e}")

        return train_examples

    # ── Shared helpers ─────────────────────────────────────────────────────

    def _parse_queries_and_read(
        self,
        query_cls: type,
        kb: Any,
        round1_messages: list[list[dict]],
        responses: list[str | None],
        logs: list[str],
        log_prefix: str = "Val",
        *,
        instruction_query: str = "",
        instruction_response: str = "",
    ) -> list[_QuerySlot | None]:
        """Parse batch query responses, read knowledge base for each. Returns slots aligned with data."""
        slots: list[_QuerySlot | None] = []
        for msgs, content in zip(round1_messages, responses, strict=True):
            query_prompt = msgs[0]["content"]
            if content is None:
                logs.append(f"{log_prefix} query generation failed (batch error)")
                slots.append(None)
                continue
            try:
                query = query_cls(**_parse_json_from_llm(content))
            except Exception as e:
                logs.append(f"{log_prefix} query parse failed: {e}")
                slots.append(None)
                continue
            try:
                retrieved = _guarded_read(kb, query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except RuntimeViolationError:
                raise
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"{log_prefix} read failed: {e}")
            retrieved_prompt = build_retrieved_memory_prompt(retrieved_str, instruction_response)
            slots.append(_QuerySlot(query, content, retrieved_str, query_prompt, retrieved_prompt))
        return slots

    @staticmethod
    def _build_eval_result(
        scores: list[float],
        outputs: list[str],
        failed_cases: list[FailedCase],
        success_cases: list[FailedCase],
        logs: list[str],
    ) -> EvalResult:
        """Assemble the final EvalResult with average score logging."""
        avg_score = sum(scores) / len(scores) if scores else 0.0
        logs.append(f"Val score: {avg_score:.3f} ({len(scores)} cases)")
        return EvalResult(
            score=avg_score,
            per_case_scores=scores,
            per_case_outputs=outputs,
            failed_cases=failed_cases,
            success_cases=success_cases,
            logs=logs,
        )

    def _evaluate_val(
        self,
        kb: Any,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
        *,
        instruction_query: str = "",
        instruction_response: str = "",
    ) -> EvalResult:
        """Two-round batched val: all query prompts → serial reads → all answer prompts."""
        if not val_data:
            return self._build_eval_result([], [], [], [], logs)

        # Round 1: batch all query generation
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema, instruction_query)}]
            for item in val_data
        ]
        round1_responses = self._batch_llm_call(round1_messages, json_mode=True)

        # Parse queries and do serial knowledge base reads
        slots = self._parse_queries_and_read(
            query_cls,
            kb,
            round1_messages,
            round1_responses,
            logs,
            log_prefix="Val",
            instruction_query=instruction_query,
            instruction_response=instruction_response,
        )

        # Round 2: batch answer generation only for successful slots
        valid = [(i, s) for i, s in enumerate(slots) if s is not None]
        round2_messages = [
            [
                {"role": "user", "content": s.query_prompt},
                {"role": "assistant", "content": s.query_json},
                {"role": "user", "content": s.retrieved_prompt},
            ]
            for _i, s in valid
        ]
        round2_responses = self._batch_llm_call(round2_messages)

        # Assemble results
        scores: list[float] = []
        outputs: list[str] = []
        failed_cases: list[FailedCase] = []
        success_cases: list[FailedCase] = []
        log_snapshot = list(toolkit.logger.logs)

        valid_idx = 0
        for i, item in enumerate(val_data):
            slot = slots[i]
            if slot is None:
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        memory_logs=log_snapshot,
                    )
                )
                continue

            answer = round2_responses[valid_idx]
            valid_idx += 1

            if answer is None:
                logs.append("Val answer generation failed (batch error)")
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        memory_logs=log_snapshot,
                    )
                )
                continue

            outputs.append(answer)
            score = self.scorer(answer, item.expected_answer)
            scores.append(score)
            conv = [
                {"role": "user", "content": slot.query_prompt},
                {"role": "assistant", "content": slot.query_json},
                {"role": "user", "content": slot.retrieved_prompt},
                {"role": "assistant", "content": answer},
            ]
            case = FailedCase(
                question=item.question,
                output=answer,
                expected=item.expected_answer,
                score=score,
                conversation_history=conv,
                memory_logs=log_snapshot,
            )
            if score < 1.0:
                failed_cases.append(case)
            else:
                success_cases.append(case)

        result = self._build_eval_result(scores, outputs, failed_cases, success_cases, logs)
        self.logger.log(
            f"Val: complete — score={result.score:.3f}, {len(failed_cases)}/{len(val_data)} failed",
            header="EVAL",
        )
        return result

    def _batch_llm_call(self, all_messages: list[list[dict]], *, json_mode: bool = False) -> list[str | None]:
        """Fan out independent LLM calls via litellm.batch_completion.

        Returns a list of content strings (same length as all_messages).
        Failed entries are None (error already logged).
        """
        if not all_messages:
            return []
        kwargs: dict = dict(model=self.task_model, messages=all_messages, temperature=0.0, caching=True)
        if json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        responses = litellm.batch_completion(**kwargs)
        results: list[str | None] = []
        for resp in responses:
            if isinstance(resp, Exception):
                self.logger.log(f"Batch LLM call failed: {resp}", header="EVAL")
                results.append(None)
            else:
                results.append(resp.choices[0].message.content)
        return results
