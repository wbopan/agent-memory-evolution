"""LLM integration tests — verify prompt → LLM → parse end-to-end with real Deepseek V3.2.

Uses litellm disk cache so repeated runs don't incur API costs.
Snapshots capture both prompts and outputs for human review.
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
from programmaticmemory.evolution.toolkit import ToolkitConfig, create_toolkit
from programmaticmemory.evolution.types import DataItem, MemoryProgram

MODEL = "openrouter/deepseek/deepseek-v3.2"


def _get_obs_query_schema() -> tuple[str, str]:
    """Compile INITIAL_MEMORY_PROGRAM and return (obs_schema, query_schema)."""
    result = compile_memory_program(INITIAL_MEMORY_PROGRAM)
    assert not isinstance(result, Exception)
    obs_cls, query_cls, _ = result
    return extract_dataclass_schema(obs_cls), extract_dataclass_schema(query_cls)


# ---------------------------------------------------------------------------
# Toolkit LLM completion
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_toolkit_llm_completion(snapshot: SnapshotAssertion):
    """Toolkit.llm_completion() calls real LLM and returns a string answer."""
    config = ToolkitConfig(llm_model=MODEL, llm_call_budget=5)
    tk = create_toolkit(config)

    messages = [{"role": "user", "content": "What is 2 + 3? Answer with just the number."}]
    output = tk.llm_completion(messages)

    assert isinstance(output, str)
    assert "5" in output
    assert tk._llm_calls_used == 1

    tk.close()

    assert {"prompt": messages[0]["content"], "output": output} == snapshot


# ---------------------------------------------------------------------------
# 3a. Query Generation (read path)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_query_generation(snapshot: SnapshotAssertion):
    """LLM generates a valid Query JSON from a natural-language question."""
    _, query_schema = _get_obs_query_schema()
    prompt = build_query_generation_prompt("What is the capital of France?", query_schema)
    messages = [{"role": "user", "content": prompt}]

    output = _llm_call(MODEL, messages)

    parsed = _parse_json_from_llm(output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    assert len(parsed["raw"]) > 0

    assert {"prompt": prompt, "output": output} == snapshot


# ---------------------------------------------------------------------------
# 3b. Observation Generation (write — Type A standalone)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_observation_generation(snapshot: SnapshotAssertion):
    """LLM generates a valid Observation JSON from raw text."""
    obs_schema, _ = _get_obs_query_schema()
    prompt = build_observation_generation_prompt("The capital of France is Paris.", obs_schema)
    messages = [{"role": "user", "content": prompt}]

    output = _llm_call(MODEL, messages)

    parsed = _parse_json_from_llm(output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    assert len(parsed["raw"]) > 0

    assert {"prompt": prompt, "output": output} == snapshot


# ---------------------------------------------------------------------------
# 3c. Observation with Feedback (write — Type B with feedback)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_observation_with_feedback(snapshot: SnapshotAssertion):
    """LLM generates an Observation informed by evaluation feedback."""
    obs_schema, _ = _get_obs_query_schema()
    prompt = build_observation_with_feedback_prompt("Score: 0.0 (incorrect)", "Paris", obs_schema)
    messages = [{"role": "user", "content": prompt}]

    output = _llm_call(MODEL, messages)

    parsed = _parse_json_from_llm(output)
    assert "raw" in parsed
    assert isinstance(parsed["raw"], str)
    assert "paris" in parsed["raw"].lower() or "paris" in output.lower()

    assert {"prompt": prompt, "output": output} == snapshot


# ---------------------------------------------------------------------------
# 3d. Retrieved Memory Answer (multi-turn: query → answer)
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_retrieved_memory_answer(snapshot: SnapshotAssertion):
    """LLM answers a question using retrieved memory in a multi-turn conversation."""
    _, query_schema = _get_obs_query_schema()

    # Step 1: query generation
    step1_prompt = build_query_generation_prompt("What is the capital of France?", query_schema)
    step1_output = _llm_call(MODEL, [{"role": "user", "content": step1_prompt}])

    # Step 2: answer from retrieved memory
    step2_prompt = build_retrieved_memory_prompt("The capital of France is Paris.")
    messages = [
        {"role": "user", "content": step1_prompt},
        {"role": "assistant", "content": step1_output},
        {"role": "user", "content": step2_prompt},
    ]
    step2_output = _llm_call(MODEL, messages)

    assert "paris" in step2_output.lower()

    assert {
        "step1_prompt": step1_prompt,
        "step1_output": step1_output,
        "step2_prompt": step2_prompt,
        "step2_output": step2_output,
    } == snapshot


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

    output = _llm_call(
        MODEL,
        [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    )

    # Must extract a Python code block
    code = _extract_code_block(output)
    assert code is not None, "Failed to extract code block from reflection output"

    # Code must compile and define all three classes
    compile_result = compile_memory_program(code)
    assert isinstance(compile_result, tuple), f"Compile failed: {compile_result}"
    obs_cls, query_cls, memory_cls = compile_result
    assert obs_cls.__name__ == "Observation"
    assert query_cls.__name__ == "Query"
    assert memory_cls.__name__ == "Memory"

    assert {
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "output": output,
    } == snapshot


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

    output = _llm_call(MODEL, [{"role": "user", "content": prompt}])

    assert "paris" in output.lower()
    assert len(output) < 500

    assert {"prompt": prompt, "output": output} == snapshot


# ---------------------------------------------------------------------------
# 3g. End-to-End Type A Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_end_to_end_type_a(snapshot: SnapshotAssertion):
    """Full Type A pipeline: ingest → query → answer → score with real LLM.

    Prompts are generated internally by MemoryEvaluator; this test snapshots
    the evaluation results only.
    """
    program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
    train_data = [
        DataItem(
            raw_text="The capital of France is Paris.",
            question="What is the capital of France?",
            expected_answer="Paris",
        ),
    ]
    val_data = list(train_data)

    evaluator = MemoryEvaluator(task_model=MODEL)
    result = evaluator.evaluate(program, train_data, val_data, dataset_type="A")

    assert result.score > 0, f"Expected positive score, got {result.score}"
    assert len(result.per_case_outputs) > 0
    assert len(result.per_case_outputs[0]) > 0

    snapshot_data = {
        "score": result.score,
        "num_outputs": len(result.per_case_outputs),
        "first_output": result.per_case_outputs[0] if result.per_case_outputs else "",
        "num_failed": len(result.failed_cases),
    }
    assert snapshot_data == snapshot


# ---------------------------------------------------------------------------
# 3h. End-to-End Type B Pipeline (multi-turn train with feedback)
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_end_to_end_type_b(snapshot: SnapshotAssertion):
    """Full Type B pipeline: interleaved train with feedback → val.

    Train has 2 samples where the expected answers are ALL-CAPS (APPLE, GREEN),
    but the questions don't hint at this format. The model must learn the
    output convention from feedback during training.

    Val has 1 sample that requires combining knowledge from both train items
    (favorite fruit + favorite color → green apple).
    """
    program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
    train_data = [
        DataItem(
            raw_text="Alice was asked about her favorite fruit. She loves apples over bananas.",
            question="Does Alice prefer apples or bananas?",
            expected_answer="APPLE",
        ),
        DataItem(
            raw_text="Alice was asked about her favorite color. She said green is her favorite.",
            question="What is Alice's favorite color?",
            expected_answer="GREEN",
        ),
    ]
    val_data = [
        DataItem(
            raw_text="",  # not used in val
            question=(
                "Given what Alice likes, which would she pick: "
                "a green apple, a red apple, a green banana, or a dragon fruit?"
            ),
            expected_answer="green apple",
        ),
    ]

    evaluator = MemoryEvaluator(task_model=MODEL)
    result = evaluator.evaluate(program, train_data, val_data, dataset_type="B")

    assert len(result.per_case_outputs) == 1
    assert len(result.per_case_outputs[0]) > 0  # non-empty answer

    snapshot_data = {
        "score": result.score,
        "num_outputs": len(result.per_case_outputs),
        "val_output": result.per_case_outputs[0] if result.per_case_outputs else "",
        "num_failed": len(result.failed_cases),
        "logs": result.logs,
    }
    assert snapshot_data == snapshot
