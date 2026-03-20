"""Evaluation strategies — control data selection during evolution and final evaluation."""

from __future__ import annotations

import random

import numpy as np

from programmaticmemory.evolution.batching import (
    EvalBatch,
    _embed_texts,
    _kmeans,
    _select_train_subset,
    select_representative_subset,
)
from programmaticmemory.evolution.types import DataItem, Dataset, PoolEntry, ProgramPool


def _subset_train_for_eval(
    train: list[DataItem],
    eval_items: list[DataItem],
    ratio: int,
    embedding_model: str = "openrouter/baai/bge-m3",
) -> list[DataItem]:
    """Select a train subset via facility location, sized ratio * len(eval_items)."""
    budget = ratio * len(eval_items)
    if budget >= len(train):
        return train
    train_texts = [item.raw_text if item.raw_text else item.question for item in train]
    eval_texts = [item.question for item in eval_items]
    train_embs = _embed_texts(train_texts, model=embedding_model)
    eval_embs = _embed_texts(eval_texts, model=embedding_model)
    indices, _ = _select_train_subset(eval_embs, train_embs, budget=budget)
    return [train[i] for i in indices]


class NoEval:
    """Skip per-iteration evaluation entirely. Only run test evaluation.

    Use with ``--iterations 0`` for baselines where only the held-out test
    score matters and seed-eval cost should be zero.
    """

    def __init__(self, test_train_ratio: int = -1) -> None:
        self._test_train_ratio = test_train_ratio

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        return [], []

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        if not dataset.test:
            return None
        train = (
            _subset_train_for_eval(dataset.train, dataset.test, self._test_train_ratio)
            if self._test_train_ratio > 0
            else dataset.train
        )
        return train, dataset.test


class FullDataset:
    """Every iteration uses the full dataset. No final revalidation needed."""

    def __init__(self, test_train_ratio: int = -1) -> None:
        self._test_train_ratio = test_train_ratio

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        return dataset.train, dataset.val

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        if not dataset.test:
            return None
        train = (
            _subset_train_for_eval(dataset.train, dataset.test, self._test_train_ratio)
            if self._test_train_ratio > 0
            else dataset.train
        )
        return train, dataset.test

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None


class RotatingBatch:
    """Round-robin batch rotation. Scores are not directly comparable across programs.

    Use final_candidates with top_k > 1 to compensate for score incomparability.
    Final revalidation on full data produces the actual ranking.
    """

    def __init__(self, batches: list[EvalBatch], top_k: int = 3, test_train_ratio: int = -1) -> None:
        self._batches = batches
        self._top_k = top_k
        self._test_train_ratio = test_train_ratio

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
        if not dataset.test:
            return None
        train = (
            _subset_train_for_eval(dataset.train, dataset.test, self._test_train_ratio)
            if self._test_train_ratio > 0
            else dataset.train
        )
        return train, dataset.test


class FixedRepresentative:
    """Representative subset selection via clustering. Scores are comparable across programs.

    Constructor computes the subset once. All iterations use the same data.
    Final revalidation evaluates the top-1 on the full dataset.
    """

    def __init__(self, dataset: Dataset, val_size: int, train_val_ratio: int = 5, test_train_ratio: int = -1) -> None:
        self._test_train_ratio = test_train_ratio
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
        if not dataset.test:
            return None
        train = (
            _subset_train_for_eval(dataset.train, dataset.test, self._test_train_ratio)
            if self._test_train_ratio > 0
            else dataset.train
        )
        return train, dataset.test

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None

    def get_state(self) -> dict:
        return {
            "type": "FixedRepresentative",
            "train_indices": list(self._train_indices),
            "val_indices": list(self._val_indices),
            "test_train_ratio": self._test_train_ratio,
        }

    @classmethod
    def from_state(cls, state: dict, dataset: Dataset) -> FixedRepresentative:
        instance = object.__new__(cls)
        instance._train_indices = state["train_indices"]
        instance._val_indices = state["val_indices"]
        instance._test_train_ratio = state["test_train_ratio"]
        return instance


class SplitValidation:
    """Split val into static (scoring) and rotate (reflection) subsets.

    Static set is fixed (clustering-based representative subset). Rotate pool
    is sampled via k-means each iteration with a varying seed for diversity.
    Prevents reflector overfitting: reflector only sees rotate val failed_cases,
    while selection uses only static val scores.
    """

    def __init__(
        self,
        dataset: Dataset,
        static_size: int,
        rotate_size: int,
        train_val_ratio: int = -1,
        test_train_ratio: int = -1,
        embedding_model: str = "openrouter/baai/bge-m3",
    ) -> None:
        self._test_train_ratio = test_train_ratio
        self._rotate_size = rotate_size

        # Static: clustering-based representative subset (fixed)
        self._train_indices, self._static_indices = select_representative_subset(
            dataset.train,
            dataset.val,
            val_size=static_size,
            train_val_ratio=train_val_ratio,
        )

        # Rotate pool: val indices not in static
        static_set = set(self._static_indices)
        self._rotate_pool = [i for i in range(len(dataset.val)) if i not in static_set]

        # Pre-embed rotate pool for k-means sampling
        if self._rotate_pool:
            rotate_texts = [dataset.val[i].question for i in self._rotate_pool]
            try:
                self._rotate_embs = _embed_texts(rotate_texts, model=embedding_model)
            except Exception:
                from programmaticmemory.logging.logger import get_logger

                get_logger().log(
                    f"Rotate pool embedding failed, falling back to random sampling ({len(self._rotate_pool)} items)",
                    header="CONFIG",
                )
                self._rotate_embs = None
        else:
            self._rotate_embs = None

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        """Return (train, static_val) for scoring."""
        train = [dataset.train[i] for i in self._train_indices]
        val = [dataset.val[i] for i in self._static_indices]
        return train, val

    def select_reflection_val(self, dataset: Dataset, iteration: int) -> list[DataItem]:
        """Return rotate val items for reflection (k-means sampled, varies by iteration)."""
        if not self._rotate_pool:
            return []
        k = min(self._rotate_size, len(self._rotate_pool))
        if k >= len(self._rotate_pool):
            return [dataset.val[i] for i in self._rotate_pool]

        # Fallback to random sampling when embeddings are unavailable
        if self._rotate_embs is None:
            rng = random.Random(42 + iteration)
            selected = rng.sample(self._rotate_pool, k)
            return [dataset.val[i] for i in selected]

        # K-means with iteration-varying seed for diverse samples
        labels = _kmeans(self._rotate_embs, k=k, seed=42 + iteration)
        selected: list[int] = []
        for c in range(k):
            members = [j for j, label in enumerate(labels) if label == c]
            if not members:
                continue
            centroid = self._rotate_embs[members].mean(axis=0)
            norm = np.linalg.norm(centroid)
            if norm > 1e-10:
                centroid /= norm
            sims = self._rotate_embs[members] @ centroid
            selected.append(self._rotate_pool[members[int(sims.argmax())]])
        return [dataset.val[i] for i in selected]

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        return [pool.best]

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        if not dataset.test:
            return None
        train = (
            _subset_train_for_eval(dataset.train, dataset.test, self._test_train_ratio)
            if self._test_train_ratio > 0
            else dataset.train
        )
        return train, dataset.test

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        return None

    def get_state(self) -> dict:
        return {
            "type": "SplitValidation",
            "static_indices": list(self._static_indices),
            "train_indices": list(self._train_indices),
            "rotate_pool": list(self._rotate_pool),
            "rotate_size": self._rotate_size,
            "test_train_ratio": self._test_train_ratio,
        }

    @classmethod
    def from_state(cls, state: dict, dataset: Dataset) -> SplitValidation:
        """Reconstruct from saved indices, bypassing embedding API."""
        instance = object.__new__(cls)
        instance._static_indices = state["static_indices"]
        instance._train_indices = state["train_indices"]
        instance._rotate_pool = state["rotate_pool"]
        instance._rotate_size = state["rotate_size"]
        instance._test_train_ratio = state["test_train_ratio"]
        # Re-embed rotate pool (will likely hit disk cache)
        if instance._rotate_pool:
            rotate_texts = [dataset.val[i].question for i in instance._rotate_pool]
            try:
                instance._rotate_embs = _embed_texts(rotate_texts)
            except Exception:
                instance._rotate_embs = None
        else:
            instance._rotate_embs = None
        return instance
