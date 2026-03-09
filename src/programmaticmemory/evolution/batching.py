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
