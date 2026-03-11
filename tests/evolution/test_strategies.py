"""Tests for evolution/strategies.py — EvalStrategy implementations."""

from __future__ import annotations

from programmaticmemory.evolution.strategies import FullDataset
from programmaticmemory.evolution.types import (
    DataItem,
    Dataset,
    EvalResult,
    EvolutionState,
    KBProgram,
    ProgramPool,
    SoftmaxSelection,
)


def _make_dataset(n_train: int = 4, n_val: int = 4) -> Dataset:
    train = [DataItem(raw_text=f"train_{i}", question=f"tq{i}?", expected_answer=f"ta{i}") for i in range(n_train)]
    val = [DataItem(raw_text="", question=f"vq{i}?", expected_answer=f"va{i}") for i in range(n_val)]
    return Dataset(train=train, val=val, test=[])


class TestEvolutionStateFinalScores:
    def test_final_scores_default_empty(self):
        pool = ProgramPool(strategy=SoftmaxSelection())
        state = EvolutionState(pool=pool, best_score=0.0)
        assert state.final_scores == {}

    def test_final_scores_stores_values(self):
        pool = ProgramPool(strategy=SoftmaxSelection())
        state = EvolutionState(pool=pool, best_score=0.0)
        state.final_scores["abc123"] = 0.75
        assert state.final_scores["abc123"] == 0.75


class TestFullDataset:
    def test_select_returns_full_dataset(self):
        ds = _make_dataset(n_train=10, n_val=8)
        strategy = FullDataset()
        train, val = strategy.select(ds, iteration=0)
        assert len(train) == 10
        assert len(val) == 8

    def test_select_same_every_iteration(self):
        ds = _make_dataset()
        strategy = FullDataset()
        t0, v0 = strategy.select(ds, 0)
        t5, v5 = strategy.select(ds, 5)
        assert t0 == t5
        assert v0 == v5

    def test_final_candidates_returns_best(self):
        pool = ProgramPool(strategy=SoftmaxSelection())
        p1 = KBProgram(source_code="a")
        p2 = KBProgram(source_code="b")
        pool.add(p1, EvalResult(score=0.3))
        pool.add(p2, EvalResult(score=0.9))
        strategy = FullDataset()
        candidates = strategy.final_candidates(pool)
        assert len(candidates) == 1
        assert candidates[0].program == p2

    def test_final_eval_data_returns_none(self):
        ds = _make_dataset()
        strategy = FullDataset()
        assert strategy.final_eval_data(ds) is None


from programmaticmemory.evolution.batching import EvalBatch
from programmaticmemory.evolution.strategies import RotatingBatch


class TestRotatingBatch:
    def _make_batches(self):
        return [
            EvalBatch(val_indices=[0, 1], train_indices=[0, 1], coverage=0.9),
            EvalBatch(val_indices=[2, 3], train_indices=[2, 3], coverage=0.8),
        ]

    def test_select_rotates_through_batches(self):
        ds = _make_dataset(n_train=4, n_val=4)
        strategy = RotatingBatch(self._make_batches())
        t0, v0 = strategy.select(ds, 0)
        t1, v1 = strategy.select(ds, 1)
        assert [d.raw_text for d in t0] == ["train_0", "train_1"]
        assert [d.question for d in v1] == ["vq2?", "vq3?"]

    def test_select_wraps_around(self):
        ds = _make_dataset(n_train=4, n_val=4)
        strategy = RotatingBatch(self._make_batches())
        t2, v2 = strategy.select(ds, 2)
        assert [d.raw_text for d in t2] == ["train_0", "train_1"]

    def test_final_candidates_returns_top_k(self):
        pool = ProgramPool(strategy=SoftmaxSelection())
        for i, score in enumerate([0.3, 0.9, 0.7, 0.5]):
            pool.add(KBProgram(source_code=f"p{i}"), EvalResult(score=score))
        strategy = RotatingBatch(self._make_batches(), top_k=2)
        candidates = strategy.final_candidates(pool)
        assert len(candidates) == 2
        assert candidates[0].score == 0.9
        assert candidates[1].score == 0.7

    def test_final_candidates_top_k_exceeds_pool(self):
        pool = ProgramPool(strategy=SoftmaxSelection())
        pool.add(KBProgram(source_code="only"), EvalResult(score=0.5))
        strategy = RotatingBatch(self._make_batches(), top_k=5)
        candidates = strategy.final_candidates(pool)
        assert len(candidates) == 1

    def test_final_eval_data_returns_full_dataset(self):
        ds = _make_dataset()
        strategy = RotatingBatch(self._make_batches())
        result = strategy.final_eval_data(ds)
        assert result is not None
        assert len(result[0]) == 4
        assert len(result[1]) == 4


from unittest.mock import patch

from programmaticmemory.evolution.strategies import FixedRepresentative


class TestFixedRepresentative:
    def test_select_returns_subset(self):
        ds = _make_dataset(n_train=20, n_val=10)
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0, 1, 2], [0, 1])
            strategy = FixedRepresentative(ds, val_size=2, train_val_ratio=2)

        train, val = strategy.select(ds, iteration=0)
        assert len(train) == 3
        assert len(val) == 2
        assert train[0].raw_text == "train_0"
        assert val[1].question == "vq1?"

    def test_select_returns_same_every_iteration(self):
        ds = _make_dataset(n_train=20, n_val=10)
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0, 5], [3, 7])
            strategy = FixedRepresentative(ds, val_size=2)

        t0, v0 = strategy.select(ds, 0)
        t5, v5 = strategy.select(ds, 5)
        assert t0 == t5
        assert v0 == v5

    def test_constructor_calls_select_representative_subset_once(self):
        ds = _make_dataset(n_train=20, n_val=10)
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0], [0])
            FixedRepresentative(ds, val_size=3, train_val_ratio=4)
            mock_sel.assert_called_once_with(ds.train, ds.val, val_size=3, train_val_ratio=4)

    def test_final_candidates_returns_best(self):
        ds = _make_dataset()
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0], [0])
            strategy = FixedRepresentative(ds, val_size=1)

        pool = ProgramPool(strategy=SoftmaxSelection())
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.3))
        pool.add(KBProgram(source_code="b"), EvalResult(score=0.9))
        candidates = strategy.final_candidates(pool)
        assert len(candidates) == 1
        assert candidates[0].score == 0.9

    def test_final_eval_data_returns_full_dataset(self):
        ds = _make_dataset(n_train=20, n_val=10)
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0], [0])
            strategy = FixedRepresentative(ds, val_size=1)

        result = strategy.final_eval_data(ds)
        assert result is not None
        assert len(result[0]) == 20
        assert len(result[1]) == 10
