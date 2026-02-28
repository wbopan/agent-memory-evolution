"""Evaluator — Type A and Type B evaluation pipelines for Memory Programs.

Both pipelines use multi-turn conversations where messages accumulate across steps,
matching the design document's specified interaction pattern.
"""

from __future__ import annotations

import json
import re
from typing import Literal, Protocol

import litellm

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
from programmaticmemory.evolution.toolkit import ToolkitConfig, create_toolkit
from programmaticmemory.evolution.types import DataItem, EvalResult, FailedCase, MemoryProgram
from programmaticmemory.logging.logger import get_logger


class Scorer(Protocol):
    def __call__(self, output: str, expected: str) -> float: ...


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


class LLMJudgeScorer:
    """LLM-as-judge scorer, returns 0.0 or 1.0."""

    def __init__(self, model: str = "openai/gpt-4o-mini") -> None:
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
    """Evaluates a MemoryProgram on a dataset using Type A or Type B pipeline.

    Both pipelines use multi-turn conversations where messages accumulate
    across steps within each sample, as specified in the design document.
    """

    def __init__(
        self,
        scorer: Scorer | None = None,
        task_model: str = "openai/gpt-4o-mini",
        toolkit_config: ToolkitConfig | None = None,
    ) -> None:
        self.scorer = scorer or ExactMatchScorer()
        self.task_model = task_model
        self.toolkit_config = toolkit_config
        self.logger = get_logger()

    def evaluate(
        self,
        program: MemoryProgram,
        train_data: list[DataItem],
        val_data: list[DataItem],
        dataset_type: Literal["A", "B"] = "A",
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

        toolkit = create_toolkit(self.toolkit_config)
        try:
            memory = memory_cls(toolkit)
        except Exception as e:
            return EvalResult(score=0.0, logs=[f"Memory instantiation failed: {e}"])

        try:
            if dataset_type == "A":
                return self._evaluate_type_a(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
            else:
                return self._evaluate_type_b(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
        finally:
            toolkit.close()

    # ── Type A ──────────────────────────────────────────────────────────────

    def _evaluate_type_a(
        self,
        memory: object,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: object,
    ) -> EvalResult:
        """Type A: Batch ingest train (LLM generates observations), then evaluate val."""
        logs: list[str] = []

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

    # ── Type B ──────────────────────────────────────────────────────────────

    def _evaluate_type_b(
        self,
        memory: object,
        obs_cls: type,
        query_cls: type,
        obs_schema: str,
        query_schema: str,
        train_data: list[DataItem],
        val_data: list[DataItem],
        toolkit: object,
    ) -> EvalResult:
        """Type B: Interleaved multi-turn train, then evaluate val.

        Each train sample is a multi-turn conversation:
          Step 1: generate query (user → assistant)
          Step 2: read memory + answer (user → assistant)
          Step 3: generate observation with feedback (user → assistant)
          Step 4: write observation to memory
        """
        logs: list[str] = []

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

        # Val: multi-turn query → read → answer → score (no writes)
        return self._evaluate_val(memory, query_cls, query_schema, val_data, logs, toolkit)

    # ── Shared validation ───────────────────────────────────────────────────

    def _evaluate_val(
        self,
        memory: object,
        query_cls: type,
        query_schema: str,
        val_data: list[DataItem],
        logs: list[str],
        toolkit: object,
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

    # ── Standalone helpers (Type A only) ────────────────────────────────────

    def _generate_observation_standalone(self, raw_text: str, obs_cls: type, obs_schema: str) -> object | None:
        """Generate an Observation from raw text via a single LLM call.

        Used only for Type A train (batch ingest). Type B uses the multi-turn flow.
        """
        prompt = build_observation_generation_prompt(raw_text, obs_schema)
        try:
            response = _llm_call(self.task_model, [{"role": "user", "content": prompt}])
            data = _parse_json_from_llm(response)
            return obs_cls(**data)
        except Exception as e:
            self.logger.log(f"Observation generation failed: {e}", header="EVAL")
            return None
