"""Tests for evolution/batching.py — co-selected evaluation batching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from programmaticmemory.evolution.batching import _embed_texts, _kmeans


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
