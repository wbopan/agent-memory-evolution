"""Evaluation strategies — control data selection during evolution and final evaluation."""

from __future__ import annotations

from programmaticmemory.evolution.batching import EvalBatch, select_representative_subset
from programmaticmemory.evolution.types import DataItem, Dataset, PoolEntry, ProgramPool


class FullDataset:
    """Every iteration uses the full dataset. No final revalidation needed."""

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        return dataset.train, dataset.val

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return (dataset.train, dataset.test) if dataset.test else None

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
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

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return (dataset.train, dataset.test) if dataset.test else None


class FixedRepresentative:
    """Representative subset selection via clustering. Scores are comparable across programs.

    Constructor computes the subset once. All iterations use the same data.
    Final revalidation evaluates the top-1 on the full dataset.
    """

    def __init__(self, dataset: Dataset, val_size: int, train_val_ratio: int = 5) -> None:
        self._train_indices, self._val_indices = select_representative_subset(
            dataset.train,
            dataset.val,
            val_size=val_size,
            train_val_ratio=train_val_ratio,
        )

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        train = [dataset.train[i] for i in self._train_indices]
        val = [dataset.val[i] for i in self._val_indices]
        return train, val

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return (dataset.train, dataset.test) if dataset.test else None

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None
