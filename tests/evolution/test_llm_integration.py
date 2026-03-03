"""LLM integration tests — verify prompt → LLM → parse end-to-end with real Deepseek V3.2.

Uses litellm disk cache so repeated runs don't incur API costs.
Snapshots capture both prompts and outputs for human review.
"""

from __future__ import annotations

import dataclasses

import litellm
import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import MEMORY_READ_MAX_CHARS, MemoryEvaluator, _parse_json_from_llm
from programmaticmemory.evolution.patcher import apply_patch
from programmaticmemory.evolution.prompts import (
    INITIAL_KB_PROGRAM,
    build_compile_fix_prompt,
    build_observation_generation_prompt,
    build_observation_with_feedback_prompt,
    build_query_generation_prompt,
    build_reflection_user_prompt,
    build_retrieved_memory_prompt,
)
from programmaticmemory.evolution.reflector import Reflector, _extract_patch
from programmaticmemory.evolution.sandbox import (
    CompiledProgram,
    CompileError,
    compile_kb_program,
    extract_dataclass_schema,
)
from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig
from programmaticmemory.evolution.types import DataItem, KBProgram

MODEL = "openrouter/deepseek/deepseek-v3.2"


def _llm_call(model: str, messages: list[dict], temperature: float = 0.0) -> str:
    """Task agent LLM call for integration tests."""
    response = litellm.completion(model=model, messages=messages, temperature=temperature, caching=True)
    return response.choices[0].message.content


def _get_obs_query_schema() -> tuple[str, str]:
    """Compile INITIAL_KB_PROGRAM and return (obs_schema, query_schema)."""
    result = compile_kb_program(INITIAL_KB_PROGRAM)
    assert isinstance(result, CompiledProgram)
    return extract_dataclass_schema(result.obs_cls), extract_dataclass_schema(result.query_cls)


# ---------------------------------------------------------------------------
# Toolkit LLM completion
# ---------------------------------------------------------------------------


@pytest.mark.llm
def test_toolkit_llm_completion(snapshot: SnapshotAssertion):
    """Toolkit.llm_completion() calls real LLM and returns a string answer."""
    config = ToolkitConfig(llm_model=MODEL, llm_call_budget=5)
    tk = Toolkit(config)

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
# 3b. Observation Generation (write — offline standalone)
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
# 3c. Observation with Feedback (write — online with feedback)
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

    user_prompt = build_reflection_user_prompt(
        code=INITIAL_KB_PROGRAM,
        score=0.3,
        failed_cases=failed_cases,
        iteration=1,
    )

    output = _llm_call(
        MODEL,
        [
            {"role": "user", "content": user_prompt},
        ],
    )

    # Must extract a patch and apply it
    patch = _extract_patch(output)
    assert patch is not None, "Failed to extract patch from reflection output"
    patched_code = apply_patch(INITIAL_KB_PROGRAM, patch)

    # Patched code must compile and define all three classes
    compile_result = compile_kb_program(patched_code)
    assert isinstance(compile_result, CompiledProgram), f"Compile failed: {compile_result}"
    assert compile_result.obs_cls.__name__ == "Observation"
    assert compile_result.query_cls.__name__ == "Query"
    assert compile_result.kb_cls.__name__ == "KnowledgeBase"

    assert {
        "prompt": user_prompt,
        "output": output,
    } == snapshot


# ---------------------------------------------------------------------------
# 3f. End-to-End Offline Pipeline
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_end_to_end_offline(snapshot: SnapshotAssertion):
    """Full offline pipeline: ingest → query → answer → score with real LLM.

    Prompts are generated internally by MemoryEvaluator; this test snapshots
    the evaluation results only.
    """
    program = KBProgram(source_code=INITIAL_KB_PROGRAM)
    train_data = [
        DataItem(
            raw_text="The capital of France is Paris.",
            question="What is the capital of France?",
            expected_answer="Paris",
        ),
    ]
    val_data = list(train_data)

    evaluator = MemoryEvaluator(task_model=MODEL, toolkit_config=ToolkitConfig(llm_model=MODEL))
    result = evaluator.evaluate(program, train_data, val_data)

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
# 3h. End-to-End Online Pipeline (multi-turn train with feedback)
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_end_to_end_online(snapshot: SnapshotAssertion):
    """Full online pipeline: interleaved train with feedback → val.

    Train has 2 samples where the expected answers are ALL-CAPS (APPLE, GREEN),
    but the questions don't hint at this format. The model must learn the
    output convention from feedback during training.

    Val has 1 sample that requires combining knowledge from both train items
    (favorite fruit + favorite color → green apple).
    """
    program = KBProgram(source_code=INITIAL_KB_PROGRAM)
    train_data = [
        DataItem(
            raw_text="",
            question="Does Alice prefer apples or bananas?",
            expected_answer="APPLE",
        ),
        DataItem(
            raw_text="",
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

    evaluator = MemoryEvaluator(task_model=MODEL, toolkit_config=ToolkitConfig(llm_model=MODEL))
    result = evaluator.evaluate(program, train_data, val_data)

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


# ---------------------------------------------------------------------------
# 3i. Reflection Recovery (broken program → reflect → working program)
# ---------------------------------------------------------------------------

BROKEN_KB_PROGRAM = '''\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""


@dataclass
class Observation:
    """Raw text observation to store in memory."""
    raw: str


@dataclass
class Query:
    """Raw text query to retrieve from memory."""
    raw: str


class KnowledgeBase:
    """KnowledgeBase with broken read — always reports empty."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store: list[str] = []

    def write(self, observation: Observation) -> None:
        self.store.append(observation.raw)
        self.toolkit.logger.log(f"Stored: {observation.raw[:80]}")

    def read(self, query: Query) -> str:
        self.toolkit.logger.log(f"Query: {query.raw[:80]}, store size: {len(self.store)}")
        return "No information stored."
'''


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_reflection_recovery(snapshot: SnapshotAssertion):
    """Broken program → reflect → working program.

    Starts with a program whose read() always returns empty (ignoring stored data),
    evaluates it (score 0), reflects on the failure, and verifies the reflected
    program scores higher.
    """
    program = KBProgram(source_code=BROKEN_KB_PROGRAM)
    train_data = [
        DataItem(
            raw_text="",
            question="What is Project Zephyr's access code?",
            expected_answer="DELTA-7742",
        ),
    ]
    val_data = list(train_data)

    evaluator = MemoryEvaluator(task_model=MODEL, toolkit_config=ToolkitConfig(llm_model=MODEL))

    # Round 1: broken program should score 0
    result1 = evaluator.evaluate(program, train_data, val_data)

    # Reflect on failures
    reflector = Reflector(model=MODEL, temperature=0.0)
    child = reflector.reflect_and_mutate(program, result1, iteration=1)
    assert child is not None, "Reflection failed to produce code"

    # Round 2: reflected program should improve
    result2 = evaluator.evaluate(child, train_data, val_data)
    assert result2.score > result1.score, (
        f"Expected improvement: round1={result1.score:.3f} -> round2={result2.score:.3f}\n"
        f"Reflected code:\n{child.source_code}\n"
        f"Round2 logs: {result2.logs}"
    )

    snapshot_data = {
        "round1_score": result1.score,
        "round2_score": result2.score,
        "round2_output": result2.per_case_outputs[0] if result2.per_case_outputs else "",
        "reflected_code": child.source_code,
    }
    assert snapshot_data == snapshot


# ---------------------------------------------------------------------------
# 3j. Compile-Fix Loop (broken code → detect → LLM fix → valid)
# ---------------------------------------------------------------------------

PROGRAM_WITH_DISALLOWED_IMPORT = """\
import numpy as np
from dataclasses import dataclass


@dataclass
class Observation:
    raw: str


@dataclass
class Query:
    raw: str


class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []

    def write(self, observation: Observation) -> None:
        self.store.append(observation.raw)
        self.toolkit.logger.log(f"Stored: {observation.raw[:80]}")

    def read(self, query: Query) -> str:
        return "\\n".join(self.store) if self.store else "No information stored."
"""

PROGRAM_WITH_RUNTIME_BUG = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""


@dataclass
class Observation:
    raw: str


@dataclass
class Query:
    raw: str


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []

    def write(self, observation: Observation) -> None:
        processed = process_text(observation.raw)
        self.store.append(processed)

    def read(self, query: Query) -> str:
        return "\\n".join(self.store) if self.store else "No information stored."
"""


@pytest.mark.llm
def test_compile_fix_disallowed_import(snapshot: SnapshotAssertion):
    """Compile error (disallowed import) → detected → LLM fixes → compiles and passes smoke test."""
    from programmaticmemory.evolution.sandbox import smoke_test

    # Step 1: Verify the program is broken
    compile_result = compile_kb_program(PROGRAM_WITH_DISALLOWED_IMPORT)
    assert isinstance(compile_result, CompileError), f"Expected CompileError, got {type(compile_result)}"
    assert "numpy" in compile_result.details.lower() or "import" in compile_result.message.lower()

    # Step 2: LLM fixes it
    reflector = Reflector(model=MODEL, temperature=0.0)
    fixed_code = reflector._try_fix(
        PROGRAM_WITH_DISALLOWED_IMPORT,
        error_type=compile_result.message,
        error_details=compile_result.details,
    )
    assert fixed_code is not None, "LLM failed to produce a fix"

    # Step 3: Verify the fix compiles
    fixed_result = compile_kb_program(fixed_code)
    assert isinstance(fixed_result, CompiledProgram), f"Fixed code still fails to compile: {fixed_result}"
    assert fixed_result.obs_cls.__name__ == "Observation"
    assert fixed_result.query_cls.__name__ == "Query"
    assert fixed_result.kb_cls.__name__ == "KnowledgeBase"

    # Step 4: Verify the fix passes smoke test
    st = smoke_test(fixed_code)
    assert st.success, f"Fixed code fails smoke test: {st.error}"

    assert {
        "original_error_type": compile_result.message,
        "original_error_details": compile_result.details,
        "fixed_code": fixed_code,
    } == snapshot


@pytest.mark.llm
def test_compile_fix_runtime_bug(snapshot: SnapshotAssertion):
    """Runtime bug (NameError in write) → detected by smoke test → LLM fixes → passes."""
    from programmaticmemory.evolution.sandbox import smoke_test

    # Step 1: Program compiles but fails smoke test
    compile_result = compile_kb_program(PROGRAM_WITH_RUNTIME_BUG)
    assert isinstance(compile_result, CompiledProgram), f"Expected compile success, got {compile_result}"

    st = smoke_test(PROGRAM_WITH_RUNTIME_BUG)
    assert not st.success, "Expected smoke test failure, got success"
    assert "process_text" in st.error.lower() or "nameerror" in st.error.lower()

    # Step 2: LLM fixes it
    reflector = Reflector(model=MODEL, temperature=0.0)
    fixed_code = reflector._try_fix(
        PROGRAM_WITH_RUNTIME_BUG,
        error_type="Smoke test error",
        error_details=st.error,
    )
    assert fixed_code is not None, "LLM failed to produce a fix"

    # Step 3: Verify the fix compiles and passes smoke test
    fixed_compile = compile_kb_program(fixed_code)
    assert isinstance(fixed_compile, CompiledProgram), f"Fixed code fails to compile: {fixed_compile}"

    fixed_st = smoke_test(fixed_code)
    assert fixed_st.success, f"Fixed code fails smoke test: {fixed_st.error}"

    assert {
        "original_smoke_error": st.error,
        "fixed_code": fixed_code,
    } == snapshot


# ---------------------------------------------------------------------------
# 3k. Runtime Violation Fix (oversized read → detect → LLM fix → within limits)
# ---------------------------------------------------------------------------

OVERSIZED_READ_KB_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""


@dataclass
class Observation:
    raw: str


@dataclass
class Query:
    raw: str


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store: list[str] = []

    def write(self, observation: Observation) -> None:
        self.store.append(observation.raw)

    def read(self, query: Query) -> str:
        return "x" * 5000
"""


@pytest.mark.llm
@pytest.mark.uses_chroma
def test_runtime_violation_fix_oversized_read(snapshot: SnapshotAssertion):
    """Oversized read output → detected by eval → LLM fixes → output within limits."""
    # Step 1: Evaluate with real LLM — should detect runtime violation
    program = KBProgram(source_code=OVERSIZED_READ_KB_PROGRAM)
    train_data = [
        DataItem(
            raw_text="Project Zephyr's access code is DELTA-7742.",
            question="What is Project Zephyr's access code?",
            expected_answer="DELTA-7742",
        ),
    ]
    val_data = list(train_data)

    evaluator = MemoryEvaluator(task_model=MODEL, toolkit_config=ToolkitConfig(llm_model=MODEL))
    result = evaluator.evaluate(program, train_data, val_data)

    assert result.runtime_violation is not None, "Expected runtime violation but got None"
    assert "5000" in result.runtime_violation
    assert "1000" in result.runtime_violation
    assert result.score == 0.0

    # Step 2: LLM fixes the runtime violation
    reflector = Reflector(model=MODEL, temperature=0.0)
    fixed_code = reflector.fix_runtime_violation(OVERSIZED_READ_KB_PROGRAM, result.runtime_violation)
    assert fixed_code is not None, "Reflector failed to produce a fix"

    # Step 3: Verify the fixed read() output respects the char limit
    # Compile to get classes (smoke_test already passed inside fix_runtime_violation)
    fixed_compile = compile_kb_program(fixed_code)
    assert isinstance(fixed_compile, CompiledProgram), f"Fixed code fails to compile: {fixed_compile}"
    toolkit = Toolkit(ToolkitConfig(llm_model=MODEL, llm_call_budget=5))
    try:
        # Clear collections left by smoke_test (EphemeralClient shares in-process state)
        for col in toolkit.chroma.list_collections():
            toolkit.chroma.delete_collection(col.name)
        memory = fixed_compile.kb_cls(toolkit)

        # Build obs/query dynamically from dataclass fields (LLM may rename fields)
        obs_kwargs = {
            f.name: "test value"
            for f in dataclasses.fields(fixed_compile.obs_cls)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        memory.write(fixed_compile.obs_cls(**obs_kwargs))

        query_kwargs = {
            f.name: "test query"
            for f in dataclasses.fields(fixed_compile.query_cls)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING
        }
        read_result = memory.read(fixed_compile.query_cls(**query_kwargs))
        result_str = str(read_result) if read_result is not None else ""
        assert len(result_str) <= MEMORY_READ_MAX_CHARS, (
            f"Fixed code still returns {len(result_str)} chars (limit: {MEMORY_READ_MAX_CHARS})"
        )
    finally:
        toolkit.close()

    assert {
        "runtime_violation": result.runtime_violation,
        "fixed_code": fixed_code,
    } == snapshot


# ---------------------------------------------------------------------------
# 3l. Patch Generation — reflection prompt → V4A patch → apply → compile
# ---------------------------------------------------------------------------

PATCH_MODEL = "openrouter/openai/gpt-5.1-codex-mini"


@pytest.mark.llm
def test_patch_generation_reflection(snapshot: SnapshotAssertion):
    """Real model generates a valid V4A patch from the reflection prompt.

    Sends the reflection prompt (with INITIAL_KB_PROGRAM, a low score, and a
    realistic failed case) to gpt-5.1-codex-mini, extracts the patch, applies
    it to INITIAL_KB_PROGRAM, and verifies the result compiles with all
    required classes and constants.
    """
    failed_cases = [
        {
            "question": "What is Alice's favorite color?",
            "expected": "blue",
            "output": "I don't know",
            "score": 0.0,
            "conversation_history": [
                {"role": "user", "content": "What is Alice's favorite color?"},
                {"role": "assistant", "content": "I don't know"},
            ],
            "memory_logs": ["Query: alice favorite color, store size: 0"],
        }
    ]

    user_prompt = build_reflection_user_prompt(
        code=INITIAL_KB_PROGRAM,
        score=0.200,
        failed_cases=failed_cases,
        iteration=1,
    )

    output = _llm_call(
        PATCH_MODEL,
        [{"role": "user", "content": user_prompt}],
        temperature=0.0,
    )

    # Must extract a V4A patch
    patch = _extract_patch(output)
    assert patch is not None, f"Failed to extract patch from model output:\n{output[:500]}"

    # Apply the patch to INITIAL_KB_PROGRAM
    patched_code = apply_patch(INITIAL_KB_PROGRAM, patch)
    assert patched_code != INITIAL_KB_PROGRAM, "Patch produced no changes"

    # Patched code must compile and define all three classes + three constants
    compile_result = compile_kb_program(patched_code)
    assert isinstance(compile_result, CompiledProgram), f"Compile failed: {compile_result}"
    assert compile_result.obs_cls.__name__ == "Observation"
    assert compile_result.query_cls.__name__ == "Query"
    assert compile_result.kb_cls.__name__ == "KnowledgeBase"
    assert isinstance(compile_result.instruction_observation, str)
    assert isinstance(compile_result.instruction_query, str)
    assert isinstance(compile_result.instruction_response, str)

    assert {
        "prompt": user_prompt,
        "output": output,
        "patched_code": patched_code,
    } == snapshot


# ---------------------------------------------------------------------------
# 3m. Patch Generation — compile-fix prompt → V4A patch → apply → compile
# ---------------------------------------------------------------------------

PROGRAM_WITH_SYNTAX_ERROR = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""


@dataclass
class Observation:
    raw: str


@dataclass
class Query:
    raw: str


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []

    def write(self, observation: Observation) -> None:
        self.store.append(observation.raw)

    def read(self, query: Query) -> str:
        # Bug: missing closing parenthesis
        return "\\n".join(self.store[:5] if self.store else "No information stored."
"""


@pytest.mark.llm
def test_patch_generation_compile_fix(snapshot: SnapshotAssertion):
    """Real model fixes a broken program via a V4A patch from the compile-fix prompt.

    Sends a program with a syntax error to gpt-5.1-codex-mini via the
    compile-fix prompt, extracts the patch, applies it, and verifies the
    result compiles successfully.
    """
    # Step 1: Verify the program is broken
    compile_result = compile_kb_program(PROGRAM_WITH_SYNTAX_ERROR)
    assert isinstance(compile_result, CompileError), f"Expected CompileError, got {type(compile_result)}"

    # Step 2: Build compile-fix prompt
    user_prompt = build_compile_fix_prompt(
        code=PROGRAM_WITH_SYNTAX_ERROR,
        error_type=compile_result.message,
        error_details=compile_result.details,
    )

    output = _llm_call(
        PATCH_MODEL,
        [{"role": "user", "content": user_prompt}],
        temperature=0.0,
    )

    # Step 3: Extract and apply patch
    patch = _extract_patch(output)
    assert patch is not None, f"Failed to extract patch from model output:\n{output[:500]}"

    fixed_code = apply_patch(PROGRAM_WITH_SYNTAX_ERROR, patch)

    # Step 4: Verify the fix compiles
    fixed_result = compile_kb_program(fixed_code)
    assert isinstance(fixed_result, CompiledProgram), f"Fixed code still fails to compile: {fixed_result}"
    assert fixed_result.obs_cls.__name__ == "Observation"
    assert fixed_result.query_cls.__name__ == "Query"
    assert fixed_result.kb_cls.__name__ == "KnowledgeBase"
    assert isinstance(fixed_result.instruction_observation, str)
    assert isinstance(fixed_result.instruction_query, str)
    assert isinstance(fixed_result.instruction_response, str)

    assert {
        "prompt": user_prompt,
        "output": output,
        "fixed_code": fixed_code,
    } == snapshot
