"""Evaluator — offline and online evaluation pipelines for Memory Programs.

Both pipelines use multi-turn conversations where messages accumulate across steps,
matching the design document's specified interaction pattern.
"""

from __future__ import annotations

import collections
import json
import re
from typing import Any

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
    compile_memory_program,
    extract_dataclass_schema,
)
from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig
from programmaticmemory.evolution.types import DataItem, EvalMode, EvalResult, FailedCase, MemoryProgram, Scorer
from programmaticmemory.logging.logger import get_logger


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

    def __init__(self, model: str = "openrouter/deepseek/deepseek-v3.2") -> None:
        self.model = model

    def __call__(self, output: str, expected: str) -> float:
        response = litellm.completion(
            model=self.model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are a strict judge. Determine if the output answers the question correctly "
                        "based on the expected answer. Reply ONLY with 1 (correct) or 0 (incorrect)."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Expected answer: {expected}\nActual output: {output}\n\nScore (0 or 1):",
                },
            ],
            temperature=0.0,
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


def _llm_call(model: str, messages: list[dict], temperature: float = 0.0) -> str:
    """Task agent LLM call (separate from Toolkit's LLM)."""
    response = litellm.completion(model=model, messages=messages, temperature=temperature)
    return response.choices[0].message.content


class MemoryEvaluator:
    """Evaluates a MemoryProgram on a dataset using offline or online pipeline.

    Both pipelines use multi-turn conversations where messages accumulate
    across steps within each sample, as specified in the design document.
    """

    def __init__(
        self,
        scorer: Scorer | None = None,
        task_model: str = "openrouter/deepseek/deepseek-v3.2",
        toolkit_config: ToolkitConfig | None = None,
        batch_process: bool = True,
    ) -> None:
        self.scorer = scorer or ExactMatchScorer()
        self.task_model = task_model
        self.toolkit_config = toolkit_config
        self.batch_process = batch_process
        self.logger = get_logger()

    @weave.op()
    def evaluate(
        self,
        program: MemoryProgram,
        train_data: list[DataItem],
        val_data: list[DataItem],
        eval_mode: EvalMode = EvalMode.OFFLINE,
    ) -> EvalResult:
        """Run evaluation pipeline and return results."""
        compile_result = compile_memory_program(program.source_code)
        if isinstance(compile_result, CompileError):
            self.logger.log(f"Compile failed: {compile_result.message}", header="EVAL")
            return EvalResult(
                score=0.0,
                logs=[f"Compile error: {compile_result.message} — {compile_result.details}"],
            )

        obs_cls, query_cls, memory_cls = compile_result
        obs_schema = extract_dataclass_schema(obs_cls)
        query_schema = extract_dataclass_schema(query_cls)

        toolkit = Toolkit(self.toolkit_config)
        try:
            memory = memory_cls(toolkit)
        except Exception as e:
            return EvalResult(score=0.0, logs=[f"Memory instantiation failed: {e}"])

        try:
            if eval_mode == EvalMode.OFFLINE:
                return self._evaluate_offline(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
            else:
                return self._evaluate_online(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
        finally:
            toolkit.close()

    # ── Offline ─────────────────────────────────────────────────────────────

    def _evaluate_offline(
        self,
        memory: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
    ) -> EvalResult:
        """Offline: Batch ingest train (LLM generates observations), then evaluate val."""
        logs: list[str] = []

        if self.batch_process:
            # Batch all observation generation prompts in one call
            all_messages = [
                [{"role": "user", "content": build_observation_generation_prompt(item.raw_text, obs_schema)}]
                for item in train_data
            ]
            responses = self._batch_llm_call(all_messages)
            for item, content in zip(train_data, responses, strict=True):
                if content is None:
                    logs.append(f"Failed to generate observation for: {item.raw_text[:60]}")
                    continue
                try:
                    obs = obs_cls(**_parse_json_from_llm(content))
                    memory.write(obs)
                except Exception as e:
                    logs.append(f"Obs parse/write failed: {e}")
        else:
            # Train: generate observations via LLM and write
            for item in train_data:
                obs = self._generate_observation_standalone(item.raw_text, obs_cls, obs_schema)
                if obs is None:
                    logs.append(f"Failed to generate observation for: {item.raw_text[:60]}")
                    continue
                try:
                    memory.write(obs)
                except Exception as e:
                    logs.append(f"Write failed: {e}")

        # Val: multi-turn query → read → answer → score
        return self._evaluate_val(memory, query_cls, query_schema, val_data, logs, toolkit)

    # ── Online ──────────────────────────────────────────────────────────────

    def _evaluate_online(
        self,
        memory: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
    ) -> EvalResult:
        """Online: Interleaved multi-turn train, then evaluate val."""
        logs: list[str] = []
        if self.batch_process:
            self._online_train_batched(memory, obs_cls, query_cls, obs_schema, query_schema, train_data, logs)
        else:
            self._online_train_sequential(
                memory, obs_cls, query_cls, obs_schema, query_schema, train_data, logs, toolkit
            )
        return self._evaluate_val(memory, query_cls, query_schema, val_data, logs, toolkit)

    def _online_train_sequential(
        self,
        memory: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
    ) -> None:
        """Online train sequential: one item at a time, multi-turn conversation per item.

        Each train sample is a multi-turn conversation:
          Step 1: generate query (user → assistant)
          Step 2: read memory + answer (user → assistant)
          Step 3: generate observation with feedback (user → assistant)
          Step 4: write observation to memory
        """
        for item in train_data:
            messages: list[dict[str, str]] = []

            # Step 1: generate query
            messages.append(
                {
                    "role": "user",
                    "content": build_query_generation_prompt(item.question, query_schema),
                }
            )
            try:
                query_json = _llm_call(self.task_model, messages)
            except Exception as e:
                logs.append(f"Train query generation failed: {e}")
                continue
            messages.append({"role": "assistant", "content": query_json})

            try:
                query = query_cls(**_parse_json_from_llm(query_json))
            except Exception as e:
                logs.append(f"Train query parse failed: {e}")
                continue

            # Read memory
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Train read failed: {e}")

            # Step 2: answer based on retrieved memory
            messages.append(
                {
                    "role": "user",
                    "content": build_retrieved_memory_prompt(retrieved_str),
                }
            )
            try:
                answer = _llm_call(self.task_model, messages)
            except Exception as e:
                logs.append(f"Train answer generation failed: {e}")
                continue
            messages.append({"role": "assistant", "content": answer})

            # Score for feedback
            score = self.scorer(answer, item.expected_answer)
            evaluation_result = f"Score: {score:.1f} ({'correct' if score >= 1.0 else 'incorrect'})"

            # Step 3: generate observation with feedback context
            messages.append(
                {
                    "role": "user",
                    "content": build_observation_with_feedback_prompt(
                        evaluation_result, item.expected_answer, obs_schema
                    ),
                }
            )
            try:
                obs_json = _llm_call(self.task_model, messages)
            except Exception as e:
                logs.append(f"Train observation generation failed: {e}")
                continue

            try:
                obs = obs_cls(**_parse_json_from_llm(obs_json))
            except Exception as e:
                logs.append(f"Train observation parse failed: {e}")
                continue

            # Step 4: write to memory
            try:
                memory.write(obs)
            except Exception as e:
                logs.append(f"Train write failed: {e}")

    def _online_train_batched(
        self,
        memory: Any,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        logs: list[str],
    ) -> None:
        """Online train batched: 3 rounds of batch_completion, then serial writes."""
        if not train_data:
            return

        # Round 1: query generation for all items
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema)}]
            for item in train_data
        ]
        round1_responses = self._batch_llm_call(round1_messages)

        # Parse queries + serial reads
        # slot[i] = (query_obj, query_json_str, retrieved_str) or None
        slots: list[tuple | None] = []
        for _item, content in zip(train_data, round1_responses, strict=True):
            if content is None:
                logs.append("Train query generation failed (batch error)")
                slots.append(None)
                continue
            try:
                query = query_cls(**_parse_json_from_llm(content))
            except Exception as e:
                logs.append(f"Train query parse failed: {e}")
                slots.append(None)
                continue
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Train read failed: {e}")
            slots.append((query, content, retrieved_str))

        # Round 2: answer generation for valid slots
        valid = [(i, s) for i, s in enumerate(slots) if s is not None]
        round2_messages = [
            [
                {"role": "user", "content": build_query_generation_prompt(train_data[i].question, query_schema)},
                {"role": "assistant", "content": s[1]},
                {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
            ]
            for i, s in valid
        ]
        round2_responses = self._batch_llm_call(round2_messages)

        # Score answers for feedback; build tuples for round 3
        # (item, slot, msgs_so_far, answer, score, evaluation_result)
        answered: list[tuple] = []
        for (i, s), answer in zip(valid, round2_responses, strict=True):
            item = train_data[i]
            if answer is None:
                logs.append("Train answer generation failed (batch error)")
                continue
            score = self.scorer(answer, item.expected_answer)
            evaluation_result = f"Score: {score:.1f} ({'correct' if score >= 1.0 else 'incorrect'})"
            msgs_so_far = [
                {"role": "user", "content": build_query_generation_prompt(item.question, query_schema)},
                {"role": "assistant", "content": s[1]},
                {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
                {"role": "assistant", "content": answer},
            ]
            answered.append((item, s, msgs_so_far, answer, score, evaluation_result))

        # Round 3: observation generation with feedback
        round3_messages = [
            msgs_so_far
            + [
                {
                    "role": "user",
                    "content": build_observation_with_feedback_prompt(
                        evaluation_result, item.expected_answer, obs_schema
                    ),
                }
            ]
            for item, s, msgs_so_far, answer, score, evaluation_result in answered
        ]
        round3_responses = self._batch_llm_call(round3_messages)

        # Serial writes
        for (_item, _s, _msgs_so_far, _answer, _score, _ev), obs_content in zip(
            answered, round3_responses, strict=True
        ):
            if obs_content is None:
                logs.append("Train observation generation failed (batch error)")
                continue
            try:
                obs = obs_cls(**_parse_json_from_llm(obs_content))
                memory.write(obs)
            except Exception as e:
                logs.append(f"Train observation parse/write failed: {e}")

    # ── Shared validation ───────────────────────────────────────────────────

    def _evaluate_val(
        self,
        memory: Any,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
    ) -> EvalResult:
        """Validation: query → read → answer → score. No writes."""
        if self.batch_process:
            return self._evaluate_val_batched(memory, query_cls, query_schema, val_data, logs, toolkit)
        return self._evaluate_val_sequential(memory, query_cls, query_schema, val_data, logs, toolkit)

    def _evaluate_val_sequential(
        self,
        memory: Any,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
    ) -> EvalResult:
        """Validation: multi-turn query → read → answer → score. No writes.

        Each val sample is a multi-turn conversation:
          Step 1: generate query (user → assistant)
          Step 2: read memory + answer (user → assistant)
        """
        scores: list[float] = []
        outputs: list[str] = []
        failed_cases: list[FailedCase] = []

        for item in val_data:
            messages: list[dict[str, str]] = []

            # Step 1: generate query
            messages.append(
                {
                    "role": "user",
                    "content": build_query_generation_prompt(item.question, query_schema),
                }
            )
            try:
                query_json = _llm_call(self.task_model, messages)
            except Exception as e:
                self.logger.log(f"Val query generation failed: {e}", header="EVAL")
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        memory_logs=list(toolkit.logger.logs),
                    )
                )
                continue
            messages.append({"role": "assistant", "content": query_json})

            try:
                query = query_cls(**_parse_json_from_llm(query_json))
            except Exception as e:
                self.logger.log(f"Val query parse failed: {e}", header="EVAL")
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        conversation_history=list(messages),
                        memory_logs=list(toolkit.logger.logs),
                    )
                )
                continue

            # Read memory
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Val read failed: {e}")

            # Step 2: answer based on retrieved memory
            messages.append(
                {
                    "role": "user",
                    "content": build_retrieved_memory_prompt(retrieved_str),
                }
            )
            try:
                answer = _llm_call(self.task_model, messages)
            except Exception as e:
                self.logger.log(f"Val answer generation failed: {e}", header="EVAL")
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        conversation_history=list(messages),
                        memory_logs=list(toolkit.logger.logs),
                    )
                )
                continue
            messages.append({"role": "assistant", "content": answer})

            outputs.append(answer)

            # Score
            score = self.scorer(answer, item.expected_answer)
            scores.append(score)

            if score < 1.0:
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output=answer,
                        expected=item.expected_answer,
                        score=score,
                        conversation_history=list(messages),
                        memory_logs=list(toolkit.logger.logs),
                    )
                )

        avg_score = sum(scores) / len(scores) if scores else 0.0
        logs.append(f"Val score: {avg_score:.3f} ({len(scores)} cases)")
        return EvalResult(
            score=avg_score,
            per_case_scores=scores,
            per_case_outputs=outputs,
            failed_cases=failed_cases,
            logs=logs,
        )

    def _evaluate_val_batched(
        self,
        memory: Any,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
    ) -> EvalResult:
        """Two-round batched val: all query prompts → serial reads → all answer prompts."""
        if not val_data:
            logs.append("Val score: 0.000 (0 cases)")
            return EvalResult(score=0.0, per_case_scores=[], per_case_outputs=[], failed_cases=[], logs=logs)

        # Round 1: batch all query generation
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema)}]
            for item in val_data
        ]
        round1_responses = self._batch_llm_call(round1_messages)

        # Parse queries and do serial memory reads
        # slot[i] = (query_obj, query_json_str, retrieved_str) or None if failed
        slots: list[tuple | None] = []
        for _item, content in zip(val_data, round1_responses, strict=True):
            if content is None:
                slots.append(None)
                continue
            try:
                query = query_cls(**_parse_json_from_llm(content))
            except Exception as e:
                self.logger.log(f"Val query parse failed: {e}", header="EVAL")
                slots.append(None)
                continue
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Val read failed: {e}")
            slots.append((query, content, retrieved_str))

        # Round 2: batch answer generation only for successful slots
        valid = [(i, s) for i, s in enumerate(slots) if s is not None]
        round2_messages = [
            [
                {"role": "user", "content": build_query_generation_prompt(val_data[i].question, query_schema)},
                {"role": "assistant", "content": s[1]},
                {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
            ]
            for i, s in valid
        ]
        round2_responses = self._batch_llm_call(round2_messages)

        # Assemble results
        scores: list[float] = []
        outputs: list[str] = []
        failed_cases: list[FailedCase] = []

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
                        memory_logs=list(toolkit.logger.logs),
                    )
                )
                continue

            answer = round2_responses[valid_idx]
            valid_idx += 1

            if answer is None:
                self.logger.log("Val answer generation failed (batch error)", header="EVAL")
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        memory_logs=list(toolkit.logger.logs),
                    )
                )
                continue

            outputs.append(answer)
            score = self.scorer(answer, item.expected_answer)
            scores.append(score)
            if score < 1.0:
                conv = [
                    {"role": "user", "content": build_query_generation_prompt(item.question, query_schema)},
                    {"role": "assistant", "content": slot[1]},
                    {"role": "user", "content": build_retrieved_memory_prompt(slot[2])},
                    {"role": "assistant", "content": answer},
                ]
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output=answer,
                        expected=item.expected_answer,
                        score=score,
                        conversation_history=conv,
                        memory_logs=list(toolkit.logger.logs),
                    )
                )

        avg_score = sum(scores) / len(scores) if scores else 0.0
        logs.append(f"Val score: {avg_score:.3f} ({len(scores)} cases)")
        return EvalResult(
            score=avg_score,
            per_case_scores=scores,
            per_case_outputs=outputs,
            failed_cases=failed_cases,
            logs=logs,
        )

    # ── Standalone helpers (offline only) ────────────────────────────────────

    def _generate_observation_standalone(self, raw_text: str, obs_cls: type, obs_schema: str) -> Any | None:
        """Generate an Observation from raw text via a single LLM call.

        Used only for offline train (batch ingest). Online uses the multi-turn flow.
        """
        prompt = build_observation_generation_prompt(raw_text, obs_schema)
        try:
            response = _llm_call(self.task_model, [{"role": "user", "content": prompt}])
            data = _parse_json_from_llm(response)
            return obs_cls(**data)
        except Exception as e:
            self.logger.log(f"Observation generation failed: {e}", header="EVAL")
            return None

    def _batch_llm_call(self, all_messages: list[list[dict]]) -> list[str | None]:
        """Fan out independent LLM calls via litellm.batch_completion.

        Returns a list of content strings (same length as all_messages).
        Failed entries are None (error already logged).
        """
        if not all_messages:
            return []
        responses = litellm.batch_completion(model=self.task_model, messages=all_messages)
        results: list[str | None] = []
        for resp in responses:
            if isinstance(resp, Exception):
                self.logger.log(f"Batch LLM call failed: {resp}", header="EVAL")
                results.append(None)
            else:
                results.append(resp.choices[0].message.content)
        return results
