"""Tests for evolution/strategies.py — EvalStrategy implementations."""

from __future__ import annotations

import numpy as np

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
            mock_sel.assert_called_once_with(
                ds.train, ds.val, val_size=3, train_val_ratio=4, embedding_model="openrouter/baai/bge-m3"
            )

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

    def test_final_eval_data_returns_none_when_no_test(self):
        ds = _make_dataset(n_train=20, n_val=10)
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0], [0])
            strategy = FixedRepresentative(ds, val_size=1)

        result = strategy.final_eval_data(ds)
        assert result is None

    def test_final_eval_data_returns_test_when_present(self):
        ds = _make_dataset(n_train=20, n_val=10)
        ds.test = [DataItem(raw_text="", question=f"testq{i}?", expected_answer=f"testa{i}") for i in range(5)]
        with patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel:
            mock_sel.return_value = ([0], [0])
            strategy = FixedRepresentative(ds, val_size=1)

        result = strategy.final_eval_data(ds)
        assert result is not None
        assert len(result[0]) == 20
        assert len(result[1]) == 5
        assert result[1] is ds.test


from programmaticmemory.evolution.strategies import SplitValidation


class TestSplitValidation:
    def _make_large_dataset(self, n_val=30):
        """Dataset with enough val items to split into static + rotate."""
        train = [DataItem(raw_text=f"train_{i}", question=f"tq{i}?", expected_answer=f"ta{i}") for i in range(10)]
        val = [DataItem(raw_text="", question=f"vq{i}?", expected_answer=f"va{i}") for i in range(n_val)]
        return Dataset(train=train, val=val, test=[])

    def test_select_returns_static_val_only(self):
        """select() returns only static val items, not rotate items."""
        ds = self._make_large_dataset(n_val=30)
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = (list(range(10)), [0, 1, 2, 3, 4])  # 5 static val
            mock_emb.return_value = np.random.randn(25, 8)  # 30 - 5 = 25 rotate pool
            strategy = SplitValidation(ds, static_size=5, rotate_size=3)

        train, val = strategy.select(ds, iteration=0)
        assert len(val) == 5
        assert val[0].question == "vq0?"

    def test_select_returns_same_every_iteration(self):
        """Static val is fixed across iterations."""
        ds = self._make_large_dataset(n_val=30)
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = (list(range(10)), [0, 1, 2])
            mock_emb.return_value = np.random.randn(27, 8)
            strategy = SplitValidation(ds, static_size=3, rotate_size=2)

        t0, v0 = strategy.select(ds, 0)
        t5, v5 = strategy.select(ds, 5)
        assert [d.question for d in v0] == [d.question for d in v5]

    def test_select_reflection_val_returns_rotate_items(self):
        """select_reflection_val returns items from rotate pool, not static set."""
        ds = self._make_large_dataset(n_val=30)
        static_indices = [0, 1, 2, 3, 4]
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = (list(range(10)), static_indices)
            mock_emb.return_value = np.random.randn(25, 8)
            strategy = SplitValidation(ds, static_size=5, rotate_size=3)

        reflect_val = strategy.select_reflection_val(ds, iteration=0)
        assert len(reflect_val) == 3
        # None of the reflect val items should be in the static set
        static_questions = {ds.val[i].question for i in static_indices}
        for item in reflect_val:
            assert item.question not in static_questions

    def test_select_reflection_val_varies_by_iteration(self):
        """Different iterations should (usually) get different rotate samples."""
        ds = self._make_large_dataset(n_val=30)
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = (list(range(10)), [0, 1, 2])
            mock_emb.return_value = np.random.randn(27, 8)
            strategy = SplitValidation(ds, static_size=3, rotate_size=3)

        r0 = [d.question for d in strategy.select_reflection_val(ds, 0)]
        r1 = [d.question for d in strategy.select_reflection_val(ds, 1)]
        # With 27 items choosing 3, different seeds should give different selections
        # (not guaranteed but extremely likely)
        assert r0 != r1

    def test_no_overlap_between_static_and_rotate_pool(self):
        """Static and rotate pools are disjoint."""
        ds = self._make_large_dataset(n_val=20)
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            static_indices = [0, 3, 7, 10, 15]
            mock_sel.return_value = (list(range(10)), static_indices)
            mock_emb.return_value = np.random.randn(15, 8)
            strategy = SplitValidation(ds, static_size=5, rotate_size=3)

        _, static_val = strategy.select(ds, 0)
        reflect_val = strategy.select_reflection_val(ds, 0)
        static_qs = {d.question for d in static_val}
        reflect_qs = {d.question for d in reflect_val}
        assert static_qs.isdisjoint(reflect_qs)

    def test_final_candidates_returns_best(self):
        ds = self._make_large_dataset()
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = ([0], [0])
            mock_emb.return_value = np.random.randn(29, 8)
            strategy = SplitValidation(ds, static_size=1, rotate_size=2)

        pool = ProgramPool(strategy=SoftmaxSelection())
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.3))
        pool.add(KBProgram(source_code="b"), EvalResult(score=0.9))
        candidates = strategy.final_candidates(pool)
        assert len(candidates) == 1
        assert candidates[0].score == 0.9

    def test_final_eval_data_returns_test(self):
        ds = self._make_large_dataset()
        ds.test = [DataItem(raw_text="", question="tq?", expected_answer="ta")]
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = ([0], [0])
            mock_emb.return_value = np.random.randn(29, 8)
            strategy = SplitValidation(ds, static_size=1, rotate_size=2)

        result = strategy.final_eval_data(ds)
        assert result is not None
        assert len(result[1]) == 1

    def test_final_eval_data_returns_none_when_no_test(self):
        ds = self._make_large_dataset()
        with (
            patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
            patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
        ):
            mock_sel.return_value = ([0], [0])
            mock_emb.return_value = np.random.randn(29, 8)
            strategy = SplitValidation(ds, static_size=1, rotate_size=2)

        assert strategy.final_eval_data(ds) is None
