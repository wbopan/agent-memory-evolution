# Co-Selected Evaluation Batching Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a preprocessing step that splits (train, val) into semantically aligned batches using embedding similarity, so each evolution run can use a cheaper subset instead of the full dataset.

**Architecture:** New `batching.py` module with pure functions. K-means clusters val questions, facility location greedy selects matching train items per cluster. Integrated into `__main__.py` via two new CLI flags. Loop and evaluator unchanged.

**Tech Stack:** litellm (embeddings API), numpy (matrix ops), existing logger

---

### Task 1: Embedding helper + tests

**Files:**
- Create: `src/programmaticmemory/evolution/batching.py`
- Create: `tests/evolution/test_batching.py`

**Step 1: Write the failing test**

```python
# tests/evolution/test_batching.py
"""Tests for evolution/batching.py — co-selected evaluation batching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from programmaticmemory.evolution.batching import _embed_texts


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
        # Check L2 normalization
        norms = np.linalg.norm(result, axis=1)
        np.testing.assert_allclose(norms, [1.0, 1.0], atol=1e-6)
        # First vector was already unit, second should be [0, 0.6, 0.8]
        np.testing.assert_allclose(result[1], [0.0, 0.6, 0.8], atol=1e-6)

    def test_passes_caching_true(self):
        """Verify caching=True is passed to litellm.embedding."""
        mock_response = MagicMock()
        mock_response.data = [{"embedding": [1.0, 0.0]}]

        with patch("programmaticmemory.evolution.batching.litellm") as mock_litellm:
            mock_litellm.embedding.return_value = mock_response
            _embed_texts(["hello"], model="openrouter/baai/bge-m3")

        mock_litellm.embedding.assert_called_once_with(
            model="openrouter/baai/bge-m3", input=["hello"], caching=True
        )

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
            # 150 texts with batch size 100 should make 2 calls
            with patch("programmaticmemory.evolution.batching._EMBED_BATCH_SIZE", 100):
                result = _embed_texts([f"text_{i}" for i in range(150)], model="m")

        assert call_count == 2
        assert result.shape == (150, 2)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_batching.py::TestEmbedTexts -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'programmaticmemory.evolution.batching'`

**Step 3: Write minimal implementation**

```python
# src/programmaticmemory/evolution/batching.py
"""Co-selected evaluation batching — split (train, val) into semantically aligned batches."""

from __future__ import annotations

import litellm
import numpy as np

_EMBED_BATCH_SIZE = 2048  # max texts per litellm.embedding call


def _embed_texts(texts: list[str], model: str) -> np.ndarray:
    """Encode texts via litellm embedding API. Returns L2-normalized vectors."""
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        chunk = texts[start : start + _EMBED_BATCH_SIZE]
        response = litellm.embedding(model=model, input=chunk, caching=True)
        all_embeddings.extend(d["embedding"] for d in response.data)
    vectors = np.array(all_embeddings, dtype=np.float64)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-10)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_batching.py::TestEmbedTexts -v`
Expected: PASS (3 tests)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py
git commit -m "feat(batching): add embedding helper with litellm API"
```

---

### Task 2: K-means implementation + tests

**Files:**
- Modify: `src/programmaticmemory/evolution/batching.py`
- Modify: `tests/evolution/test_batching.py`

**Step 1: Write the failing test**

Append to `tests/evolution/test_batching.py`:

```python
from programmaticmemory.evolution.batching import _kmeans


class TestKMeans:
    def test_two_clusters_on_obvious_data(self):
        """Two well-separated groups should be cleanly split."""
        # Cluster A: near [1, 0], Cluster B: near [0, 1]
        vectors = np.array([
            [1.0, 0.0],
            [0.95, 0.05],
            [0.9, 0.1],
            [0.0, 1.0],
            [0.05, 0.95],
            [0.1, 0.9],
        ])
        # L2 normalize
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels = _kmeans(vectors, k=2, seed=42)
        assert labels.shape == (6,)
        # First 3 should share a label, last 3 another
        assert labels[0] == labels[1] == labels[2]
        assert labels[3] == labels[4] == labels[5]
        assert labels[0] != labels[3]

    def test_k_equals_n(self):
        """Each point is its own cluster."""
        vectors = np.eye(3)  # already unit vectors
        labels = _kmeans(vectors, k=3, seed=42)
        assert len(set(labels)) == 3

    def test_single_cluster(self):
        """k=1 puts everything in one cluster."""
        vectors = np.array([[1.0, 0.0], [0.0, 1.0]])
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels = _kmeans(vectors, k=1, seed=42)
        assert all(l == 0 for l in labels)

    def test_deterministic_with_same_seed(self):
        rng = np.random.RandomState(123)
        vectors = rng.randn(20, 5)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        labels1 = _kmeans(vectors, k=3, seed=42)
        labels2 = _kmeans(vectors, k=3, seed=42)
        np.testing.assert_array_equal(labels1, labels2)
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_batching.py::TestKMeans -v`
Expected: FAIL — `ImportError: cannot import name '_kmeans'`

**Step 3: Write minimal implementation**

Add to `batching.py`:

```python
def _kmeans(vectors: np.ndarray, k: int, max_iter: int = 50, seed: int = 42) -> np.ndarray:
    """Lloyd's algorithm with cosine distance on L2-normalized vectors.

    Args:
        vectors: (n, d) L2-normalized array.
        k: Number of clusters.

    Returns:
        labels: (n,) integer cluster assignments.
    """
    n = len(vectors)
    rng = np.random.RandomState(seed)
    indices = rng.choice(n, k, replace=n < k)
    centers = vectors[indices].copy()

    for _ in range(max_iter):
        # Cosine similarity = dot product for L2-normalized vectors
        sim = vectors @ centers.T  # (n, k)
        labels = sim.argmax(axis=1)

        new_centers = np.zeros_like(centers)
        for i in range(k):
            mask = labels == i
            if mask.any():
                new_centers[i] = vectors[mask].mean(axis=0)
            else:
                # Empty cluster: re-seed from a random point
                new_centers[i] = vectors[rng.randint(n)]

        # Re-normalize for cosine distance
        norms = np.linalg.norm(new_centers, axis=1, keepdims=True)
        new_centers = new_centers / np.maximum(norms, 1e-10)

        if np.allclose(centers, new_centers, atol=1e-8):
            break
        centers = new_centers

    return labels
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_batching.py::TestKMeans -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py
git commit -m "feat(batching): add K-means with cosine distance"
```

---

### Task 3: Facility location greedy + tests

**Files:**
- Modify: `src/programmaticmemory/evolution/batching.py`
- Modify: `tests/evolution/test_batching.py`

**Step 1: Write the failing test**

Append to `tests/evolution/test_batching.py`:

```python
from programmaticmemory.evolution.batching import _select_train_subset


class TestFacilityLocation:
    def test_selects_most_similar_first(self):
        """First selected item should be the one with highest total similarity."""
        val_embs = np.array([[1.0, 0.0], [0.0, 1.0]])  # two orthogonal queries
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        train_embs = np.array([
            [1.0, 0.0],   # perfect match for query 0
            [0.0, 1.0],   # perfect match for query 1
            [0.5, 0.5],   # decent match for both
        ])
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, coverage = _select_train_subset(val_embs, train_embs, budget=2)
        assert len(indices) == 2
        # Should select the two perfect matches (indices 0 and 1)
        assert set(indices) == {0, 1}
        assert coverage > 0.99  # near-perfect coverage

    def test_budget_limits_selection(self):
        """Selection stops at budget even if more items available."""
        val_embs = np.array([[1.0, 0.0]])
        train_embs = np.eye(5)[:, :2]  # 5 train items, 2 dims
        # Normalize
        train_embs = train_embs / np.maximum(np.linalg.norm(train_embs, axis=1, keepdims=True), 1e-10)
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)

        indices, _ = _select_train_subset(val_embs, train_embs, budget=3)
        assert len(indices) == 3

    def test_threshold_stops_early(self):
        """With high threshold, stops before reaching budget."""
        val_embs = np.array([[1.0, 0.0]])
        val_embs = val_embs / np.linalg.norm(val_embs, axis=1, keepdims=True)
        # One perfect match, rest are orthogonal
        train_embs = np.array([[1.0, 0.0], [0.0, 1.0], [0.0, 1.0]])
        train_embs = train_embs / np.linalg.norm(train_embs, axis=1, keepdims=True)

        indices, _ = _select_train_subset(val_embs, train_embs, budget=3, threshold=0.5)
        # After selecting index 0 (perfect match), remaining marginal gains are ~0
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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_batching.py::TestFacilityLocation -v`
Expected: FAIL — `ImportError: cannot import name '_select_train_subset'`

**Step 3: Write minimal implementation**

Add to `batching.py`:

```python
def _select_train_subset(
    val_embs: np.ndarray,
    train_embs: np.ndarray,
    budget: int,
    threshold: float | None = None,
) -> tuple[list[int], float]:
    """Greedy facility location: select train items maximizing coverage of val items.

    Args:
        val_embs: (m, d) L2-normalized val embeddings.
        train_embs: (N, d) L2-normalized train embeddings.
        budget: Max number of train items to select.
        threshold: Stop when marginal gain falls below this.

    Returns:
        (selected_indices, coverage) where coverage = mean max similarity.
    """
    if len(val_embs) == 0:
        return [], 0.0

    sim = val_embs @ train_embs.T  # (m, N)
    current_max = np.full(len(val_embs), -np.inf)
    selected: list[int] = []
    mask = np.ones(sim.shape[1], dtype=bool)  # available train items

    for _ in range(min(budget, len(train_embs))):
        gains = np.maximum(sim[:, mask] - current_max[:, None], 0).sum(axis=0)
        if threshold is not None and gains.max() < threshold:
            break
        # Map back to original index
        available_indices = np.where(mask)[0]
        best_local = int(gains.argmax())
        best = int(available_indices[best_local])
        selected.append(best)
        current_max = np.maximum(current_max, sim[:, best])
        mask[best] = False

    coverage = float(current_max.mean()) if selected else 0.0
    return selected, coverage
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_batching.py::TestFacilityLocation -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py
git commit -m "feat(batching): add facility location greedy selection"
```

---

### Task 4: Cluster balancing + tests

**Files:**
- Modify: `src/programmaticmemory/evolution/batching.py`
- Modify: `tests/evolution/test_batching.py`

**Step 1: Write the failing test**

Append to `tests/evolution/test_batching.py`:

```python
from programmaticmemory.evolution.batching import _balance_clusters


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
        # All original indices should be present exactly once
        all_indices = sorted(idx for c in clusters for idx in c)
        assert all_indices == [0, 1, 2, 3]

    def test_oversized_cluster_trimmed(self):
        """Oversized cluster keeps items closest to centroid."""
        # Cluster 0 has 3 items, target is 2 — drop the farthest
        labels = np.array([0, 0, 0, 1])
        vectors = np.array([
            [1.0, 0.0],    # close to center
            [0.95, 0.05],  # close to center
            [0.5, 0.5],    # far from center
            [0.0, 1.0],
        ], dtype=np.float64)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.array([[1, 0], [0, 1]], dtype=np.float64)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        # Cluster 0 should keep indices 0, 1 (closest to [1, 0])
        assert len(clusters[0]) == 2
        assert 2 not in clusters[0]  # index 2 dropped from cluster 0

    def test_undersized_cluster_filled(self):
        """Undersized cluster gets filled from unassigned items."""
        labels = np.array([0, 0, 0, 1])
        vectors = np.array([
            [1.0, 0.0],
            [0.95, 0.05],
            [0.5, 0.5],    # this gets bumped from cluster 0 to cluster 1
            [0.0, 1.0],
        ], dtype=np.float64)
        vectors = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
        centers = np.array([[1, 0], [0, 1]], dtype=np.float64)
        centers = centers / np.linalg.norm(centers, axis=1, keepdims=True)

        clusters = _balance_clusters(labels, vectors, centers, target_size=2)
        assert len(clusters[1]) == 2
        # Total items should be preserved
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
        # Last cluster gets remainder
        assert sizes[-1] == 1 or sizes[-1] == 2  # flexible, just no items lost
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_batching.py::TestBalanceClusters -v`
Expected: FAIL — `ImportError: cannot import name '_balance_clusters'`

**Step 3: Write minimal implementation**

Add to `batching.py`:

```python
def _balance_clusters(
    labels: np.ndarray,
    vectors: np.ndarray,
    centers: np.ndarray,
    target_size: int,
) -> list[list[int]]:
    """Balance K-means clusters to target_size items each.

    Oversized clusters keep items closest to centroid.
    Undersized clusters fill from unassigned pool (nearest to centroid).
    Last cluster absorbs remainder when n % k != 0.

    Returns:
        List of K lists of original indices.
    """
    k = len(centers)
    # Sort each cluster by distance to its centroid (closest first)
    raw_clusters: list[list[int]] = [[] for _ in range(k)]
    for idx, label in enumerate(labels):
        raw_clusters[label].append(idx)

    for i in range(k):
        members = raw_clusters[i]
        if len(members) > 1:
            sims = vectors[members] @ centers[i]
            order = np.argsort(-sims)  # highest similarity first
            raw_clusters[i] = [members[j] for j in order]

    # Phase 1: trim oversized clusters, collect surplus into pool
    balanced: list[list[int]] = [[] for _ in range(k)]
    pool: list[int] = []
    for i in range(k):
        if i == k - 1:
            # Last cluster: takes whatever is left after balancing
            balanced[i] = raw_clusters[i]
        elif len(raw_clusters[i]) > target_size:
            balanced[i] = raw_clusters[i][:target_size]
            pool.extend(raw_clusters[i][target_size:])
        else:
            balanced[i] = raw_clusters[i]

    # Phase 2: fill undersized clusters from pool
    for i in range(k - 1):  # skip last (absorbs remainder)
        deficit = target_size - len(balanced[i])
        if deficit > 0 and pool:
            # Pick pool items closest to this cluster's centroid
            pool_sims = vectors[pool] @ centers[i]
            order = np.argsort(-pool_sims)
            fill_count = min(deficit, len(pool))
            fill_indices = [pool[order[j]] for j in range(fill_count)]
            balanced[i].extend(fill_indices)
            pool = [p for p in pool if p not in set(fill_indices)]

    # Remaining pool items go to last cluster
    balanced[k - 1] = balanced[k - 1] + pool

    return balanced
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_batching.py::TestBalanceClusters -v`
Expected: PASS (4 tests)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py
git commit -m "feat(batching): add cluster balancing for even batch sizes"
```

---

### Task 5: Main `build_eval_batches` function + tests

**Files:**
- Modify: `src/programmaticmemory/evolution/batching.py`
- Modify: `tests/evolution/test_batching.py`

**Step 1: Write the failing test**

Append to `tests/evolution/test_batching.py`:

```python
from programmaticmemory.evolution.batching import EvalBatch, build_eval_batches
from programmaticmemory.evolution.types import DataItem


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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_batching.py::TestBuildEvalBatches -v`
Expected: FAIL — `ImportError: cannot import name 'EvalBatch'`

**Step 3: Write minimal implementation**

Add to `batching.py` at the top (after imports):

```python
from dataclasses import dataclass

from programmaticmemory.evolution.types import DataItem
from programmaticmemory.logging.logger import get_logger


@dataclass
class EvalBatch:
    """A co-selected evaluation batch with val and train subsets."""

    val_indices: list[int]
    train_indices: list[int]
    coverage: float
```

And the main function:

```python
def build_eval_batches(
    train_data: list[DataItem],
    val_data: list[DataItem],
    num_batches: int = 10,
    train_budget_per_val: int = 5,
    coverage_threshold: float | None = None,
    embedding_model: str = "openrouter/baai/bge-m3",
) -> list[EvalBatch]:
    """Build K co-selected evaluation batches.

    Clusters val questions, then greedily selects train items for each cluster
    using facility location to maximize coverage.

    Args:
        train_data: Full training set.
        val_data: Full validation set.
        num_batches: Number of batches (K).
        train_budget_per_val: Train items per val item (|D_k| = this * |Q_k|).
        coverage_threshold: Stop facility location when marginal gain < this.
        embedding_model: litellm model string for embeddings.

    Returns:
        List of K EvalBatch objects.
    """
    logger = get_logger()
    k = num_batches
    target_m = len(val_data) // k

    logger.log(
        f"Building {k} eval batches: train={len(train_data)}, val={len(val_data)}, "
        f"target_val_per_batch={target_m}, train_budget_per_val={train_budget_per_val}, "
        f"model={embedding_model}",
        header="BATCH",
    )

    # Step 1: Embed all texts
    # Train: use raw_text if available (offline pipeline), else question (online pipeline)
    train_texts = [
        item.raw_text if item.raw_text else item.question
        for item in train_data
    ]
    val_texts = [item.question for item in val_data]

    logger.log(f"Embedding {len(train_texts)} train texts...", header="BATCH")
    train_embs = _embed_texts(train_texts, model=embedding_model)
    logger.log(f"Embedding {len(val_texts)} val texts...", header="BATCH")
    val_embs = _embed_texts(val_texts, model=embedding_model)
    logger.log(
        f"Embeddings complete: train={train_embs.shape}, val={val_embs.shape}",
        header="BATCH",
    )

    # Step 2: Cluster val embeddings
    if k == 1:
        clusters = [list(range(len(val_data)))]
    else:
        labels = _kmeans(val_embs, k=k)
        # Log raw cluster sizes
        raw_sizes = [int((labels == i).sum()) for i in range(k)]
        logger.log(f"K-means raw cluster sizes: {raw_sizes}", header="BATCH")

        # Compute centers for balancing
        centers = np.zeros((k, val_embs.shape[1]))
        for i in range(k):
            mask = labels == i
            if mask.any():
                centers[i] = val_embs[mask].mean(axis=0)
        norms = np.linalg.norm(centers, axis=1, keepdims=True)
        centers = centers / np.maximum(norms, 1e-10)

        clusters = _balance_clusters(labels, val_embs, centers, target_size=target_m)
        balanced_sizes = [len(c) for c in clusters]
        logger.log(f"Balanced cluster sizes: {balanced_sizes}", header="BATCH")

    # Step 3: Facility location for each cluster
    batches: list[EvalBatch] = []
    for i, val_indices in enumerate(clusters):
        budget = train_budget_per_val * len(val_indices)
        cluster_val_embs = val_embs[val_indices]
        train_indices, coverage = _select_train_subset(
            cluster_val_embs, train_embs, budget=budget, threshold=coverage_threshold
        )
        logger.log(
            f"Batch {i}: val={len(val_indices)}, train={len(train_indices)}, coverage={coverage:.4f}",
            header="BATCH",
        )
        batches.append(EvalBatch(
            val_indices=val_indices,
            train_indices=train_indices,
            coverage=coverage,
        ))

    # Step 4: Quality summary
    coverages = [b.coverage for b in batches]
    mean_cov = np.mean(coverages)
    std_cov = np.std(coverages)
    logger.log(
        f"Coverage summary: min={min(coverages):.4f}, max={max(coverages):.4f}, "
        f"mean={mean_cov:.4f}, std={std_cov:.4f}",
        header="BATCH",
    )
    for i, b in enumerate(batches):
        if std_cov > 0 and b.coverage < mean_cov - 2 * std_cov:
            logger.log(f"WARNING: Batch {i} coverage {b.coverage:.4f} is >2 std below mean", header="BATCH")

    return batches
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_batching.py::TestBuildEvalBatches -v`
Expected: PASS (6 tests)

**Step 5: Run all batching tests together**

Run: `uv run pytest tests/evolution/test_batching.py -v`
Expected: PASS (all ~19 tests)

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py
git commit -m "feat(batching): add build_eval_batches with logging"
```

---

### Task 6: CLI integration in `__main__.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py:51-109`

**Step 1: Add CLI arguments**

After line 95 (`parser.add_argument("--seed-dir", ...)`), add:

```python
    parser.add_argument("--num-batches", type=int, default=0, help="Split dataset into K eval batches (0 = disabled)")
    parser.add_argument("--batch-index", type=int, default=0, help="Which batch to use (0-indexed, requires --num-batches)")
```

**Step 2: Add batching logic after dataset loading**

After line 109 (`dataset = load_dataset(...)`) and before line 111 (`from programmaticmemory.logging.logger import ...`), insert:

```python
    # Apply co-selected batching if requested
    if args.num_batches > 0:
        if args.batch_index >= args.num_batches:
            print(
                f"Error: --batch-index {args.batch_index} must be < --num-batches {args.num_batches}",
                file=sys.stderr,
            )
            sys.exit(1)
        from programmaticmemory.evolution.batching import build_eval_batches

        batches = build_eval_batches(
            dataset.train,
            dataset.val,
            num_batches=args.num_batches,
        )
        batch = batches[args.batch_index]
        dataset.train = [dataset.train[i] for i in batch.train_indices]
        dataset.val = [dataset.val[i] for i in batch.val_indices]
```

**Step 3: Add batch info to the logger output**

After the existing `logger.log(f"Dataset=...", header="CONFIG")` block (~line 122-125), add a conditional log:

```python
    if args.num_batches > 0:
        logger.log(
            f"Batch {args.batch_index}/{args.num_batches}: "
            f"train={len(dataset.train)}, val={len(dataset.val)}, "
            f"coverage={batches[args.batch_index].coverage:.4f}",
            header="CONFIG",
        )
```

Note: the `batches` variable needs to be accessible. Move the batching block after the logger setup, or store `batch` info. Simplest: move the batch logging into the batching block itself and store coverage for later:

Actually, the logger isn't set up yet at that point. Better approach: store the batch info and log it after logger setup. Revise step 2 to:

```python
    # Apply co-selected batching if requested
    _batch_info = None
    if args.num_batches > 0:
        if args.batch_index >= args.num_batches:
            print(
                f"Error: --batch-index {args.batch_index} must be < --num-batches {args.num_batches}",
                file=sys.stderr,
            )
            sys.exit(1)
        from programmaticmemory.evolution.batching import build_eval_batches

        batches = build_eval_batches(
            dataset.train,
            dataset.val,
            num_batches=args.num_batches,
        )
        batch = batches[args.batch_index]
        _batch_info = {
            "index": args.batch_index,
            "total": args.num_batches,
            "train_size": len(batch.train_indices),
            "val_size": len(batch.val_indices),
            "coverage": batch.coverage,
        }
        dataset.train = [dataset.train[i] for i in batch.train_indices]
        dataset.val = [dataset.val[i] for i in batch.val_indices]
```

Then after the logger CONFIG block:

```python
    if _batch_info:
        logger.log(
            f"Using batch {_batch_info['index']}/{_batch_info['total']}: "
            f"train={_batch_info['train_size']}, val={_batch_info['val_size']}, "
            f"coverage={_batch_info['coverage']:.4f}",
            header="CONFIG",
        )
```

**Step 4: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/__main__.py`
Expected: No errors

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat(batching): add --num-batches/--batch-index CLI flags"
```

---

### Task 7: End-to-end smoke test

**Files:**
- Modify: `tests/evolution/test_batching.py`

**Step 1: Write integration test**

Append to `tests/evolution/test_batching.py`:

```python
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
        # Each batch should have ~2 val items
        total_val = sum(len(b.val_indices) for b in batches)
        assert total_val == 4
        # Each batch should have train items
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

        # Simulate __main__.py slicing
        batch = batches[0]
        train_subset = [train[i] for i in batch.train_indices]
        val_subset = [val[i] for i in batch.val_indices]

        assert len(val_subset) > 0
        assert len(train_subset) > 0
        # All items should be valid DataItem instances
        assert all(isinstance(item, DataItem) for item in train_subset)
        assert all(isinstance(item, DataItem) for item in val_subset)
```

**Step 2: Run all tests**

Run: `uv run pytest tests/evolution/test_batching.py -v`
Expected: PASS (all tests)

**Step 3: Run full test suite to check no regressions**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: PASS (all existing tests still pass)

**Step 4: Lint**

Run: `uv run ruff check src/programmaticmemory/evolution/batching.py && uv run ruff format src/programmaticmemory/evolution/batching.py`
Expected: Clean

**Step 5: Commit**

```bash
git add tests/evolution/test_batching.py
git commit -m "test(batching): add integration tests for eval batching"
```

---

### Task 8: Final assembly and lint pass

**Step 1: Verify final file structure**

Run: `ls src/programmaticmemory/evolution/batching.py tests/evolution/test_batching.py`
Expected: Both files exist

**Step 2: Full lint pass**

Run: `uv run ruff check src/programmaticmemory/evolution/ && uv run ruff format --check src/programmaticmemory/evolution/`
Expected: Clean

**Step 3: Full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All pass

**Step 4: Commit (if any lint fixes)**

```bash
git add -u
git commit -m "chore: lint fixes for batching module"
```
