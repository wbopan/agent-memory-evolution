"""Tests for evolution/strategies.py — EvalStrategy implementations."""

from __future__ import annotations

import numpy as np
from unittest.mock import patch

from programmaticmemory.evolution.strategies import SplitValidation
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


def test_evolution_seed_affects_rotation_sampling(mock_chromadb):
    """Different evolution seeds should produce different rotation val samples."""
    items = [DataItem(raw_text=f"text_{i}", question=f"q_{i}", expected_answer=f"a_{i}") for i in range(40)]
    dataset = Dataset(train=items[:20], val=items[20:], test=[])

    with (
        patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
        patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
    ):
        # 20 val items, 5 static → 15 rotate pool
        mock_sel.return_value = (list(range(20)), list(range(5)))
        mock_emb.return_value = np.random.randn(15, 8)
        strat_seed1 = SplitValidation(
            dataset, static_size=5, rotate_size=3, embedding_model="local", evolution_seed=1
        )
        mock_sel.return_value = (list(range(20)), list(range(5)))
        mock_emb.return_value = np.random.randn(15, 8)
        strat_seed2 = SplitValidation(
            dataset, static_size=5, rotate_size=3, embedding_model="local", evolution_seed=2
        )

    # They should differ for at least one iteration in 0..4
    any_differ = False
    for it in range(5):
        r1 = [item.question for item in strat_seed1.select_reflection_val(dataset, it)]
        r2 = [item.question for item in strat_seed2.select_reflection_val(dataset, it)]
        if r1 != r2:
            any_differ = True
            break
    assert any_differ, "Different evolution seeds should produce different rotation samples"


def test_evolution_seed_persisted_in_state(mock_chromadb):
    """evolution_seed should survive get_state/from_state round-trip."""
    items = [DataItem(raw_text=f"text_{i}", question=f"q_{i}", expected_answer=f"a_{i}") for i in range(40)]
    dataset = Dataset(train=items[:20], val=items[20:], test=[])

    with (
        patch("programmaticmemory.evolution.strategies.select_representative_subset") as mock_sel,
        patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
    ):
        mock_sel.return_value = (list(range(20)), list(range(5)))
        mock_emb.return_value = np.random.randn(15, 8)
        strat = SplitValidation(
            dataset, static_size=5, rotate_size=3, embedding_model="local", evolution_seed=99
        )

    state = strat.get_state()
    assert state["evolution_seed"] == 99

    with (
        patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
    ):
        mock_emb.return_value = np.random.randn(15, 8)
        restored = SplitValidation.from_state(state, dataset)
    assert restored._evolution_seed == 99

    # Override via from_state kwarg
    with (
        patch("programmaticmemory.evolution.strategies._embed_texts") as mock_emb,
    ):
        mock_emb.return_value = np.random.randn(15, 8)
        restored2 = SplitValidation.from_state(state, dataset, evolution_seed=7)
    assert restored2._evolution_seed == 7
