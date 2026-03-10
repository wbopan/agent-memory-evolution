"""Tests for evolution/types.py — all dataclass types."""

from programmaticmemory.evolution.types import (
    DataItem,
    Dataset,
    EvalResult,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    PoolEntry,
    ProgramPool,
    SoftmaxSelection,
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
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.8))
        state = EvolutionState(pool=pool, best_score=0.8)
        assert state.history == []
        assert state.total_iterations == 0
        assert state.best_program == p

    def test_with_history(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.9))
        record = EvolutionRecord(iteration=1, program=p, score=0.9, parent_hash=None)
        state = EvolutionState(
            pool=pool,
            best_score=0.9,
            history=[record],
            total_iterations=1,
        )
        assert len(state.history) == 1
        assert state.history[0].parent_hash is None


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
                always_on_knowledge: str,
            ) -> list[tuple[str, float]]:
                return [("answer", 1.0)] * len(items)

        scorer = MyScorer()
        items = [DataItem(raw_text="", question="q", expected_answer="a")]
        result = scorer.score_batch(items, ["retrieved"], "model", "instruction", "")
        assert result == [("answer", 1.0)]

    def test_dataset_val_scorer_defaults_to_none(self):
        ds = Dataset(train=[], val=[], test=[])
        assert ds.val_scorer is None

    def test_dataset_accepts_val_scorer(self):
        class MyScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response, always_on_knowledge):
                return []

        ds = Dataset(train=[], val=[], test=[], val_scorer=MyScorer())
        assert ds.val_scorer is not None


class TestPoolEntry:
    def test_construction(self):
        p = KBProgram(source_code="x")
        er = EvalResult(score=0.8)
        entry = PoolEntry(program=p, eval_result=er)
        assert entry.score == 0.8
        assert entry.program == p
        assert entry.eval_result == er


class TestProgramPool:
    def test_add_and_best(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p1 = KBProgram(source_code="a")
        p2 = KBProgram(source_code="b")
        pool.add(p1, EvalResult(score=0.3))
        pool.add(p2, EvalResult(score=0.8))
        assert pool.best.score == 0.8
        assert pool.best.program == p2

    def test_best_with_single_entry(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.5))
        assert pool.best.score == 0.5

    def test_sample_parent_returns_pool_entry(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.5))
        entry = pool.sample_parent()
        assert isinstance(entry, PoolEntry)
        assert entry.program == p

    def test_sample_parent_single_entry_always_returns_it(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="only")
        pool.add(p, EvalResult(score=0.5))
        for _ in range(10):
            assert pool.sample_parent().program == p

    def test_len(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        assert len(pool) == 0
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.5))
        assert len(pool) == 1

    def test_entries_accessible(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.5))
        assert len(pool.entries) == 1


class TestSoftmaxSelection:
    def test_weights_favor_higher_scores(self):
        from programmaticmemory.evolution.types import SoftmaxSelection

        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.8)),
            PoolEntry(program=KBProgram(source_code="b"), eval_result=EvalResult(score=0.2)),
        ]
        weights = strategy.weights(entries)
        assert weights[0] > weights[1]

    def test_sample_returns_pool_entry(self):
        from programmaticmemory.evolution.types import SoftmaxSelection

        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.5)),
        ]
        result = strategy.sample(entries)
        assert isinstance(result, PoolEntry)

    def test_distribution_matches_softmax(self):
        """Verify softmax selection matches expected probabilities."""
        import math
        import random as _random
        from collections import Counter

        from programmaticmemory.evolution.types import SoftmaxSelection

        _random.seed(42)
        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="best"), eval_result=EvalResult(score=0.6)),
            PoolEntry(program=KBProgram(source_code="mid1"), eval_result=EvalResult(score=0.4)),
            PoolEntry(program=KBProgram(source_code="mid2"), eval_result=EvalResult(score=0.4)),
            PoolEntry(program=KBProgram(source_code="weak"), eval_result=EvalResult(score=0.2)),
        ]

        n = 10000
        counts = Counter()
        for _ in range(n):
            entry = strategy.sample(entries)
            counts[entry.program.source_code] += 1

        scores = [0.6, 0.4, 0.4, 0.2]
        max_s = max(scores)
        weights = [math.exp((s - max_s) / 0.15) for s in scores]
        z = sum(weights)
        expected = [w / z for w in weights]

        labels = ["best", "mid1", "mid2", "weak"]
        for label, exp_p in zip(labels, expected, strict=True):
            empirical_p = counts[label] / n
            assert abs(empirical_p - exp_p) < 0.05, f"{label}: expected {exp_p:.3f}, got {empirical_p:.3f}"

    def test_repr(self):
        from programmaticmemory.evolution.types import SoftmaxSelection

        strategy = SoftmaxSelection(temperature=0.15)
        assert repr(strategy) == "SoftmaxSelection(T=0.15)"


class TestRecencyDecaySelection:
    def test_weights_decay_by_generation(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="old", generation=5), eval_result=EvalResult(score=0.9)),
            PoolEntry(program=KBProgram(source_code="new", generation=0), eval_result=EvalResult(score=0.1)),
        ]
        weights = strategy.weights(entries)
        assert weights[1] > weights[0]  # newer has higher weight despite lower score

    def test_weights_ignore_score(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=2), eval_result=EvalResult(score=0.9)),
            PoolEntry(program=KBProgram(source_code="b", generation=2), eval_result=EvalResult(score=0.1)),
        ]
        weights = strategy.weights(entries)
        assert weights[0] == weights[1]

    def test_weights_values(self):
        import math

        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=0), eval_result=EvalResult(score=0.5)),
            PoolEntry(program=KBProgram(source_code="b", generation=3), eval_result=EvalResult(score=0.5)),
        ]
        weights = strategy.weights(entries)
        assert math.isclose(weights[0], 1.0)
        assert math.isclose(weights[1], 0.8**3)

    def test_sample_returns_pool_entry(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=0), eval_result=EvalResult(score=0.5)),
        ]
        result = strategy.sample(entries)
        assert isinstance(result, PoolEntry)

    def test_repr(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=0.8)
        assert repr(strategy) == "RecencyDecaySelection(decay=0.8)"
