"""Tests for evolution/batching.py — co-selected evaluation batching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

from programmaticmemory.evolution.batching import (
    EvalBatch,
    _balance_clusters,
    _embed_texts,
    _kmeans,
    _select_train_subset,
    build_eval_batches,
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


def _make_mock_litellm(dim: int = 4):
    """Create a mock litellm that returns deterministic embeddings based on text hash."""

    def fake_embedding(**kwargs):
        resp = MagicMock()
        embeddings = []
        for text in kwargs["input"]:
            rng = np.random.RandomState(hash(text) % (2**31))
            vec = rng.randn(dim)
            vec = vec / np.linalg.norm(vec)
            embeddings.append({"embedding": vec.tolist()})
        resp.data = embeddings
        return resp

    return fake_embedding


class TestBuildEvalBatches:
    def test_returns_correct_number_of_batches(self):
        train = [DataItem(raw_text=f"fact {i}", question="", expected_answer="") for i in range(20)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(10)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=2)

        assert len(batches) == 2
        assert all(isinstance(b, EvalBatch) for b in batches)

    def test_val_indices_cover_all_items(self):
        """Every val item appears in exactly one batch."""
        train = [DataItem(raw_text=f"fact {i}", question="", expected_answer="") for i in range(30)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(12)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=3)

        all_val = sorted(idx for b in batches for idx in b.val_indices)
        assert all_val == list(range(12))

    def test_train_indices_within_bounds(self):
        train = [DataItem(raw_text=f"fact {i}", question="", expected_answer="") for i in range(15)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(6)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=2)

        for b in batches:
            assert all(0 <= idx < 15 for idx in b.train_indices)

    def test_coverage_is_positive(self):
        train = [DataItem(raw_text=f"fact {i}", question="", expected_answer="") for i in range(20)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(10)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=2)

        for b in batches:
            assert b.coverage > 0.0

    def test_online_pipeline_uses_question_for_train(self):
        """When raw_text is empty, train embedding uses item.question."""
        train = [DataItem(raw_text="", question=f"train_q{i}?", expected_answer=f"a{i}") for i in range(5)]
        val = [DataItem(raw_text="", question=f"val_q{i}?", expected_answer=f"a{i}") for i in range(4)]

        captured_inputs: list[list[str]] = []

        def capturing_embedding(**kwargs):
            captured_inputs.append(kwargs["input"])
            resp = MagicMock()
            resp.data = [{"embedding": [1.0, 0.0, 0.0, 0.0]} for _ in kwargs["input"]]
            return resp

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = capturing_embedding
            build_eval_batches(train, val, num_batches=2)

        # First call should be train texts (questions since raw_text is empty)
        assert all("train_q" in t for t in captured_inputs[0])

    def test_single_batch(self):
        """num_batches=1 returns one batch with all val items."""
        train = [DataItem(raw_text=f"f{i}", question="", expected_answer="") for i in range(5)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(3)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=1)

        assert len(batches) == 1
        assert sorted(batches[0].val_indices) == [0, 1, 2]


class TestBuildEvalBatchesIntegration:
    """Integration tests using realistic DataItem patterns."""

    def test_offline_pipeline_text_extraction(self):
        """Offline pipeline: train texts come from raw_text, val from question."""
        train = [
            DataItem(raw_text="The capital of France is Paris.", question="", expected_answer=""),
            DataItem(raw_text="Water boils at 100 degrees Celsius.", question="", expected_answer=""),
            DataItem(raw_text="Jupiter is the largest planet.", question="", expected_answer=""),
            DataItem(raw_text="DNA stands for deoxyribonucleic acid.", question="", expected_answer=""),
            DataItem(raw_text="Shakespeare was born in 1564.", question="", expected_answer=""),
            DataItem(raw_text="The Nile is the longest river.", question="", expected_answer=""),
        ]
        val = [
            DataItem(raw_text="", question="What is the capital of France?", expected_answer="Paris"),
            DataItem(raw_text="", question="At what temperature does water boil?", expected_answer="100 degrees"),
            DataItem(raw_text="", question="What is the largest planet?", expected_answer="Jupiter"),
            DataItem(raw_text="", question="When was Shakespeare born?", expected_answer="1564"),
        ]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm(dim=8)
            batches = build_eval_batches(train, val, num_batches=2, train_budget_per_val=3)

        assert len(batches) == 2
        total_val = sum(len(b.val_indices) for b in batches)
        assert total_val == 4
        for b in batches:
            assert len(b.train_indices) > 0
            assert len(b.train_indices) <= 3 * len(b.val_indices)

    def test_batch_slicing_produces_valid_subsets(self):
        """Simulate what __main__.py does: slice dataset using batch indices."""
        train = [DataItem(raw_text=f"fact_{i}", question="", expected_answer="") for i in range(10)]
        val = [DataItem(raw_text="", question=f"q_{i}?", expected_answer=f"a_{i}") for i in range(6)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm()
            batches = build_eval_batches(train, val, num_batches=2)

        batch = batches[0]
        train_subset = [train[i] for i in batch.train_indices]
        val_subset = [val[i] for i in batch.val_indices]

        assert len(val_subset) > 0
        assert len(train_subset) > 0
        assert all(isinstance(item, DataItem) for item in train_subset)
        assert all(isinstance(item, DataItem) for item in val_subset)

    def test_all_batches_have_nonempty_train(self):
        """Every batch should select at least one train item."""
        train = [DataItem(raw_text=f"fact {i}", question="", expected_answer="") for i in range(50)]
        val = [DataItem(raw_text="", question=f"q{i}?", expected_answer=f"a{i}") for i in range(15)]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.side_effect = _make_mock_litellm(dim=8)
            batches = build_eval_batches(train, val, num_batches=3, train_budget_per_val=5)

        for i, b in enumerate(batches):
            assert len(b.train_indices) > 0, f"Batch {i} has no train items"
            assert len(b.val_indices) > 0, f"Batch {i} has no val items"
