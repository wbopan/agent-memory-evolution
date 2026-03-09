"""Tests for evolution/prompts.py — prompt templates and construction."""

from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.prompts import (
    INITIAL_KB_PROGRAM,
    KB_INTERFACE_SPEC,
    ReflectionPromptConfig,
    _sample_cases,
    build_compile_fix_prompt,
    build_knowledge_item_generation_prompt,
    build_knowledge_item_with_feedback_prompt,
    build_query_generation_prompt,
    build_reflection_user_prompt,
    build_retrieved_memory_prompt,
)
from programmaticmemory.evolution.types import TrainExample


class TestInitialKBProgram:
    def test_contains_required_classes(self):
        assert "class KnowledgeItem" in INITIAL_KB_PROGRAM
        assert "class Query" in INITIAL_KB_PROGRAM
        assert "class KnowledgeBase" in INITIAL_KB_PROGRAM
        assert "INSTRUCTION_KNOWLEDGE_ITEM" in INITIAL_KB_PROGRAM
        assert "INSTRUCTION_QUERY" in INITIAL_KB_PROGRAM
        assert "INSTRUCTION_RESPONSE" in INITIAL_KB_PROGRAM
        assert "ALWAYS_ON_KNOWLEDGE" in INITIAL_KB_PROGRAM

    def test_compiles(self):
        from programmaticmemory.evolution.sandbox import CompileError, compile_kb_program

        result = compile_kb_program(INITIAL_KB_PROGRAM)
        assert not isinstance(result, CompileError)

    def test_smoke_test_passes(self):
        from programmaticmemory.evolution.sandbox import smoke_test

        result = smoke_test(INITIAL_KB_PROGRAM)
        assert result.success is True


class TestKBInterfaceSpec:
    def test_contains_key_components(self):
        assert "KnowledgeItem" in KB_INTERFACE_SPEC
        assert "Query" in KB_INTERFACE_SPEC
        assert "KnowledgeBase" in KB_INTERFACE_SPEC
        assert "Toolkit" in KB_INTERFACE_SPEC
        assert "write" in KB_INTERFACE_SPEC
        assert "read" in KB_INTERFACE_SPEC


class TestBuildReflectionUserPrompt:
    def test_includes_code_and_score(self, snapshot: SnapshotAssertion):
        prompt = build_reflection_user_prompt(
            code="class KnowledgeBase: pass",
            score=0.42,
            failed_cases=[],
            iteration=3,
        )
        assert "class KnowledgeBase: pass" in prompt
        assert "0.420" in prompt
        assert 'iteration="3"' in prompt
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
        config = ReflectionPromptConfig(max_memory_log_chars=2000)
        prompt = build_reflection_user_prompt(
            code="code here",
            score=0.0,
            failed_cases=cases,
            iteration=1,
            config=config,
        )
        assert "What is X?" in prompt
        assert "42" in prompt
        assert "unknown" in prompt
        assert "Stored: fact about X" in prompt
        assert "Query: What is X" in prompt
        assert prompt == snapshot

    def test_samples_cases_when_exceeding_limit(self, snapshot: SnapshotAssertion):
        cases = [{"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0} for i in range(10)]
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
        # Default max_failed_cases=2 — weighted sampling selects exactly 2 from 10
        case_count = prompt.count("<case id=")
        assert case_count == 2
        assert prompt == snapshot

    def test_long_conversation_not_truncated(self):
        long_content = "x" * 500
        cases = [
            {
                "question": "q",
                "expected": "a",
                "output": "o",
                "score": 0.0,
                "conversation_history": [
                    {"role": "user", "content": long_content},
                ],
            }
        ]
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
        assert long_content in prompt

    def test_many_memory_logs_within_budget(self):
        logs = [f"log entry {i}" for i in range(20)]
        cases = [
            {
                "question": "q",
                "expected": "a",
                "output": "o",
                "score": 0.0,
                "memory_logs": logs,
            }
        ]
        config = ReflectionPromptConfig(max_memory_log_chars=2000)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        assert "log entry 0" in prompt
        assert "log entry 19" in prompt

    def test_handles_empty_optional_fields(self, snapshot: SnapshotAssertion):
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0}]
        prompt = build_reflection_user_prompt(code="x", score=0.5, failed_cases=cases, iteration=1)
        assert "q" in prompt
        assert prompt == snapshot

    def test_includes_success_cases(self, snapshot: SnapshotAssertion):
        failed = [{"question": "q_fail", "expected": "a_fail", "output": "wrong", "score": 0.0}]
        success = [
            {
                "question": "q_success",
                "expected": "correct_answer",
                "output": "correct_answer",
                "score": 1.0,
                "conversation_history": [
                    {"role": "user", "content": "query prompt"},
                    {"role": "assistant", "content": "correct_answer"},
                ],
            }
        ]
        prompt = build_reflection_user_prompt(
            code="class KnowledgeBase: pass",
            score=0.5,
            failed_cases=failed,
            iteration=1,
            success_cases=success,
        )
        assert "<success_cases>" in prompt
        assert "q_success" in prompt
        assert "correct_answer" in prompt
        assert "Preserve the behavior" in prompt
        assert prompt == snapshot

    def test_success_cases_limited_by_config(self, snapshot: SnapshotAssertion):
        success = [{"question": f"sq{i}", "expected": f"sa{i}", "output": f"sa{i}", "score": 1.0} for i in range(5)]
        config = ReflectionPromptConfig(max_success_cases=1)
        prompt = build_reflection_user_prompt(
            code="x", score=0.8, failed_cases=[], iteration=1, config=config, success_cases=success
        )
        assert "sq0" in prompt
        assert "sq1" not in prompt
        assert prompt == snapshot

    def test_no_success_cases_omits_section(self, snapshot: SnapshotAssertion):
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=[], iteration=1, success_cases=[])
        assert "<success_cases>" not in prompt
        assert prompt == snapshot

    def test_includes_train_examples(self, snapshot: SnapshotAssertion):
        examples = [
            TrainExample(
                messages=[
                    {
                        "role": "user",
                        "content": 'Given the following text...\nText: Hello world\nSchema: {"raw": "..."}',
                    },
                    {"role": "assistant", "content": '{"raw": "Hello world"}'},
                ]
            )
        ]
        prompt = build_reflection_user_prompt(
            code="class KnowledgeBase: pass",
            score=0.1,
            failed_cases=[],
            iteration=1,
            train_examples=examples,
        )
        assert "<write_examples>" in prompt
        assert "Hello world" in prompt
        assert prompt == snapshot


class TestReflectionPromptConfig:
    def test_max_failed_cases(self, snapshot: SnapshotAssertion):
        cases = [{"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0} for i in range(10)]
        config = ReflectionPromptConfig(max_failed_cases=2)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        # Weighted sampling selects exactly 2 from 10
        case_count = prompt.count("<case id=")
        assert case_count == 2
        assert prompt == snapshot

    def test_max_train_examples(self, snapshot: SnapshotAssertion):
        examples = [TrainExample(messages=[{"role": "user", "content": f"example {i}"}]) for i in range(10)]
        config = ReflectionPromptConfig(max_train_examples=2)
        prompt = build_reflection_user_prompt(
            code="x", score=0.0, failed_cases=[], iteration=1, train_examples=examples, config=config
        )
        assert "example 0" in prompt
        assert "example 1" in prompt
        assert "example 2" not in prompt
        assert prompt == snapshot

    def test_max_success_cases(self, snapshot: SnapshotAssertion):
        success = [{"question": f"sq{i}", "expected": f"sa{i}", "output": f"sa{i}", "score": 1.0} for i in range(5)]
        config = ReflectionPromptConfig(max_success_cases=2)
        prompt = build_reflection_user_prompt(
            code="x", score=0.5, failed_cases=[], iteration=1, config=config, success_cases=success
        )
        assert "sq0" in prompt
        assert "sq1" in prompt
        assert "sq2" not in prompt
        assert prompt == snapshot

    def test_max_memory_log_chars_truncates(self):
        # Each log line "  - log entry NNN\n" is ~20 chars, 50 entries = ~1000 chars
        logs = [f"log entry {i:03d} with extra padding to make it longer" for i in range(50)]
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0, "memory_logs": logs}]
        config = ReflectionPromptConfig(max_memory_log_chars=200)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        assert "chars omitted" in prompt
        # First log entry should be partially present (in the head)
        assert "log entry 000" in prompt

    def test_max_memory_log_chars_zero_excludes(self, snapshot: SnapshotAssertion):
        logs = ["log entry 1", "log entry 2"]
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0, "memory_logs": logs}]
        config = ReflectionPromptConfig(max_memory_log_chars=0)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        assert "<memory_logs>" not in prompt
        assert "log entry" not in prompt
        assert prompt == snapshot

    def test_memory_logs_deduplicated(self):
        shared_logs = ["init db", "write knowledge item", "read query"]
        cases = [
            {"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0, "memory_logs": shared_logs}
            for i in range(3)
        ]
        config = ReflectionPromptConfig(max_memory_log_chars=2000)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        # Each log string should appear exactly once (deduplicated into standalone section)
        for log in shared_logs:
            assert prompt.count(log) == 1
        # Should have a standalone debug logs section, not per-case
        assert "<memory_debug_logs>" in prompt
        assert "<memory_logs>" not in prompt

    def test_default_config(self, snapshot: SnapshotAssertion):
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0}]
        prompt_no_config = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
        prompt_default = build_reflection_user_prompt(
            code="x", score=0.0, failed_cases=cases, iteration=1, config=ReflectionPromptConfig()
        )
        assert prompt_no_config == prompt_default
        assert prompt_no_config == snapshot


class TestBuildQueryGenerationPrompt:
    def test_includes_question_and_schema(self, snapshot: SnapshotAssertion):
        prompt = build_query_generation_prompt("What is the capital?", "Fields:\n  - raw: str")
        assert "What is the capital?" in prompt
        assert "raw: str" in prompt
        assert "JSON" in prompt
        assert prompt == snapshot


class TestBuildKnowledgeItemGenerationPrompt:
    def test_includes_text_and_schema(self, snapshot: SnapshotAssertion):
        prompt = build_knowledge_item_generation_prompt("Paris is the capital.", "Fields:\n  - raw: str")
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

    def test_always_on_knowledge_injected(self, snapshot: SnapshotAssertion):
        prompt = build_retrieved_memory_prompt(
            "some retrieved text", "answer this", always_on_knowledge="Be systematic."
        )
        assert prompt == snapshot
        # always_on_knowledge should appear before the retrieved text
        aok_pos = prompt.index("Be systematic.")
        retrieved_pos = prompt.index("some retrieved text")
        assert aok_pos < retrieved_pos

    def test_always_on_knowledge_empty_unchanged(self, snapshot: SnapshotAssertion):
        prompt_without = build_retrieved_memory_prompt("some retrieved text", "answer this")
        prompt_with_empty = build_retrieved_memory_prompt("some retrieved text", "answer this", always_on_knowledge="")
        assert prompt_without == prompt_with_empty
        assert prompt_without == snapshot


class TestBuildKnowledgeItemWithFeedbackPrompt:
    def test_includes_feedback_and_ground_truth(self, snapshot: SnapshotAssertion):
        prompt = build_knowledge_item_with_feedback_prompt(
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
        prompt = build_knowledge_item_with_feedback_prompt("ok", "42", "schema")
        assert "Ground truth" in prompt
        assert "42" in prompt
        assert prompt == snapshot


class TestSampleCases:
    def test_returns_all_when_fewer_than_k(self):
        """When len(cases) <= k, return all cases unchanged."""
        cases = [{"question": f"q{i}", "score": 0.0} for i in range(3)]
        result = _sample_cases(cases, k=5, seed=42)
        assert result == cases

    def test_returns_k_cases(self):
        """When len(cases) > k, return exactly k cases."""
        cases = [{"question": f"q{i}", "score": 0.0} for i in range(10)]
        result = _sample_cases(cases, k=3, seed=42)
        assert len(result) == 3

    def test_deterministic_with_same_seed(self):
        """Same seed produces same selection."""
        cases = [{"question": f"q{i}", "score": i / 10} for i in range(10)]
        r1 = _sample_cases(cases, k=3, seed=42)
        r2 = _sample_cases(cases, k=3, seed=42)
        assert r1 == r2

    def test_different_seeds_produce_different_selections(self):
        """Different seeds can select different subsets from equal-weight pool."""
        cases = [{"question": f"q{i}", "score": 0.0} for i in range(10)]
        all_selected = set()
        for seed in range(20):
            result = _sample_cases(cases, k=2, seed=seed)
            for case in result:
                all_selected.add(case["question"])
        # With 10 equal-weight cases and 20 different seeds, we should see diversity
        assert len(all_selected) > 2

    def test_low_scores_preferred(self):
        """Over many seeds, score=0 cases appear more often than score=0.9 cases."""
        cases = [{"question": f"hard_{i}", "score": 0.0} for i in range(5)]
        cases += [{"question": f"easy_{i}", "score": 0.9} for i in range(5)]
        hard_count = 0
        easy_count = 0
        for seed in range(200):
            result = _sample_cases(cases, k=3, seed=seed)
            for case in result:
                if case["question"].startswith("hard"):
                    hard_count += 1
                else:
                    easy_count += 1
        # weight=1.0 vs weight=0.1 — hard cases should dominate
        assert hard_count > easy_count * 3

    def test_preserves_relative_order(self):
        """Selected cases maintain their original order from the input list."""
        cases = [{"question": f"q{i}", "score": 0.0} for i in range(10)]
        result = _sample_cases(cases, k=3, seed=42)
        # Extract indices of selected cases in the original list
        indices = [cases.index(c) for c in result]
        assert indices == sorted(indices)


class TestBuildCompileFixPrompt:
    def test_includes_code_and_error(self, snapshot: SnapshotAssertion):
        prompt = build_compile_fix_prompt(
            code="class KnowledgeBase: pass",
            error_type="Syntax error",
            error_details="unexpected indent at line 5",
        )
        assert "class KnowledgeBase: pass" in prompt
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
