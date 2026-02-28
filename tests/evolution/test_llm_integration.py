"""LLM integration tests — verify prompt → LLM → parse end-to-end with real Deepseek V3.2.

Uses litellm disk cache so repeated runs don't incur API costs.
Snapshots are human-readable via syrupy and committed to git.
"""

from __future__ import annotations

import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import MemoryEvaluator, _llm_call, _parse_json_from_llm
from programmaticmemory.evolution.prompts import (
    INITIAL_MEMORY_PROGRAM,
    MEMORY_INTERFACE_SPEC,
    REFLECTION_SYSTEM_PROMPT,
    build_observation_generation_prompt,
    build_observation_with_feedback_prompt,
    build_query_generation_prompt,
    build_reflection_user_prompt,
    build_response_prompt,
    build_retrieved_memory_prompt,
)
from programmaticmemory.evolution.reflector import _extract_code_block
from programmaticmemory.evolution.sandbox import compile_memory_program, extract_dataclass_schema
from programmaticmemory.evolution.types import DataItem, MemoryProgram

MODEL = "openrouter/deepseek/deepseek-v3.2"


def _get_obs_query_schema() -> tuple[str, str]:
    """Compile INITIAL_MEMORY_PROGRAM and return (obs_schema, query_schema)."""
    result = compile_memory_program(INITIAL_MEMORY_PROGRAM)
    assert not isinstance(result, Exception)
    obs_cls, query_cls, _ = result
    return extract_dataclass_schema(obs_cls), extract_dataclass_schema(query_cls)


# ---------------------------------------------------------------------------
# 3a. Query Generation (read path)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_query_generation(snapshot: SnapshotAssertion):
    """LLM generates a valid Query JSON from a natural-language question."""
    _, query_schema = _get_obs_query_schema()
    prompt = build_query_generation_prompt("What is the capital of France?", query_schema)

    raw_output = _llm_call(MODEL, [{"role": "user", "content": prompt}])

    # Parse must succeed and match Query dataclass fields
    parsed = _parse_json_from_llm(raw_output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    assert len(parsed["raw"]) > 0

    assert raw_output == snapshot


# ---------------------------------------------------------------------------
# 3b. Observation Generation (write — Type A standalone)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_observation_generation(snapshot: SnapshotAssertion):
    """LLM generates a valid Observation JSON from raw text."""
    obs_schema, _ = _get_obs_query_schema()
    prompt = build_observation_generation_prompt("The capital of France is Paris.", obs_schema)

    raw_output = _llm_call(MODEL, [{"role": "user", "content": prompt}])

    parsed = _parse_json_from_llm(raw_output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    assert len(parsed["raw"]) > 0

    assert raw_output == snapshot


# ---------------------------------------------------------------------------
# 3c. Observation with Feedback (write — Type B with feedback)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_observation_with_feedback(snapshot: SnapshotAssertion):
    """LLM generates an Observation informed by evaluation feedback."""
    obs_schema, _ = _get_obs_query_schema()
    prompt = build_observation_with_feedback_prompt("Score: 0.0 (incorrect)", "Paris", obs_schema)

    raw_output = _llm_call(MODEL, [{"role": "user", "content": prompt}])

    parsed = _parse_json_from_llm(raw_output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    # The observation should contain Paris-related information
    assert "paris" in parsed["raw"].lower() or "paris" in raw_output.lower()

    assert raw_output == snapshot


# ---------------------------------------------------------------------------
# 3d. Retrieved Memory Answer (read → answer)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_retrieved_memory_answer(snapshot: SnapshotAssertion):
    """LLM answers a question using retrieved memory in a multi-turn conversation."""
    _, query_schema = _get_obs_query_schema()

    # Build Step 1 messages (query generation)
    query_prompt = build_query_generation_prompt("What is the capital of France?", query_schema)
    query_json = _llm_call(MODEL, [{"role": "user", "content": query_prompt}])

    # Build Step 2 messages (answer from retrieved memory)
    messages = [
        {"role": "user", "content": query_prompt},
        {"role": "assistant", "content": query_json},
        {"role": "user", "content": build_retrieved_memory_prompt("The capital of France is Paris.")},
    ]
    answer = _llm_call(MODEL, messages)

    assert "paris" in answer.lower()

    assert answer == snapshot


# ---------------------------------------------------------------------------
# 3e. Reflection (diagnose + produce improved code)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_reflection(snapshot: SnapshotAssertion):
    """LLM reflects on failed cases and produces compilable improved code."""
    failed_cases = [
        {
            "question": "What is the capital of France?",
            "expected": "Paris",
            "output": "I don't know",
            "score": 0.0,
            "conversation_history": [
                {"role": "user", "content": "What is the capital of France?"},
                {"role": "assistant", "content": "I don't know"},
            ],
            "memory_logs": ["Query: what is the capital of france, store size: 0"],
        }
    ]

    system_prompt = REFLECTION_SYSTEM_PROMPT.format(interface_spec=MEMORY_INTERFACE_SPEC)
    user_prompt = build_reflection_user_prompt(
        code=INITIAL_MEMORY_PROGRAM,
        score=0.3,
        failed_cases=failed_cases,
        iteration=1,
    )

    raw_output = _llm_call(
        MODEL,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    # Must extract a Python code block
    code = _extract_code_block(raw_output)
    assert code is not None, "Failed to extract code block from reflection output"

    # Code must compile and define all three classes
    compile_result = compile_memory_program(code)
    assert isinstance(compile_result, tuple) is not False, f"Compile failed: {compile_result}"
    obs_cls, query_cls, memory_cls = compile_result
    assert obs_cls.__name__ == "Observation"
    assert query_cls.__name__ == "Query"
    assert memory_cls.__name__ == "Memory"

    assert raw_output == snapshot


# ---------------------------------------------------------------------------
# 3f. Response Generation (standalone answer)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_response_generation(snapshot: SnapshotAssertion):
    """LLM answers a question from retrieved information (standalone, non-conversational)."""
    prompt = build_response_prompt(
        "What is the capital of France?",
        "The capital of France is Paris.",
    )

    answer = _llm_call(MODEL, [{"role": "user", "content": prompt}])

    assert "paris" in answer.lower()
    assert len(answer) < 500  # Should be concise

    assert answer == snapshot


# ---------------------------------------------------------------------------
# 3g. End-to-End Type A Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_end_to_end_type_a(snapshot: SnapshotAssertion):
    """Full Type A pipeline: ingest → query → answer → score with real LLM."""
    program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
    train_data = [
        DataItem(
            raw_text="The capital of France is Paris.",
            question="What is the capital of France?",
            expected_answer="Paris",
        ),
    ]
    val_data = list(train_data)  # Same data for simplicity

    evaluator = MemoryEvaluator(task_model=MODEL)
    result = evaluator.evaluate(program, train_data, val_data, dataset_type="A")

    assert result.score > 0, f"Expected positive score, got {result.score}"
    assert len(result.per_case_outputs) > 0
    assert len(result.per_case_outputs[0]) > 0  # Non-empty answer

    # Snapshot key fields (not the full object, which includes non-deterministic logs)
    snapshot_data = {
        "score": result.score,
        "num_outputs": len(result.per_case_outputs),
        "first_output": result.per_case_outputs[0] if result.per_case_outputs else "",
        "num_failed": len(result.failed_cases),
    }
    assert snapshot_data == snapshot
