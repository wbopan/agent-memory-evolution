"""Co-selected evaluation batching — split (train, val) into semantically aligned batches."""

from __future__ import annotations

import litellm
import numpy as np

_EMBED_BATCH_SIZE = 2048


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
        sim = vectors @ centers.T  # (n, k)
        labels = sim.argmax(axis=1)

        new_centers = np.zeros_like(centers)
        for i in range(k):
            mask = labels == i
            if mask.any():
                new_centers[i] = vectors[mask].mean(axis=0)
            else:
                new_centers[i] = vectors[rng.randint(n)]

        norms = np.linalg.norm(new_centers, axis=1, keepdims=True)
        new_centers = new_centers / np.maximum(norms, 1e-10)

        if np.allclose(centers, new_centers, atol=1e-8):
            break
        centers = new_centers

    return labels


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
    mask = np.ones(sim.shape[1], dtype=bool)

    for _ in range(min(budget, len(train_embs))):
        gains = np.maximum(sim[:, mask] - current_max[:, None], 0).sum(axis=0)
        if threshold is not None and gains.max() < threshold:
            break
        available_indices = np.where(mask)[0]
        best_local = int(gains.argmax())
        best = int(available_indices[best_local])
        selected.append(best)
        current_max = np.maximum(current_max, sim[:, best])
        mask[best] = False

    coverage = float(current_max.mean()) if selected else 0.0
    return selected, coverage
