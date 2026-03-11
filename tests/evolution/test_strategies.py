"""Tests for evolution/strategies.py — EvalStrategy implementations."""

from __future__ import annotations

from programmaticmemory.evolution.types import (
    DataItem,
    Dataset,
    EvolutionState,
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
