"""Tests for evolution/types.py — all dataclass types."""

from programmaticmemory.evolution.types import (
    DataItem,
    Dataset,
    EvalResult,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
)


class TestKBProgram:
    def test_content_hash_deterministic(self):
        p1 = KBProgram(source_code="class A: pass")
        p2 = KBProgram(source_code="class A: pass")
        assert p1.hash == p2.hash

    def test_content_hash_differs_for_different_code(self):
        p1 = KBProgram(source_code="class A: pass")
        p2 = KBProgram(source_code="class B: pass")
        assert p1.hash != p2.hash

    def test_hash_is_16_chars(self):
        p = KBProgram(source_code="x = 1")
        assert len(p.hash) == 16

    def test_frozen(self):
        p = KBProgram(source_code="x = 1")
        try:
            p.source_code = "y = 2"
            assert False, "Should be frozen"
        except AttributeError:
            pass

    def test_defaults(self):
        p = KBProgram(source_code="x")
        assert p.generation == 0
        assert p.parent_hash is None

    def test_with_parent(self):
        parent = KBProgram(source_code="v1")
        child = KBProgram(source_code="v2", generation=1, parent_hash=parent.hash)
        assert child.generation == 1
        assert child.parent_hash == parent.hash


class TestDataItem:
    def test_all_fields_required(self):
        item = DataItem(raw_text="fact", question="q?", expected_answer="a")
        assert item.raw_text == "fact"
        assert item.question == "q?"
        assert item.expected_answer == "a"

    def test_missing_field_raises(self):
        try:
            DataItem(raw_text="fact", question="q?")
            assert False, "Should require expected_answer"
        except TypeError:
            pass


class TestFailedCase:
    def test_defaults(self):
        fc = FailedCase(question="q", output="o", expected="e", score=0.0)
        assert fc.conversation_history == []
        assert fc.memory_logs == []

    def test_with_history(self):
        fc = FailedCase(
            question="q",
            output="o",
            expected="e",
            score=0.5,
            conversation_history=[{"role": "user", "content": "hi"}],
            memory_logs=["stored: x"],
        )
        assert len(fc.conversation_history) == 1
        assert len(fc.memory_logs) == 1


class TestEvalResult:
    def test_defaults(self):
        er = EvalResult(score=0.75)
        assert er.score == 0.75
        assert er.per_case_scores == []
        assert er.per_case_outputs == []
        assert er.failed_cases == []
        assert er.logs == []

    def test_with_data(self):
        er = EvalResult(
            score=0.5,
            per_case_scores=[1.0, 0.0],
            per_case_outputs=["yes", "no"],
            failed_cases=[FailedCase(question="q", output="no", expected="yes", score=0.0)],
            logs=["evaluated 2 cases"],
        )
        assert len(er.per_case_scores) == 2
        assert len(er.failed_cases) == 1


class TestEvolutionState:
    def test_construction(self):
        p = KBProgram(source_code="x")
        state = EvolutionState(
            best_program=p,
            best_score=0.8,
            current_program=p,
            current_score=0.8,
        )
        assert state.history == []
        assert state.total_iterations == 0

    def test_with_history(self):
        p = KBProgram(source_code="x")
        record = EvolutionRecord(iteration=1, program=p, score=0.9, accepted=True)
        state = EvolutionState(
            best_program=p,
            best_score=0.9,
            current_program=p,
            current_score=0.9,
            history=[record],
            total_iterations=1,
        )
        assert len(state.history) == 1
        assert state.history[0].accepted is True


class TestDataItemMetadata:
    def test_metadata_defaults_to_empty_dict(self):
        item = DataItem(raw_text="text", question="q", expected_answer="a")
        assert item.metadata == {}

    def test_metadata_accepts_dict(self):
        item = DataItem(raw_text="", question="q", expected_answer="a", metadata={"game_file": "/path/to/game.tw-pddl"})
        assert item.metadata["game_file"] == "/path/to/game.tw-pddl"

    def test_metadata_does_not_share_between_instances(self):
        a = DataItem(raw_text="", question="q1", expected_answer="a1")
        b = DataItem(raw_text="", question="q2", expected_answer="a2")
        a.metadata["key"] = "value"
        assert "key" not in b.metadata


class TestValScorer:
    def test_val_scorer_protocol_accepts_conforming_class(self):
        class MyScorer:
            def score_batch(
                self,
                items: list[DataItem],
                retrieved: list[str],
                task_model: str,
                instruction_response: str,
            ) -> list[tuple[str, float]]:
                return [("answer", 1.0)] * len(items)

        scorer = MyScorer()
        items = [DataItem(raw_text="", question="q", expected_answer="a")]
        result = scorer.score_batch(items, ["retrieved"], "model", "instruction")
        assert result == [("answer", 1.0)]

    def test_dataset_val_scorer_defaults_to_none(self):
        ds = Dataset(train=[], val=[], test=[])
        assert ds.val_scorer is None

    def test_dataset_accepts_val_scorer(self):
        class MyScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response):
                return []

        ds = Dataset(train=[], val=[], test=[], val_scorer=MyScorer())
        assert ds.val_scorer is not None
