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
