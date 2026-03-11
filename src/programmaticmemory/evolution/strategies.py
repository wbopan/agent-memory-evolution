"""Evaluation strategies — control data selection during evolution and final evaluation."""

from __future__ import annotations

from programmaticmemory.evolution.batching import EvalBatch
from programmaticmemory.evolution.types import DataItem, Dataset, PoolEntry, ProgramPool


class FullDataset:
    """Every iteration uses the full dataset. No final revalidation needed."""

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        return dataset.train, dataset.val

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None


class RotatingBatch:
    """Round-robin batch rotation. Scores are not directly comparable across programs.

    Use final_candidates with top_k > 1 to compensate for score incomparability.
    Final revalidation on full data produces the actual ranking.
    """

    def __init__(self, batches: list[EvalBatch], top_k: int = 3) -> None:
        self._batches = batches
        self._top_k = top_k

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        batch = self._batches[iteration % len(self._batches)]
        train = [dataset.train[i] for i in batch.train_indices]
        val = [dataset.val[i] for i in batch.val_indices]
        return train, val

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        sorted_entries = sorted(pool.entries, key=lambda e: e.score, reverse=True)
        return sorted_entries[: self._top_k]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return dataset.train, dataset.val
