"""Tests for evolution/types.py — all dataclass types."""

import pytest

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


class TestMaxSelection:
    def test_always_selects_highest_score(self):
        from programmaticmemory.evolution.types import MaxSelection

        strategy = MaxSelection()
        entries = [
            PoolEntry(program=KBProgram(source_code="weak"), eval_result=EvalResult(score=0.2)),
            PoolEntry(program=KBProgram(source_code="best"), eval_result=EvalResult(score=0.8)),
            PoolEntry(program=KBProgram(source_code="mid"), eval_result=EvalResult(score=0.5)),
        ]
        # Should always return the best, every time
        for _ in range(10):
            result = strategy.sample(entries)
            assert result.program.source_code == "best"

    def test_weights_are_zero_except_max(self):
        from programmaticmemory.evolution.types import MaxSelection

        strategy = MaxSelection()
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.3)),
            PoolEntry(program=KBProgram(source_code="b"), eval_result=EvalResult(score=0.9)),
            PoolEntry(program=KBProgram(source_code="c"), eval_result=EvalResult(score=0.5)),
        ]
        weights = strategy.weights(entries)
        assert weights == [0.0, 1.0, 0.0]

    def test_ties_give_equal_weight(self):
        from programmaticmemory.evolution.types import MaxSelection

        strategy = MaxSelection()
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.8)),
            PoolEntry(program=KBProgram(source_code="b"), eval_result=EvalResult(score=0.8)),
            PoolEntry(program=KBProgram(source_code="c"), eval_result=EvalResult(score=0.3)),
        ]
        weights = strategy.weights(entries)
        assert weights == [1.0, 1.0, 0.0]

    def test_repr(self):
        from programmaticmemory.evolution.types import MaxSelection

        strategy = MaxSelection()
        assert repr(strategy) == "MaxSelection()"


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


class TestSelectionStrategyValidation:
    def test_softmax_rejects_zero_temperature(self):
        with pytest.raises(ValueError, match="temperature must be positive"):
            SoftmaxSelection(temperature=0)

    def test_softmax_rejects_negative_temperature(self):
        with pytest.raises(ValueError, match="temperature must be positive"):
            SoftmaxSelection(temperature=-0.5)

    def test_recency_decay_rejects_zero(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        with pytest.raises(ValueError, match="decay_rate must be in"):
            RecencyDecaySelection(decay_rate=0)

    def test_recency_decay_rejects_negative(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        with pytest.raises(ValueError, match="decay_rate must be in"):
            RecencyDecaySelection(decay_rate=-0.5)

    def test_recency_decay_rejects_greater_than_one(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        with pytest.raises(ValueError, match="decay_rate must be in"):
            RecencyDecaySelection(decay_rate=1.5)

    def test_recency_decay_accepts_one(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection

        strategy = RecencyDecaySelection(decay_rate=1.0)
        assert strategy.decay_rate == 1.0


class TestProgramPoolSummary:
    def test_empty_pool(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        assert pool.summary() == "Pool: empty"

    def test_single_entry_shows_100_percent(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.5))
        summary = pool.summary()
        assert "1 programs" in summary
        assert "P=100.0%" in summary
        assert f"{p.hash}" in summary
        assert "score=0.500" in summary

    def test_multiple_entries_probabilities_sum_to_one(self):
        import re

        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.8))
        pool.add(KBProgram(source_code="b"), EvalResult(score=0.3))
        pool.add(KBProgram(source_code="c"), EvalResult(score=0.5))
        summary = pool.summary()

        probs = [float(m) for m in re.findall(r"P=([\d.]+)%", summary)]
        assert abs(sum(probs) - 100.0) < 1.0  # sum to ~100%

    def test_entries_sorted_by_score_descending(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="low"), EvalResult(score=0.2))
        pool.add(KBProgram(source_code="high"), EvalResult(score=0.9))
        pool.add(KBProgram(source_code="mid"), EvalResult(score=0.5))
        summary = pool.summary()

        lines = summary.strip().split("\n")
        scores = []
        for line in lines[1:]:  # skip header
            if "score=" in line:
                score_str = line.split("score=")[1].split()[0]
                scores.append(float(score_str))
        assert scores == sorted(scores, reverse=True)

    def test_summary_includes_strategy_repr(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="x"), EvalResult(score=0.5))
        assert "SoftmaxSelection(T=0.15)" in pool.summary()

    def test_summary_includes_generation_and_name(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="x", generation=3), EvalResult(score=0.5), name="iter_5")
        summary = pool.summary()
        assert "gen=3" in summary
        assert "programs/iter_5.py" in summary

    def test_summary_with_max_selection(self):
        """Ensure summary works with MaxSelection (non-softmax weights)."""
        from programmaticmemory.evolution.types import MaxSelection

        pool = ProgramPool(strategy=MaxSelection())
        pool.add(KBProgram(source_code="best"), EvalResult(score=0.9))
        pool.add(KBProgram(source_code="worst"), EvalResult(score=0.1))
        summary = pool.summary()
        assert "P=100.0%" in summary
        assert "P=0.0%" in summary


class TestProgramPoolEdgeCases:
    def test_add_with_custom_name(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="x"), EvalResult(score=0.5), name="iter_3")
        assert pool.entries[0].name == "iter_3"

    def test_add_default_name(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="x"), EvalResult(score=0.5))
        assert pool.entries[0].name == "seed_0"

    def test_best_raises_on_empty_pool(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        with pytest.raises(ValueError):
            _ = pool.best

    def test_sample_parent_with_multiple_entries(self):
        """Verify sample_parent delegates to strategy when pool has >1 entry."""
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.9))
        pool.add(KBProgram(source_code="b"), EvalResult(score=0.1))
        # With large score gap and low temperature, should strongly favor "a"
        results = {pool.sample_parent().program.source_code for _ in range(50)}
        assert "a" in results  # must appear at least once


class TestFindReferences:
    """Tests for ProgramPool.find_references and lineage helpers."""

    def _pool(self) -> ProgramPool:
        return ProgramPool(strategy=SoftmaxSelection(temperature=0.15))

    def test_single_entry_returns_none_none(self):
        pool = self._pool()
        seed = KBProgram(source_code="seed")
        pool.add(seed, EvalResult(score=0.5), name="seed_0")
        sibling, child_or_parent = pool.find_references(pool.entries[0])
        assert sibling is None
        assert child_or_parent is None

    def test_two_seeds_sibling_is_other_seed(self):
        pool = self._pool()
        s0 = KBProgram(source_code="seed0")
        s1 = KBProgram(source_code="seed1")
        pool.add(s0, EvalResult(score=0.3), name="seed_0")
        pool.add(s1, EvalResult(score=0.7), name="seed_1")

        sibling, child_or_parent = pool.find_references(pool.entries[0])
        assert sibling is not None
        assert sibling.program.source_code == "seed1"
        assert child_or_parent is None  # no parent/child for seeds

    def test_child_returned_over_parent_fallback(self):
        pool = self._pool()
        parent = KBProgram(source_code="parent")
        child = KBProgram(source_code="child", parent_hash=parent.hash, generation=1)
        pool.add(parent, EvalResult(score=0.5), name="seed_0")
        pool.add(child, EvalResult(score=0.6), name="iter_1")

        _, child_or_parent = pool.find_references(pool.entries[0])
        assert child_or_parent is not None
        assert child_or_parent.program.source_code == "child"

    def test_parent_fallback_when_no_children(self):
        pool = self._pool()
        parent = KBProgram(source_code="parent")
        child = KBProgram(source_code="child", parent_hash=parent.hash, generation=1)
        pool.add(parent, EvalResult(score=0.5), name="seed_0")
        pool.add(child, EvalResult(score=0.6), name="iter_1")

        # Asking for references of child — no children exist, should fallback to parent
        _, child_or_parent = pool.find_references(pool.entries[1])
        assert child_or_parent is not None
        assert child_or_parent.program.source_code == "parent"

    def test_sibling_excludes_full_lineage(self):
        """Sibling must exclude ancestors AND descendants, not just direct parent/child."""
        pool = self._pool()
        grandparent = KBProgram(source_code="gp")
        parent = KBProgram(source_code="p", parent_hash=grandparent.hash, generation=1)
        child = KBProgram(source_code="c", parent_hash=parent.hash, generation=2)
        unrelated = KBProgram(source_code="u")  # different lineage

        pool.add(grandparent, EvalResult(score=0.3), name="seed_0")
        pool.add(parent, EvalResult(score=0.5), name="iter_1")
        pool.add(child, EvalResult(score=0.7), name="iter_2")
        pool.add(unrelated, EvalResult(score=0.4), name="seed_1")

        # References for parent: lineage = {gp, parent, child}, sibling must be unrelated
        sibling, _ = pool.find_references(pool.entries[1])
        assert sibling is not None
        assert sibling.program.source_code == "u"

    def test_best_sibling_picks_highest_score(self):
        pool = self._pool()
        me = KBProgram(source_code="me")
        sib_low = KBProgram(source_code="low")
        sib_high = KBProgram(source_code="high")
        pool.add(me, EvalResult(score=0.5), name="seed_0")
        pool.add(sib_low, EvalResult(score=0.2), name="seed_1")
        pool.add(sib_high, EvalResult(score=0.9), name="seed_2")

        sibling, _ = pool.find_references(pool.entries[0])
        assert sibling is not None
        assert sibling.program.source_code == "high"
        assert sibling.score == 0.9

    def test_latest_child_is_last_added(self):
        """When multiple children exist, latest child = last added to pool."""
        pool = self._pool()
        parent = KBProgram(source_code="parent")
        child1 = KBProgram(source_code="child1", parent_hash=parent.hash, generation=1)
        child2 = KBProgram(source_code="child2", parent_hash=parent.hash, generation=1)
        pool.add(parent, EvalResult(score=0.5), name="seed_0")
        pool.add(child1, EvalResult(score=0.6), name="iter_1")
        pool.add(child2, EvalResult(score=0.4), name="iter_2")

        _, child_or_parent = pool.find_references(pool.entries[0])
        assert child_or_parent is not None
        assert child_or_parent.program.source_code == "child2"

    def test_all_in_same_lineage_returns_no_sibling(self):
        pool = self._pool()
        a = KBProgram(source_code="a")
        b = KBProgram(source_code="b", parent_hash=a.hash, generation=1)
        c = KBProgram(source_code="c", parent_hash=b.hash, generation=2)
        pool.add(a, EvalResult(score=0.3), name="seed_0")
        pool.add(b, EvalResult(score=0.5), name="iter_1")
        pool.add(c, EvalResult(score=0.7), name="iter_2")

        sibling, _ = pool.find_references(pool.entries[1])  # parent of b
        assert sibling is None  # all entries are in b's lineage
