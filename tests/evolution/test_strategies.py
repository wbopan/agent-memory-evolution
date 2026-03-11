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
