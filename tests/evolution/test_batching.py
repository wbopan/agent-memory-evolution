"""Tests for evolution/batching.py — co-selected evaluation batching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from programmaticmemory.evolution.batching import (
    _balance_clusters,
    _embed_texts,
    _kmeans,
    _select_train_subset,
)
from programmaticmemory.evolution.types import DataItem


class TestEmbedTexts:
    def test_returns_l2_normalized_vectors(self):
        """Verify _embed_texts returns L2-normalized numpy array."""
        fake_embeddings = [[1.0, 0.0, 0.0], [0.0, 3.0, 4.0]]
        mock_response = MagicMock()
        mock_response.data = [{"embedding": e} for e in fake_embeddings]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.return_value = mock_response
            result = _embed_texts(["hello", "world"], model="test-model")

        assert result.shape == (2, 3)
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-6)
        np.testing.assert_allclose(result[1], [0.0, 0.6, 0.8], atol=1e-6)

    def test_passes_caching_true(self):
        """Verify caching=True is passed to litellm.embedding."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [1.0, 0.0]}]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.return_value = mock_response
            _embed_texts(["hello"], model="openrouter/baai/bge-m3")

        mock_litellm.embedding.assert_called_once_with(model="openrouter/baai/bge-m3", input=["hello"], caching=True)

    def test_batches_large_input(self):
        """Inputs larger than _EMBED_BATCH_SIZE are split into chunks."""
        call_count = 0

        def fake_embedding(**kwargs):
            nonlocal call_count
            call_count += 1
            resp = MagicMock()
            resp.data = [{"embedding": [1.0, 0.0]} for _ in kwargs["input"]]
            return resp

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = fake_embedding
            with patch("programmaticmemory.evolution.batching._EMBED_BATCH_SIZE", 100):
                result = _embed_texts([f"text_{i}" for i in range(150)], model="m")

        assert call_count == 2
        assert result.shape == (150, 2)


class TestKMeans:
    def test_two_clusters_on_obvious_data(self):
        """Two well-separated groups should be cleanly split."""
        vectors = np.array(
            [
                [1.0, 0.0],
                [0.95, 0.05],
                [0.9, 0.1],
                [0.0, 1.0],
                [0.05, 0.95],
                [0.1, 0.9],
            ]
        )
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels = _kmeans(vectors, k=2, seed=42)
        assert labels.shape == (6,)
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] == labels[4] == labels[5]
        assert labels[0] != labels[3]

    def test_k_equals_n(self):
        """Each point is its own cluster."""
        vectors = np.eye(3)
        labels = _kmeans(vectors, k=3, seed=42)
        assert len(set(labels)) == 3

    def test_single_cluster(self):
        """k=1 puts everything in one cluster."""
        vectors = np.array([[1.0, 0.0], [0.0, 1.0]])
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels = _kmeans(vectors, k=1, seed=42)
        assert all(label == 0 for label in labels)

    def test_deterministic_with_same_seed(self):
        rng = np.random.RandomState(123)
        vectors = rng.randn(20, 5)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels1 = _kmeans(vectors, k=3, seed=42)
        labels2 = _kmeans(vectors, k=3, seed=42)
        np.testing.assert_array_equal(labels1, labels2)


class TestFacilityLocation:
    def test_selects_most_similar_first(self):
        """First selected item should be the one with highest total similarity."""
        val_embs = np.array([[1.0, 0.0], [0.0, 1.0]])
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        train_embs = np.array(
            [
                [1.0, 0.0],
                [0.0, 1.0],
                [0.5, 0.5],
            ]
        )
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, coverage = _select_train_subset(val_embs, train_embs, budget=2)
        assert len(indices) == 2
        assert set(indices) == {0, 1}
        assert coverage > 0.99

    def test_budget_limits_selection(self):
        """Selection stops at budget even if more items available."""
        val_embs = np.array([[1.0, 0.0]])
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        train_embs = np.random.RandomState(42).randn(10, 2)
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, _ = _select_train_subset(val_embs, train_embs, budget=3)
        assert len(indices) == 3

    def test_threshold_stops_early(self):
        """With high threshold, stops before reaching budget."""
        val_embs = np.array([[1.0, 0.0]])
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        train_embs = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, _ = _select_train_subset(val_embs, train_embs, budget=3, threshold=0.5)
        assert len(indices) == 1

    def test_no_duplicates(self):
        """Selected indices should have no duplicates."""
        rng = np.random.RandomState(42)
        val_embs = rng.randn(10, 8)
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        train_embs = rng.randn(50, 8)
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, _ = _select_train_subset(val_embs, train_embs, budget=20)
        assert len(indices) == len(set(indices))

    def test_empty_val(self):
        """Empty val set returns empty selection with 0 coverage."""
        val_embs = np.zeros((0, 4))
        train_embs = np.ones((5, 4))
        indices, coverage = _select_train_subset(val_embs, train_embs, budget=3)
        assert indices == []
        assert coverage == 0.0


class TestBalanceClusters:
    def test_already_balanced(self):
        """Clusters already the right size are unchanged."""
        labels = np.array([0, 0, 1, 1])
        vectors = np.array([[1, 0], [0.9, 0.1], [0, 1], [0.1, 0.9]], dtype=np.float64)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.array([[1, 0], [0, 1]], dtype=np.float64)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        assert len(clusters) == 2
        assert all(len(c) == 2 for c in clusters)
        all_indices = sorted(idx for c in clusters for idx in c)
        assert all_indices == [0, 1, 2, 3]

    def test_oversized_cluster_trimmed(self):
        """Oversized cluster keeps items closest to centroid."""
        labels = np.array([0, 0, 0, 1])
        vectors = np.array(
            [
                [1.0, 0.0],
                [0.95, 0.05],
                [0.5, 0.5],
                [0.0, 1.0],
            ],
            dtype=np.float64,
        )
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.array([[1, 0], [0, 1]], dtype=np.float64)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        assert len(clusters[0]) == 2
        assert 2 not in clusters[0]

    def test_undersized_cluster_filled(self):
        """Undersized cluster gets filled from unassigned items."""
        labels = np.array([0, 0, 0, 1])
        vectors = np.array(
            [
                [1.0, 0.0],
                [0.95, 0.05],
                [0.5, 0.5],
                [0.0, 1.0],
            ],
            dtype=np.float64,
        )
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.array([[1, 0], [0, 1]], dtype=np.float64)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        assert len(clusters[1]) == 2
        all_indices = sorted(idx for c in clusters for idx in c)
        assert all_indices == [0, 1, 2, 3]

    def test_remainder_goes_to_last_batch(self):
        """When n % k != 0, last cluster gets the remainder."""
        labels = np.array([0, 0, 1, 1, 2])
        vectors = np.random.RandomState(42).randn(5, 3)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.random.RandomState(42).randn(3, 3)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        sizes = [len(c) for c in clusters]
        assert sum(sizes) == 5


from programmaticmemory.evolution.batching import select_representative_subset


class TestSelectRepresentativeSubset:
    def _make_data(self, n):
        return [DataItem(raw_text=f"text_{i}", question=f"q{i}?", expected_answer=f"a{i}") for i in range(n)]

    def _mock_embed(self, n, dim=4):
        """Create deterministic embeddings that form distinct clusters."""
        rng = np.random.RandomState(42)
        return rng.randn(n, dim).astype(np.float64)

    def test_returns_correct_number_of_indices(self):
        train = self._make_data(50)
        val = self._make_data(20)
        val_size = 5
        train_ratio = 3

        with patch("programmaticmemory.evolution.batching._embed_texts") as mock_embed:
            mock_embed.side_effect = [
                self._mock_embed(20),  # val embeddings
                self._mock_embed(50),  # train embeddings
            ]
            train_idx, val_idx = select_representative_subset(
                train,
                val,
                val_size=val_size,
                train_val_ratio=train_ratio,
            )

        assert len(val_idx) == val_size
        assert len(train_idx) <= train_ratio * val_size
        assert len(set(val_idx)) == val_size  # no duplicates

    def test_val_indices_are_valid(self):
        train = self._make_data(20)
        val = self._make_data(10)

        with patch("programmaticmemory.evolution.batching._embed_texts") as mock_embed:
            mock_embed.side_effect = [self._mock_embed(10), self._mock_embed(20)]
            _, val_idx = select_representative_subset(train, val, val_size=3, train_val_ratio=2)

        for idx in val_idx:
            assert 0 <= idx < 10

    def test_train_indices_are_valid(self):
        train = self._make_data(20)
        val = self._make_data(10)

        with patch("programmaticmemory.evolution.batching._embed_texts") as mock_embed:
            mock_embed.side_effect = [self._mock_embed(10), self._mock_embed(20)]
            train_idx, _ = select_representative_subset(train, val, val_size=3, train_val_ratio=2)

        for idx in train_idx:
            assert 0 <= idx < 20

    def test_val_size_exceeds_data_returns_all(self):
        """When val_size >= len(val_data), return all val indices."""
        train = self._make_data(10)
        val = self._make_data(5)

        with patch("programmaticmemory.evolution.batching._embed_texts") as mock_embed:
            mock_embed.side_effect = [self._mock_embed(5), self._mock_embed(10)]
            train_idx, val_idx = select_representative_subset(train, val, val_size=10, train_val_ratio=2)

        assert sorted(val_idx) == list(range(5))

    def test_train_embedding_failure_falls_back_to_random(self):
        """Train embedding failure should fall back to a random selection and avoid raising."""
        train = self._make_data(20)
        val = self._make_data(10)

        with patch("programmaticmemory.evolution.batching._embed_texts") as mock_embed:
            mock_embed.side_effect = [
                self._mock_embed(10),  # val embedding
                RuntimeError("embedding failed"),
            ]

            train_idx, val_idx = select_representative_subset(
                train,
                val,
                val_size=3,
                train_val_ratio=4,
            )

        assert len(val_idx) == 3
        assert len(train_idx) == 12
        assert len(set(train_idx)) == len(train_idx)
        assert all(0 <= idx < len(train) for idx in train_idx)
        assert all(0 <= idx < len(val) for idx in val_idx)
