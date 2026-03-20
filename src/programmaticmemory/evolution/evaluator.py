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
    build_knowledge_item_generation_prompt,
    build_knowledge_item_with_feedback_prompt,
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
    ValScorer,
)
from programmaticmemory.logging.logger import get_logger

# Module-level thread pool for batch LLM calls.  Reusing threads avoids the
# file-descriptor leak caused by litellm.batch_completion: each short-lived
# ThreadPoolExecutor thread opens a thread-local SQLite connection to the
# litellm disk-cache, and those connections are never explicitly closed when the
# thread dies.  A long-lived pool means threads (and their cache connections)
# are reused across calls.
_BATCH_POOL: concurrent.futures.ThreadPoolExecutor | None = None
_BATCH_POOL_SIZE = 10


def _get_batch_pool() -> concurrent.futures.ThreadPoolExecutor:
    global _BATCH_POOL
    if _BATCH_POOL is None:
        _BATCH_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=_BATCH_POOL_SIZE)
    return _BATCH_POOL


def set_batch_pool_size(size: int) -> None:
    """Set max concurrency for batch LLM calls. Must be called before first evaluation."""
    global _BATCH_POOL_SIZE, _BATCH_POOL
    _BATCH_POOL_SIZE = size
    _BATCH_POOL = None  # reset so next call creates a new pool


# Shared pool for _guarded_write / _guarded_read timeout enforcement.
_GUARD_POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4)

MEMORY_OP_TIMEOUT = 60.0
MEMORY_READ_MAX_CHARS = 3000


class RuntimeViolationError(Exception):
    """Raised when memory.write/read violates runtime constraints (timeout or output size)."""


def _guarded_write(kb: Any, item: Any, raw_text: str, timeout: float = MEMORY_OP_TIMEOUT) -> None:
    """Wrap kb.write(item, raw_text) with timeout and per-call LLM budget reset."""
    import time as _time

    if hasattr(kb, "toolkit"):
        kb.toolkit.reset_llm_budget()
    t0 = _time.monotonic()
    future = _GUARD_POOL.submit(kb.write, item, raw_text)
    try:
        future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise RuntimeViolationError(f"kb.write() timed out after {timeout}s")
    elapsed = _time.monotonic() - t0
    if elapsed > 10.0:
        logger = get_logger()
        logger.log(f"Slow kb.write(): {elapsed:.1f}s", header="DEBUG")


def _guarded_read(
    kb: Any, query: Any, timeout: float = MEMORY_OP_TIMEOUT, max_chars: int = MEMORY_READ_MAX_CHARS
) -> Any:
    """Wrap kb.read(query) with timeout, output length check, and per-call LLM budget reset."""
    import time as _time

    if hasattr(kb, "toolkit"):
        kb.toolkit.reset_llm_budget()
    t0 = _time.monotonic()
    future = _GUARD_POOL.submit(kb.read, query)
    try:
        result = future.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        future.cancel()
        raise RuntimeViolationError(f"kb.read() timed out after {timeout}s")
    elapsed = _time.monotonic() - t0
    if elapsed > 10.0:
        logger = get_logger()
        logger.log(f"Slow kb.read(): {elapsed:.1f}s", header="DEBUG")
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
        text = str(text).lower().strip()
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
        text = str(text).lower()
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
                {"role": "system", "content": " "},
                {
                    "role": "user",
                    "content": (
                        "You are a strict judge. Determine if the output answers the question correctly "
                        "based on the expected answer. Reply ONLY with 1 (correct) or 0 (incorrect).\n\n"
                        f"Expected answer: {expected}\nActual output: {output}\n\nScore (0 or 1):"
                    ),
                },
            ],
            caching=True,
        )
        text = response.choices[0].message.content.strip()
        try:
            return float(int(text))
        except (ValueError, TypeError):
            return 0.0


RUBRIC_GRADER_TEMPLATE = """
Your job is to look at a conversation and a rubric item, and score the last turn (i.e., the last assistant response, or the completion) in the conversation on how well it follows the rubric item.

# Conversation
<<conversation>>

# Rubric item
<<rubric_item>>

# Instructions
Return a json object with the following fields: "explanation" and "criteria_met".
- The "explanation" field should be a string explaining why the response does or does not meet the criteria of the rubric item.
- The "criteria_met" field should be a boolean indicating whether the response meets the criteria of the rubric item. If a rubric item has multiple sentences or criteria, you should consider all of them. If any of the criteria is not met, the answer should be false. Only return true is all of the criteria are met.
- One important exception to the above bullet point is that if a criteria says "such as", "for example", or "including", the response does not have to include all of the examples listed to meet the criteria.

# Example
Note that sometimes the criteria may be something undesirable and have a corresponding point value that is negative. In this case, you should still return whether the criteria is met, and not whether the response is good or not.
For example, if the critera says "Is overly verbose" and the response is concise, then the criteria is not met, and you should return:
```json
{"explanation": "The response is concise, so the criteria was not met.", "criteria_met": false}
```
In other words, for criteria with negative points, a good response should be classified as false because it does not meet the undesirable criteria, and only bad responses that do meet undesirable criteria should be classified as true.

# Final instruction
Return just the json object in markdown format. Do not include any other text in the response.
""".strip()


def _calculate_rubric_score(criteria: list[dict], grades: list[bool]) -> float:
    """Official HealthBench/PRBench scoring formula.

    criteria: [{"criterion": str, "points": float}, ...]
    grades: [True/False, ...] — per-criterion met/not-met

    Returns: sum(met_points) / sum(positive_points), clipped to [0, 1].
    Negative criteria that are met SUBTRACT from numerator.
    """
    total_positive = sum(c["points"] for c in criteria if c["points"] > 0)
    if total_positive == 0:
        return 0.0
    achieved = sum(c["points"] for c, met in zip(criteria, grades) if met)  # noqa: B905
    return max(0.0, min(1.0, achieved / total_positive))


class RubricValScorer:
    """Official rubric-based val scorer — per-criterion independent LLM judge.

    Replicates the exact evaluation methodology from:
    - openai/simple-evals healthbench_eval.py
    - scaleapi/PRBench util.py + constants.py

    Each criterion is graded independently with a separate LLM call.
    Score = sum(met_criteria_points) / sum(positive_points), clipped [0,1].
    """

    def __init__(self, judge_model: str, max_judge_tokens: int = 2048) -> None:
        self.judge_model = judge_model
        self.max_judge_tokens = max_judge_tokens

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
        always_on_knowledge: str = "",
        *,
        reasoning_effort: str | None = None,
    ) -> list[tuple[str, float]]:
        """Generate responses and grade each against rubric criteria."""
        results: list[tuple[str, float]] = []

        for item, memory_text in zip(items, retrieved):  # noqa: B905
            response = self._generate_response(
                item,
                memory_text,
                task_model,
                instruction_response,
                always_on_knowledge,
                reasoning_effort=reasoning_effort,
            )

            criteria = item.metadata.get("rubric_criteria", [])
            if not criteria:
                results.append((response, 0.0))
                continue

            conversation_text = self._format_conversation(item, response)
            grades = []
            for criterion in criteria:
                met = self._grade_single_criterion(conversation_text, criterion)
                grades.append(met)

            score = _calculate_rubric_score(criteria, grades)
            results.append((response, score))

        return results

    def _generate_response(
        self,
        item: DataItem,
        memory_text: str,
        task_model: str,
        instruction_response: str,
        always_on_knowledge: str = "",
        *,
        reasoning_effort: str | None = None,
    ) -> str:
        """Generate a single response to the conversation prompt."""
        parts = []
        if always_on_knowledge and always_on_knowledge.strip():
            parts.append(f"Context:\n{always_on_knowledge.strip()}")
        if memory_text and memory_text.strip():
            parts.append(f"Retrieved information:\n{memory_text.strip()}")
        if instruction_response and instruction_response.strip():
            parts.append(instruction_response.strip())
        parts.append(item.question)

        prompt = "\n\n".join(parts)
        extra = {}
        if reasoning_effort is not None:
            extra["reasoning_effort"] = reasoning_effort

        resp = litellm.completion(
            model=task_model,
            messages=[{"role": "user", "content": prompt}],
            caching=True,
            **extra,
        )
        return resp.choices[0].message.content.strip()

    def _format_conversation(self, item: DataItem, response: str) -> str:
        """Format conversation + response for the grader (official format)."""
        # item.question contains the formatted conversation
        # We append the model response as the last assistant turn
        return f"{item.question}\n\nassistant: {response}"

    def _grade_single_criterion(self, conversation_text: str, criterion: dict) -> bool:
        """Grade ONE criterion via independent LLM call (official method).

        Returns True if criterion is met, False otherwise.
        Uses the exact official GRADER_TEMPLATE.
        """
        rubric_str = f"[{criterion['points']}] {criterion['criterion']}"
        prompt = RUBRIC_GRADER_TEMPLATE.replace("<<conversation>>", conversation_text).replace(
            "<<rubric_item>>", rubric_str
        )

        for _attempt in range(3):
            try:
                resp = litellm.completion(
                    model=self.judge_model,
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=self.max_judge_tokens,
                    caching=True,
                )
                text = resp.choices[0].message.content.strip()
                text = re.sub(r"^```json\s*|\s*```$", "", text.strip())
                result = json.loads(text)
                if "criteria_met" in result:
                    val = result["criteria_met"]
                    if val is True or val is False:
                        return val
            except Exception:
                pass
        return False


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
        val_scorer: ValScorer | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        self.scorer = scorer or ExactMatchScorer()
        self.task_model = task_model
        self.toolkit_config = toolkit_config
        self.val_scorer = val_scorer
        self.reasoning_effort = reasoning_effort
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
        knowledge item ingestion; items without raw_text use interactive QA training.
        """
        compile_result = compile_kb_program(program.source_code)
        if isinstance(compile_result, CompileError):
            self.logger.log(f"Compile failed: {compile_result.message}", header="EVAL")
            return EvalResult(
                score=0.0,
                logs=[f"Compile error: {compile_result.message} — {compile_result.details}"],
            )

        compiled = compile_result
        ki_schema = extract_dataclass_schema(compiled.ki_cls)
        query_schema = extract_dataclass_schema(compiled.query_cls)

        toolkit = Toolkit(self.toolkit_config)
        try:
            kb = compiled.kb_cls(toolkit)
        except Exception as e:
            return EvalResult(score=0.0, logs=[f"KnowledgeBase instantiation failed: {e}"])

        try:
            if train_data and train_data[0].raw_text:
                self.logger.log(
                    f"Pipeline: offline (batch KI ingestion), train={len(train_data)}, val={len(val_data)}",
                    header="EVAL",
                )
                return self._evaluate_offline(
                    kb,
                    compiled.ki_cls,
                    compiled.query_cls,
                    ki_schema,
                    query_schema,
                    train_data,
                    val_data,
                    toolkit,
                    instruction_knowledge_item=compiled.instruction_knowledge_item,
                    instruction_query=compiled.instruction_query,
                    instruction_response=compiled.instruction_response,
                    always_on_knowledge=compiled.always_on_knowledge,
                )
            else:
                self.logger.log(
                    f"Pipeline: online (interactive QA), train={len(train_data)}, val={len(val_data)}",
                    header="EVAL",
                )
                return self._evaluate_online(
                    kb,
                    compiled.ki_cls,
                    compiled.query_cls,
                    ki_schema,
                    query_schema,
                    train_data,
                    val_data,
                    toolkit,
                    instruction_knowledge_item=compiled.instruction_knowledge_item,
                    instruction_query=compiled.instruction_query,
                    instruction_response=compiled.instruction_response,
                    always_on_knowledge=compiled.always_on_knowledge,
                )
        except RuntimeViolationError as e:
            self.logger.log(f"Runtime violation: {e}", header="EVAL")
            return EvalResult(score=0.0, logs=[f"Runtime violation: {e}"], runtime_violation=str(e))
        finally:
            toolkit.close()

    @weave.op()
    def evaluate_dual(
        self,
        program: KBProgram,
        train_data: list[DataItem],
        val_score: list[DataItem],
        val_reflect: list[DataItem],
    ) -> tuple[EvalResult, EvalResult]:
        """Single train ingestion, dual val evaluation.

        Returns (score_result, reflect_result) — score_result is for pool selection,
        reflect_result provides failed_cases for future reflection.
        """
        compile_result = compile_kb_program(program.source_code)
        if isinstance(compile_result, CompileError):
            self.logger.log(f"Compile failed: {compile_result.message}", header="EVAL")
            empty = EvalResult(
                score=0.0,
                logs=[f"Compile error: {compile_result.message} — {compile_result.details}"],
            )
            return empty, EvalResult(score=0.0)

        compiled = compile_result
        ki_schema = extract_dataclass_schema(compiled.ki_cls)
        query_schema = extract_dataclass_schema(compiled.query_cls)

        toolkit = Toolkit(self.toolkit_config)
        try:
            kb = compiled.kb_cls(toolkit)
        except Exception as e:
            empty = EvalResult(score=0.0, logs=[f"KnowledgeBase instantiation failed: {e}"])
            return empty, EvalResult(score=0.0)

        try:
            # Train ingestion (once)
            if train_data and train_data[0].raw_text:
                self.logger.log(
                    f"Pipeline: offline dual (train={len(train_data)}, "
                    f"val_score={len(val_score)}, val_reflect={len(val_reflect)})",
                    header="EVAL",
                )
                train_examples, train_logs = self._ingest_offline(
                    kb,
                    compiled.ki_cls,
                    ki_schema,
                    train_data,
                    instruction_knowledge_item=compiled.instruction_knowledge_item,
                )
            else:
                self.logger.log(
                    f"Pipeline: online dual (train={len(train_data)}, "
                    f"val_score={len(val_score)}, val_reflect={len(val_reflect)})",
                    header="EVAL",
                )
                train_logs: list[str] = []
                train_examples = self._online_train_batched(
                    kb,
                    compiled.ki_cls,
                    compiled.query_cls,
                    ki_schema,
                    query_schema,
                    train_data,
                    train_logs,
                    instruction_knowledge_item=compiled.instruction_knowledge_item,
                    instruction_query=compiled.instruction_query,
                    instruction_response=compiled.instruction_response,
                    always_on_knowledge=compiled.always_on_knowledge,
                )

            # Score val
            self.logger.log(f"Val (score): evaluating on {len(val_score)} items", header="EVAL")
            score_result = self._evaluate_val(
                kb,
                compiled.query_cls,
                query_schema,
                val_score,
                list(train_logs),
                toolkit,
                instruction_query=compiled.instruction_query,
                instruction_response=compiled.instruction_response,
                always_on_knowledge=compiled.always_on_knowledge,
            )
            score_result.train_examples = train_examples

            # Reflect val
            self.logger.log(f"Val (reflect): evaluating on {len(val_reflect)} items", header="EVAL")
            reflect_result = self._evaluate_val(
                kb,
                compiled.query_cls,
                query_schema,
                val_reflect,
                list(train_logs),
                toolkit,
                instruction_query=compiled.instruction_query,
                instruction_response=compiled.instruction_response,
                always_on_knowledge=compiled.always_on_knowledge,
            )
            reflect_result.train_examples = train_examples

            return score_result, reflect_result
        except RuntimeViolationError as e:
            self.logger.log(f"Runtime violation: {e}", header="EVAL")
            empty = EvalResult(score=0.0, logs=[f"Runtime violation: {e}"], runtime_violation=str(e))
            return empty, EvalResult(score=0.0, runtime_violation=str(e))
        finally:
            toolkit.close()

    # ── Offline ─────────────────────────────────────────────────────────────

    def _ingest_offline(
        self,
        kb: Any,
        ki_cls: type,
        ki_schema: str,
        train_data: list[DataItem],
        *,
        instruction_knowledge_item: str = "",
    ) -> tuple[list[TrainExample], list[str]]:
        """Batch ingest train data into KB. Returns (train_examples, logs)."""
        logs: list[str] = []
        self.logger.log(f"Train: generating knowledge items for {len(train_data)} items", header="EVAL")
        all_messages = [
            [
                {
                    "role": "user",
                    "content": build_knowledge_item_generation_prompt(
                        item.raw_text, ki_schema, instruction_knowledge_item
                    ),
                }
            ]
            for item in train_data
        ]
        responses = self._batch_llm_call(all_messages, json_mode=True, label="Train: KI generation")

        train_examples = []
        write_count = 0
        fail_count = 0
        for idx, (msgs, item, content) in enumerate(zip(all_messages, train_data, responses, strict=True)):
            if idx < 3 and content is not None:
                train_examples.append(TrainExample(messages=[*msgs, {"role": "assistant", "content": content}]))
            if content is None:
                logs.append(f"Failed to generate knowledge item for: {item.raw_text}")
                fail_count += 1
                continue
            try:
                ki = ki_cls(**_parse_json_from_llm(content))
                _guarded_write(kb, ki, raw_text=item.raw_text)
                write_count += 1
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Knowledge item parse/write failed: {e}")
                fail_count += 1

        self.logger.log(f"Train: write phase complete — {write_count} written, {fail_count} failed", header="EVAL")
        if fail_count > 0:
            self.logger.log(
                f"WARNING: {fail_count}/{fail_count + write_count} train items failed — KB partially populated",
                header="EVAL",
            )
        return train_examples, logs

    def _evaluate_offline(
        self,
        kb: Any,
        ki_cls: type,
        query_cls: type,
        ki_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
        *,
        instruction_knowledge_item: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> EvalResult:
        """Offline: Batch ingest train (LLM generates knowledge items), then evaluate val."""
        train_examples, logs = self._ingest_offline(
            kb, ki_cls, ki_schema, train_data, instruction_knowledge_item=instruction_knowledge_item
        )

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
            always_on_knowledge=always_on_knowledge,
        )
        result.train_examples = train_examples
        return result

    # ── Online ──────────────────────────────────────────────────────────────

    def _evaluate_online(
        self,
        kb: Any,
        ki_cls: type,
        query_cls: type,
        ki_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: Toolkit,
        *,
        instruction_knowledge_item: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> EvalResult:
        """Online: Interleaved multi-turn train, then evaluate val."""
        logs: list[str] = []
        self.logger.log(f"Train: interactive QA for {len(train_data)} items (3 rounds)", header="EVAL")
        train_examples = self._online_train_batched(
            kb,
            ki_cls,
            query_cls,
            ki_schema,
            query_schema,
            train_data,
            logs,
            instruction_knowledge_item=instruction_knowledge_item,
            instruction_query=instruction_query,
            instruction_response=instruction_response,
            always_on_knowledge=always_on_knowledge,
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
            always_on_knowledge=always_on_knowledge,
        )
        result.train_examples = train_examples
        return result

    def _online_train_batched(
        self,
        kb: Any,
        ki_cls: type,
        query_cls: type,
        ki_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        logs: list[str],
        *,
        instruction_knowledge_item: str = "",
        instruction_query: str = "",
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> list[TrainExample]:
        """Online train batched: 3 rounds of batch_completion, then serial writes."""
        if not train_data:
            return []
        fail_count = 0

        # Round 1: query generation for all items
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema, instruction_query)}]
            for item in train_data
        ]
        round1_responses = self._batch_llm_call(round1_messages, json_mode=True, label="Train: query generation")

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
            always_on_knowledge=always_on_knowledge,
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
        round2_responses = self._batch_llm_call(round2_messages, label="Train: answer generation")

        # Score answers for feedback; build context for round 3
        round3_items: list[tuple[DataItem, list[dict], str]] = []
        for (i, _s), r2_msgs, answer in zip(valid, round2_messages, round2_responses, strict=True):
            if answer is None:
                logs.append("Train answer generation failed (batch error)")
                fail_count += 1
                continue
            score = self.scorer(answer, train_data[i].expected_answer)
            evaluation_result = f"Score: {score:.1f} ({'correct' if score >= 1.0 else 'incorrect'})"
            msgs_so_far = r2_msgs + [{"role": "assistant", "content": answer}]
            round3_items.append((train_data[i], msgs_so_far, evaluation_result))

        # Round 3: knowledge item generation with feedback
        round3_messages = [
            msgs_so_far
            + [
                {
                    "role": "user",
                    "content": build_knowledge_item_with_feedback_prompt(
                        evaluation_result, item.expected_answer, ki_schema, instruction_knowledge_item
                    ),
                }
            ]
            for item, msgs_so_far, evaluation_result in round3_items
        ]
        round3_responses = self._batch_llm_call(round3_messages, json_mode=True, label="Train: KI with feedback")

        # Serial writes + capture train examples
        train_examples: list[TrainExample] = []
        for (item, _msgs, _eval), r3_msgs, ki_content in zip(
            round3_items, round3_messages, round3_responses, strict=True
        ):
            if ki_content is None:
                logs.append("Train knowledge item generation failed (batch error)")
                fail_count += 1
                continue
            if len(train_examples) < 3:
                train_examples.append(TrainExample(messages=[*r3_msgs, {"role": "assistant", "content": ki_content}]))
            try:
                ki = ki_cls(**_parse_json_from_llm(ki_content))
                _guarded_write(kb, ki, raw_text=item.raw_text)
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Train knowledge item parse/write failed: {e}")
                fail_count += 1

        if fail_count > 0:
            write_count = len(train_examples)
            self.logger.log(
                f"Train: write phase complete — {write_count} written, {fail_count} failed",
                header="EVAL",
            )
            self.logger.log(
                f"WARNING: {fail_count}/{fail_count + write_count} train items failed — KB partially populated",
                header="EVAL",
            )

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
        always_on_knowledge: str = "",
    ) -> list[_QuerySlot | None]:
        """Parse batch query responses, read knowledge base in parallel. Returns slots aligned with data."""
        import time as _time

        from rich.progress import BarColumn, MofNCompleteColumn, Progress, TimeElapsedColumn

        total = len(round1_messages)
        self.logger.log(f"{log_prefix}: parsing queries & reading KB for {total} items", header="DEBUG")

        # Phase 1: parse all queries (fast, no I/O)
        parsed: list[tuple[str, str, Any] | None] = []  # (query_prompt, query_json, query_obj) or None
        for msgs, content in zip(round1_messages, responses, strict=True):
            query_prompt = msgs[0]["content"]
            if content is None:
                logs.append(f"{log_prefix} query generation failed (batch error)")
                parsed.append(None)
                continue
            try:
                query = query_cls(**_parse_json_from_llm(content))
                parsed.append((query_prompt, content, query))
            except Exception as e:
                logs.append(f"{log_prefix} query parse failed: {e}")
                parsed.append(None)

        # Phase 2: parallel KB reads for parsed queries
        read_items = [(i, p) for i, p in enumerate(parsed) if p is not None]
        if not read_items:
            return [None] * total

        def _do_read(query: Any) -> str:
            """Execute a single KB read with timeout, return retrieved_str."""
            if hasattr(kb, "toolkit"):
                kb.toolkit.reset_llm_budget()
            t0 = _time.monotonic()
            # Call kb.read directly (we're already in a pool thread)
            result = kb.read(query)
            elapsed = _time.monotonic() - t0
            if elapsed > 10.0:
                self.logger.log(f"Slow kb.read(): {elapsed:.1f}s", header="DEBUG")
            result_str = str(result) if result is not None else ""
            if len(result_str) > MEMORY_READ_MAX_CHARS:
                raise RuntimeViolationError(
                    f"kb.read() returned {len(result_str)} chars (limit: {MEMORY_READ_MAX_CHARS})"
                )
            return result_str

        read_pool = concurrent.futures.ThreadPoolExecutor(max_workers=_BATCH_POOL_SIZE)
        read_futures = {read_pool.submit(_do_read, p[2]): i for i, p in read_items}
        # read_results[original_index] = retrieved_str or Exception
        read_results: dict[int, str | Exception] = {}
        runtime_violation: RuntimeViolationError | None = None

        self.logger.log(
            f"{log_prefix}: submitted {len(read_futures)} KB reads (pool_size={_BATCH_POOL_SIZE})",
            header="DEBUG",
        )

        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.logger.console,
            transient=True,
        ) as progress:
            ptask = progress.add_task(f"{log_prefix}: KB read", total=len(read_futures))
            for fut in concurrent.futures.as_completed(read_futures):
                idx = read_futures[fut]
                try:
                    read_results[idx] = fut.result()
                except RuntimeViolationError as e:
                    runtime_violation = e
                    # Cancel remaining futures
                    for f in read_futures:
                        f.cancel()
                    break
                except Exception as e:
                    read_results[idx] = e
                progress.advance(ptask)

        read_pool.shutdown(wait=False)

        if runtime_violation is not None:
            raise runtime_violation

        # Phase 3: assemble slots
        slots: list[_QuerySlot | None] = []
        for i in range(total):
            p = parsed[i]
            if p is None:
                slots.append(None)
                continue
            query_prompt, query_json, query = p
            result = read_results.get(i)
            if isinstance(result, Exception):
                self.logger.log(f"{log_prefix} kb.read() raised {type(result).__name__}: {result}", header="EVAL")
                retrieved_str = f"Read error: {result}"
                logs.append(f"{log_prefix} read failed: {result}")
            elif result is None:
                # Future was cancelled due to runtime violation of another read
                retrieved_str = "Read cancelled"
                logs.append(f"{log_prefix} read cancelled")
            else:
                retrieved_str = result
            retrieved_prompt = build_retrieved_memory_prompt(
                retrieved_str, instruction_response, always_on_knowledge=always_on_knowledge
            )
            slots.append(_QuerySlot(query, query_json, retrieved_str, query_prompt, retrieved_prompt))
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
        always_on_knowledge: str = "",
    ) -> EvalResult:
        """Two-phase val: (1) shared KB retrieval, (2) pluggable scoring."""
        if not val_data:
            return self._build_eval_result([], [], [], [], logs)

        # Phase 1: shared KB retrieval
        slots = self._retrieve_for_val(
            kb,
            query_cls,
            query_schema,
            val_data,
            logs,
            instruction_query=instruction_query,
            instruction_response=instruction_response,
            always_on_knowledge=always_on_knowledge,
        )

        # Phase 2: pluggable scoring
        if self.val_scorer:
            result = self._val_scorer_path(
                slots,
                val_data,
                logs,
                toolkit,
                instruction_response=instruction_response,
                always_on_knowledge=always_on_knowledge,
            )
        else:
            result = self._default_answer_and_score(
                slots,
                val_data,
                logs,
                toolkit,
                instruction_response=instruction_response,
                always_on_knowledge=always_on_knowledge,
            )

        self.logger.log(
            f"Val: complete — score={result.score:.3f}, {len(result.failed_cases)}/{len(val_data)} failed",
            header="EVAL",
        )
        return result

    def _retrieve_for_val(
        self,
        kb: Any,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        *,
        instruction_query: str = "",
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> list[_QuerySlot | None]:
        """Phase 1 of val: batch query generation + serial KB reads."""
        round1_messages = [
            [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema, instruction_query)}]
            for item in val_data
        ]
        round1_responses = self._batch_llm_call(round1_messages, json_mode=True, label="Val: query generation")
        return self._parse_queries_and_read(
            query_cls,
            kb,
            round1_messages,
            round1_responses,
            logs,
            log_prefix="Val",
            instruction_query=instruction_query,
            instruction_response=instruction_response,
            always_on_knowledge=always_on_knowledge,
        )

    def _default_answer_and_score(
        self,
        slots: list[_QuerySlot | None],
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
        *,
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> EvalResult:
        """Phase 2 default: batch LLM answer generation + scorer."""
        valid = [(i, s) for i, s in enumerate(slots) if s is not None]
        round2_messages = [
            [
                {"role": "user", "content": s.query_prompt},
                {"role": "assistant", "content": s.query_json},
                {"role": "user", "content": s.retrieved_prompt},
            ]
            for _i, s in valid
        ]
        round2_responses = self._batch_llm_call(round2_messages, label="Val: answer generation")

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
                conv = [
                    {"role": "user", "content": slot.query_prompt},
                    {"role": "assistant", "content": slot.query_json},
                    {"role": "user", "content": slot.retrieved_prompt},
                ]
                scores.append(0.0)
                outputs.append("")
                failed_cases.append(
                    FailedCase(
                        question=item.question,
                        output="",
                        expected=item.expected_answer,
                        score=0.0,
                        conversation_history=conv,
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

        return self._build_eval_result(scores, outputs, failed_cases, success_cases, logs)

    def _val_scorer_path(
        self,
        slots: list[_QuerySlot | None],
        val_data: list[DataItem],
        logs: list[str],
        toolkit: Toolkit,
        *,
        instruction_response: str = "",
        always_on_knowledge: str = "",
    ) -> EvalResult:
        """Phase 2 custom: delegate to val_scorer.score_batch."""
        retrieved = [s.retrieved_str if s is not None else "" for s in slots]

        results = self.val_scorer.score_batch(
            val_data,
            retrieved,
            self.task_model,
            instruction_response,
            always_on_knowledge,
            reasoning_effort=self.reasoning_effort,
        )

        scores: list[float] = []
        outputs: list[str] = []
        failed_cases: list[FailedCase] = []
        success_cases: list[FailedCase] = []
        log_snapshot = list(toolkit.logger.logs)

        for i, (output, score) in enumerate(results):
            scores.append(score)
            outputs.append(output)
            # Include retrieval conversation so reflection LLM can diagnose
            # whether failures stem from poor KB retrieval or poor execution.
            slot = slots[i]
            conv = (
                [
                    {"role": "user", "content": slot.query_prompt},
                    {"role": "assistant", "content": slot.query_json},
                    {"role": "user", "content": slot.retrieved_prompt},
                ]
                if slot is not None
                else []
            )
            case = FailedCase(
                question=val_data[i].question,
                output=output,
                expected=val_data[i].expected_answer,
                score=score,
                conversation_history=conv,
                memory_logs=log_snapshot,
            )
            if score < 1.0:
                failed_cases.append(case)
            else:
                success_cases.append(case)

        return self._build_eval_result(scores, outputs, failed_cases, success_cases, logs)

    def _batch_llm_call(
        self, all_messages: list[list[dict]], *, json_mode: bool = False, label: str = "LLM batch"
    ) -> list[str | None]:
        """Fan out independent LLM calls using a shared thread pool.

        Uses the module-level ``_BATCH_POOL`` instead of ``litellm.batch_completion``
        to avoid the file-descriptor leak caused by short-lived ThreadPoolExecutor
        threads each opening (and never closing) a thread-local SQLite connection
        to the litellm disk cache.

        Returns a list of content strings (same length as all_messages).
        Failed entries are None (error already logged).
        """
        if not all_messages:
            return []
        import time as _time

        extra: dict = {}
        if json_mode:
            extra["response_format"] = {"type": "json_object"}
        if self.reasoning_effort is not None:
            extra["reasoning_effort"] = self.reasoning_effort

        pool = _get_batch_pool()
        self.logger.log(
            f"{label}: submitting {len(all_messages)} calls (pool_size={_BATCH_POOL_SIZE})",
            header="DEBUG",
        )
        t_submit = _time.monotonic()
        fail_count = 0
        futures = [
            pool.submit(
                litellm.completion,
                model=self.task_model,
                messages=[{"role": "system", "content": " "}, *msgs],
                caching=True,
                **extra,
            )
            for msgs in all_messages
        ]
        self.logger.log(
            f"{label}: all {len(futures)} futures submitted in {_time.monotonic() - t_submit:.1f}s",
            header="DEBUG",
        )

        results: list[str | None] = [None] * len(futures)
        from rich.progress import BarColumn, MofNCompleteColumn, Progress, TimeElapsedColumn

        stall_check_interval = 120.0  # log warning if no progress for 2 min
        with Progress(
            "[progress.description]{task.description}",
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=self.logger.console,
            transient=True,
        ) as progress:
            task = progress.add_task(label, total=len(futures))
            completed = 0
            last_progress_time = _time.monotonic()

            # Stall-detection thread: logs warnings when no futures complete for a while
            import threading

            stall_stop = threading.Event()

            def _stall_watcher() -> None:
                while not stall_stop.wait(stall_check_interval):
                    elapsed = _time.monotonic() - last_progress_time
                    if elapsed >= stall_check_interval:
                        pending = [i for i, f in enumerate(futures) if not f.done()]
                        self.logger.log(
                            f"{label}: STALL — no progress for {elapsed:.0f}s, "
                            f"{completed}/{len(futures)} done, pending indices: {pending[:10]}"
                            f"{'...' if len(pending) > 10 else ''}",
                            header="DEBUG",
                        )

            watcher = threading.Thread(target=_stall_watcher, daemon=True)
            watcher.start()

            try:
                for future in concurrent.futures.as_completed(futures):
                    idx = futures.index(future)
                    try:
                        resp = future.result()
                        results[idx] = resp.choices[0].message.content
                    except Exception as exc:
                        self.logger.log(f"Batch LLM call failed (idx={idx}): {exc}", header="EVAL")
                        fail_count += 1
                    completed += 1
                    last_progress_time = _time.monotonic()
                    progress.advance(task)
            finally:
                stall_stop.set()
                watcher.join(timeout=1.0)
        if fail_count > 0:
            self.logger.log(f"{label}: {fail_count}/{len(futures)} calls failed", header="EVAL")
        self.logger.log(
            f"{label}: all {len(futures)} calls completed in {_time.monotonic() - t_submit:.1f}s",
            header="DEBUG",
        )
        return results
