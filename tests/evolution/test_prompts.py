"""Tests for evolution/prompts.py — prompt templates and construction."""

from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.prompts import (
    COMPILE_FIX_SYSTEM_PROMPT,
    INITIAL_MEMORY_PROGRAM,
    MEMORY_INTERFACE_SPEC,
    REFLECTION_SYSTEM_PROMPT,
    build_compile_fix_prompt,
    build_observation_generation_prompt,
    build_observation_with_feedback_prompt,
    build_query_generation_prompt,
    build_reflection_user_prompt,
    build_response_prompt,
    build_retrieved_memory_prompt,
)


class TestInitialMemoryProgram:
    def test_contains_required_classes(self):
        assert "class Observation" in INITIAL_MEMORY_PROGRAM
        assert "class Query" in INITIAL_MEMORY_PROGRAM
        assert "class Memory" in INITIAL_MEMORY_PROGRAM

    def test_compiles(self):
        from programmaticmemory.evolution.sandbox import CompileError, compile_memory_program

        result = compile_memory_program(INITIAL_MEMORY_PROGRAM)
        assert not isinstance(result, CompileError)

    def test_smoke_test_passes(self):
        from programmaticmemory.evolution.sandbox import smoke_test

        result = smoke_test(INITIAL_MEMORY_PROGRAM)
        assert result.success is True


class TestMemoryInterfaceSpec:
    def test_contains_key_components(self):
        assert "Observation" in MEMORY_INTERFACE_SPEC
        assert "Query" in MEMORY_INTERFACE_SPEC
        assert "Memory" in MEMORY_INTERFACE_SPEC
        assert "Toolkit" in MEMORY_INTERFACE_SPEC
        assert "write" in MEMORY_INTERFACE_SPEC
        assert "read" in MEMORY_INTERFACE_SPEC


class TestReflectionSystemPrompt:
    def test_has_interface_spec_placeholder(self):
        assert "{interface_spec}" in REFLECTION_SYSTEM_PROMPT

    def test_format_works(self, snapshot: SnapshotAssertion):
        formatted = REFLECTION_SYSTEM_PROMPT.format(interface_spec=MEMORY_INTERFACE_SPEC)
        assert "Observation" in formatted
        assert "{interface_spec}" not in formatted
        assert formatted == snapshot


class TestBuildReflectionUserPrompt:
    def test_includes_code_and_score(self, snapshot: SnapshotAssertion):
        prompt = build_reflection_user_prompt(
            code="class Memory: pass",
            score=0.42,
            failed_cases=[],
            iteration=3,
        )
        assert "class Memory: pass" in prompt
        assert "0.420" in prompt
        assert "iteration 3" in prompt
        assert prompt == snapshot

    def test_includes_failed_cases(self, snapshot: SnapshotAssertion):
        cases = [
            {
                "question": "What is X?",
                "expected": "42",
                "output": "unknown",
                "score": 0.0,
                "conversation_history": [
                    {"role": "user", "content": "What is X?"},
                    {"role": "assistant", "content": "unknown"},
                ],
                "memory_logs": ["Stored: fact about X", "Query: What is X"],
            }
        ]
        prompt = build_reflection_user_prompt(
            code="code here",
            score=0.0,
            failed_cases=cases,
            iteration=1,
        )
        assert "What is X?" in prompt
        assert "42" in prompt
        assert "unknown" in prompt
        assert "Stored: fact about X" in prompt
        assert "Query: What is X" in prompt
        assert prompt == snapshot

    def test_limits_to_5_cases(self, snapshot: SnapshotAssertion):
        cases = [{"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0} for i in range(10)]
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
        # Should only include first 5
        assert "q4" in prompt
        assert "q5" not in prompt
        assert prompt == snapshot

    def test_handles_empty_optional_fields(self, snapshot: SnapshotAssertion):
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0}]
        prompt = build_reflection_user_prompt(code="x", score=0.5, failed_cases=cases, iteration=1)
        assert "q" in prompt
        assert prompt == snapshot


class TestBuildQueryGenerationPrompt:
    def test_includes_question_and_schema(self, snapshot: SnapshotAssertion):
        prompt = build_query_generation_prompt("What is the capital?", "Fields:\n  - raw: str")
        assert "What is the capital?" in prompt
        assert "raw: str" in prompt
        assert "JSON" in prompt
        assert prompt == snapshot


class TestBuildObservationGenerationPrompt:
    def test_includes_text_and_schema(self, snapshot: SnapshotAssertion):
        prompt = build_observation_generation_prompt("Paris is the capital.", "Fields:\n  - raw: str")
        assert "Paris is the capital." in prompt
        assert "raw: str" in prompt
        assert "JSON" in prompt
        assert prompt == snapshot


class TestBuildRetrievedMemoryPrompt:
    def test_includes_retrieved_in_tags(self, snapshot: SnapshotAssertion):
        prompt = build_retrieved_memory_prompt("fact1\nfact2")
        assert "<retrieved_memory>" in prompt
        assert "</retrieved_memory>" in prompt
        assert "fact1\nfact2" in prompt
        assert "original question" in prompt.lower()
        assert prompt == snapshot

    def test_empty_retrieved(self, snapshot: SnapshotAssertion):
        prompt = build_retrieved_memory_prompt("")
        assert "<retrieved_memory>" in prompt
        assert prompt == snapshot


class TestBuildObservationWithFeedbackPrompt:
    def test_includes_feedback_and_ground_truth(self, snapshot: SnapshotAssertion):
        prompt = build_observation_with_feedback_prompt(
            evaluation_result="Score: 0.0 (incorrect)",
            ground_truth="Paris",
            schema="Fields:\n  - raw: str",
        )
        assert "Score: 0.0 (incorrect)" in prompt
        assert "Paris" in prompt
        assert "raw: str" in prompt
        assert "JSON" in prompt
        assert prompt == snapshot

    def test_includes_ground_truth_label(self, snapshot: SnapshotAssertion):
        prompt = build_observation_with_feedback_prompt("ok", "42", "schema")
        assert "Ground truth" in prompt
        assert "42" in prompt
        assert prompt == snapshot


class TestBuildResponsePrompt:
    def test_includes_question_and_retrieved(self, snapshot: SnapshotAssertion):
        prompt = build_response_prompt("What is X?", "X is 42.")
        assert "What is X?" in prompt
        assert "X is 42." in prompt
        assert prompt == snapshot


class TestCompileFixSystemPrompt:
    def test_contains_interface_spec_placeholder(self):
        assert "{interface_spec}" in COMPILE_FIX_SYSTEM_PROMPT

    def test_format_works(self, snapshot: SnapshotAssertion):
        formatted = COMPILE_FIX_SYSTEM_PROMPT.format(interface_spec="spec here")
        assert "spec here" in formatted
        assert "{interface_spec}" not in formatted
        assert formatted == snapshot

    def test_instructs_fix(self, snapshot: SnapshotAssertion):
        formatted = COMPILE_FIX_SYSTEM_PROMPT.format(interface_spec="spec")
        assert "fix" in formatted.lower() or "correct" in formatted.lower()
        assert formatted == snapshot


class TestBuildCompileFixPrompt:
    def test_includes_code_and_error(self, snapshot: SnapshotAssertion):
        prompt = build_compile_fix_prompt(
            code="class Memory: pass",
            error_type="Syntax error",
            error_details="unexpected indent at line 5",
        )
        assert "class Memory: pass" in prompt
        assert "Syntax error" in prompt
        assert "unexpected indent at line 5" in prompt
        assert prompt == snapshot

    def test_includes_error_type_label(self, snapshot: SnapshotAssertion):
        prompt = build_compile_fix_prompt(
            code="x",
            error_type="Import whitelist violation",
            error_details="Disallowed import: numpy",
        )
        assert "Import whitelist violation" in prompt
        assert "Disallowed import: numpy" in prompt
        assert prompt == snapshot
