"""Tests for checkpoint serialization/deserialization round-trips."""

from __future__ import annotations

from programmaticmemory.evolution.checkpoint import (
    deserialize_eval_result,
    deserialize_failed_case,
    deserialize_pool_entry,
    serialize_eval_result,
    serialize_failed_case,
    serialize_pool_entry,
)
from programmaticmemory.evolution.types import (
    EvalResult,
    FailedCase,
    KBProgram,
    PoolEntry,
    TrainExample,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SOURCE = "class KnowledgeBase: pass"


def _make_failed_case(**kwargs) -> FailedCase:
    defaults = dict(
        question="What is X?",
        output="A",
        expected="B",
        score=0.0,
        conversation_history=[{"role": "user", "content": "hi"}],
        memory_logs=["log line 1"],
    )
    defaults.update(kwargs)
    return FailedCase(**defaults)


def _make_eval_result(**kwargs) -> EvalResult:
    defaults = dict(
        score=0.75,
        per_case_scores=[0.5, 1.0],
        per_case_outputs=["answer A", "answer B"],
        failed_cases=[_make_failed_case(score=0.0)],
        success_cases=[_make_failed_case(score=1.0, output="B", expected="B")],
        logs=["eval log"],
        train_examples=[TrainExample(messages=[{"role": "user", "content": "train"}])],
        runtime_violation=None,
    )
    defaults.update(kwargs)
    return EvalResult(**defaults)


def _make_pool_entry(name: str = "seed_0", with_reflection: bool = False) -> PoolEntry:
    program = KBProgram(source_code=_SOURCE, generation=1, parent_hash="abc123")
    reflection = _make_eval_result(score=0.8) if with_reflection else None
    return PoolEntry(
        program=program,
        eval_result=_make_eval_result(),
        name=name,
        reflection_result=reflection,
        commit_message="initial commit" if name == "seed_0" else None,
    )


# ---------------------------------------------------------------------------
# FailedCase round-trips
# ---------------------------------------------------------------------------


class TestFailedCaseSerialization:
    def test_basic_round_trip(self):
        fc = _make_failed_case()
        d = serialize_failed_case(fc)
        fc2 = deserialize_failed_case(d)
        assert fc2.question == fc.question
        assert fc2.output == fc.output
        assert fc2.expected == fc.expected
        assert fc2.score == fc.score
        assert fc2.conversation_history == fc.conversation_history
        assert fc2.memory_logs == fc.memory_logs

    def test_empty_optional_fields(self):
        fc = FailedCase(question="Q", output="O", expected="E", score=0.5)
        d = serialize_failed_case(fc)
        fc2 = deserialize_failed_case(d)
        assert fc2.conversation_history == []
        assert fc2.memory_logs == []

    def test_serialized_dict_is_json_safe(self):
        import json

        fc = _make_failed_case()
        d = serialize_failed_case(fc)
        # Should not raise
        json.dumps(d)

    def test_score_preserved_exactly(self):
        fc = _make_failed_case(score=0.123456789)
        d = serialize_failed_case(fc)
        fc2 = deserialize_failed_case(d)
        assert fc2.score == fc.score

    def test_multi_turn_conversation_history(self):
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
            {"role": "user", "content": "Bye"},
        ]
        fc = _make_failed_case(conversation_history=history)
        fc2 = deserialize_failed_case(serialize_failed_case(fc))
        assert fc2.conversation_history == history

    def test_missing_optional_keys_in_dict(self):
        """deserialize_failed_case should handle dicts that omit optional keys."""
        d = {"question": "Q", "output": "O", "expected": "E", "score": 0.0}
        fc = deserialize_failed_case(d)
        assert fc.conversation_history == []
        assert fc.memory_logs == []


# ---------------------------------------------------------------------------
# EvalResult round-trips
# ---------------------------------------------------------------------------


class TestEvalResultSerialization:
    def test_basic_round_trip(self):
        er = _make_eval_result()
        d = serialize_eval_result(er)
        er2 = deserialize_eval_result(d)
        assert er2.score == er.score
        assert er2.per_case_scores == er.per_case_scores
        assert er2.per_case_outputs == er.per_case_outputs
        assert len(er2.failed_cases) == len(er.failed_cases)
        assert len(er2.success_cases) == len(er.success_cases)
        assert er2.logs == er.logs
        assert er2.runtime_violation == er.runtime_violation

    def test_runtime_violation_preserved(self):
        er = _make_eval_result(runtime_violation="timeout after 5s")
        d = serialize_eval_result(er)
        er2 = deserialize_eval_result(d)
        assert er2.runtime_violation == "timeout after 5s"

    def test_runtime_violation_none(self):
        er = _make_eval_result(runtime_violation=None)
        d = serialize_eval_result(er)
        er2 = deserialize_eval_result(d)
        assert er2.runtime_violation is None

    def test_train_examples_round_trip(self):
        messages = [{"role": "user", "content": "write this"}, {"role": "assistant", "content": "ok"}]
        er = _make_eval_result(train_examples=[TrainExample(messages=messages)])
        d = serialize_eval_result(er)
        er2 = deserialize_eval_result(d)
        assert len(er2.train_examples) == 1
        assert er2.train_examples[0].messages == messages

    def test_empty_collections(self):
        er = EvalResult(score=0.0)
        d = serialize_eval_result(er)
        er2 = deserialize_eval_result(d)
        assert er2.per_case_scores == []
        assert er2.per_case_outputs == []
        assert er2.failed_cases == []
        assert er2.success_cases == []
        assert er2.logs == []
        assert er2.train_examples == []

    def test_serialized_dict_is_json_safe(self):
        import json

        er = _make_eval_result()
        d = serialize_eval_result(er)
        json.dumps(d)

    def test_failed_cases_content_preserved(self):
        fc = _make_failed_case(question="specific Q", output="wrong", expected="right", score=0.0)
        er = _make_eval_result(failed_cases=[fc])
        er2 = deserialize_eval_result(serialize_eval_result(er))
        assert er2.failed_cases[0].question == "specific Q"
        assert er2.failed_cases[0].output == "wrong"
        assert er2.failed_cases[0].expected == "right"

    def test_missing_optional_keys_tolerated(self):
        """Deserializing a minimal dict should use defaults."""
        d = {"score": 0.42}
        er = deserialize_eval_result(d)
        assert er.score == 0.42
        assert er.per_case_scores == []
        assert er.logs == []
        assert er.runtime_violation is None


# ---------------------------------------------------------------------------
# PoolEntry round-trips
# ---------------------------------------------------------------------------


class TestPoolEntrySerialization:
    def test_basic_round_trip(self):
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.name == entry.name
        assert entry2.program.source_code == _SOURCE
        assert entry2.program.generation == entry.program.generation
        assert entry2.program.parent_hash == entry.program.parent_hash
        assert entry2.eval_result.score == entry.eval_result.score
        assert entry2.commit_message == entry.commit_message

    def test_source_code_not_in_serialized_dict(self):
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        assert "source_code" not in d

    def test_program_hash_included(self):
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        assert d["program_hash"] == entry.program.hash

    def test_with_reflection_result(self):
        entry = _make_pool_entry(with_reflection=True)
        d = serialize_pool_entry(entry)
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.reflection_result is not None
        assert entry2.reflection_result.score == entry.reflection_result.score

    def test_without_reflection_result(self):
        entry = _make_pool_entry(with_reflection=False)
        d = serialize_pool_entry(entry)
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.reflection_result is None

    def test_commit_message_none(self):
        entry = _make_pool_entry(name="iter_5")
        d = serialize_pool_entry(entry)
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.commit_message is None

    def test_hash_recomputed_from_source_code(self):
        """Hash is derived from source_code at runtime, not stored."""
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        # Provide different source code → different hash
        entry2 = deserialize_pool_entry(d, source_code="# different code")
        assert entry2.program.hash != entry.program.hash

    def test_serialized_dict_is_json_safe(self):
        import json

        entry = _make_pool_entry(with_reflection=True)
        d = serialize_pool_entry(entry)
        json.dumps(d)

    def test_generation_zero_default(self):
        """If program_generation key is missing, default to 0."""
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        del d["program_generation"]
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.program.generation == 0

    def test_score_property_delegates_to_eval_result(self):
        entry = _make_pool_entry()
        d = serialize_pool_entry(entry)
        entry2 = deserialize_pool_entry(d, source_code=_SOURCE)
        assert entry2.score == entry2.eval_result.score
