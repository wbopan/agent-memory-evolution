"""Co-selected evaluation batching — split (train, val) into semantically aligned batches."""

from __future__ import annotations

import time
from dataclasses import dataclass

import litellm
import numpy as np

from programmaticmemory.evolution.types import DataItem
from programmaticmemory.logging.logger import get_logger

_EMBED_BATCH_SIZE = 64
_EMBED_MAX_RETRIES = 3


def _embed_texts(texts: list[str], model: str) -> np.ndarray:
    """Encode texts via litellm embedding API. Returns L2-normalized vectors."""
    all_embeddings: list[list[float]] = []
    for start in range(0, len(texts), _EMBED_BATCH_SIZE):
        chunk = texts[start : start + _EMBED_BATCH_SIZE]
        for attempt in range(_EMBED_MAX_RETRIES):
            try:
                response = litellm.embedding(model=model, input=chunk, caching=True)
                break
            except Exception:
                if attempt == _EMBED_MAX_RETRIES - 1:
                    raise
                time.sleep(2**attempt)
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

    # Sort each cluster by similarity to centroid (highest first)
    raw_clusters: list[list[int]] = [[] for _ in range(k)]
    for idx, label in enumerate(labels):
        raw_clusters[label].append(idx)

    for i in range(k):
        members = raw_clusters[i]
        if len(members) > 1:
            sims = vectors[members] @ centers[i]
            order = np.argsort(-sims)
            raw_clusters[i] = [members[j] for j in order]

    # Phase 1: trim oversized, collect surplus
    balanced: list[list[int]] = [[] for _ in range(k)]
    pool: list[int] = []
    for i in range(k):
        if i == k - 1:
            balanced[i] = raw_clusters[i]
        elif len(raw_clusters[i]) > target_size:
            balanced[i] = raw_clusters[i][:target_size]
            pool.extend(raw_clusters[i][target_size:])
        else:
            balanced[i] = raw_clusters[i]

    # Phase 2: fill undersized from pool
    for i in range(k - 1):
        deficit = target_size - len(balanced[i])
        if deficit > 0 and pool:
            pool_sims = vectors[pool] @ centers[i]
            order = np.argsort(-pool_sims)
            fill_count = min(deficit, len(pool))
            fill_indices = [pool[order[j]] for j in range(fill_count)]
            balanced[i].extend(fill_indices)
            fill_set = set(fill_indices)
            pool = [p for p in pool if p not in fill_set]

    # Remaining pool to last cluster
    balanced[k - 1] = balanced[k - 1] + pool

    return balanced


@dataclass
class EvalBatch:
    """A co-selected evaluation batch with val and train subsets."""

    val_indices: list[int]
    train_indices: list[int]
    coverage: float


def build_eval_batches(
    train_data: list[DataItem],
    val_data: list[DataItem],
    num_batches: int = 10,
    batch_train_val_ratio: int = 5,
    coverage_threshold: float | None = None,
    embedding_model: str = "openrouter/baai/bge-m3",
) -> list[EvalBatch]:
    """Build K co-selected evaluation batches.

    Clusters val questions, then greedily selects train items for each cluster
    using facility location to maximize coverage.
    """
    logger = get_logger()
    k = num_batches
    target_m = len(val_data) // k

    logger.log(
        f"Building {k} eval batches: train={len(train_data)}, val={len(val_data)}, "
        f"target_val_per_batch={target_m}, batch_train_val_ratio={batch_train_val_ratio}, "
        f"model={embedding_model}",
        header="BATCH",
    )

    # Step 1: Embed all texts
    train_texts = [item.raw_text if item.raw_text else item.question for item in train_data]
    val_texts = [item.question for item in val_data]

    logger.log(f"Embedding {len(train_texts)} train texts...", header="BATCH")
    train_embs = _embed_texts(train_texts, model=embedding_model)
    logger.log(f"Embedding {len(val_texts)} val texts...", header="BATCH")
    val_embs = _embed_texts(val_texts, model=embedding_model)
    logger.log(f"Embeddings complete: train={train_embs.shape}, val={val_embs.shape}", header="BATCH")

    # Step 2: Cluster val embeddings
    if k == 1:
        clusters = [list(range(len(val_data)))]
    else:
        labels = _kmeans(val_embs, k=k)
        raw_sizes = [int((labels == i).sum()) for i in range(k)]
        logger.log(f"K-means raw cluster sizes: {raw_sizes}", header="BATCH")

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
        budget = len(train_data) if batch_train_val_ratio < 0 else batch_train_val_ratio * len(val_indices)
        cluster_val_embs = val_embs[val_indices]
        train_indices, coverage = _select_train_subset(
            cluster_val_embs, train_embs, budget=budget, threshold=coverage_threshold
        )
        logger.log(
            f"Batch {i}: val={len(val_indices)}, train={len(train_indices)}, coverage={coverage:.4f}",
            header="BATCH",
        )
        batches.append(EvalBatch(val_indices=val_indices, train_indices=train_indices, coverage=coverage))

    # Step 4: Quality summary
    coverages = [b.coverage for b in batches]
    mean_cov = float(np.mean(coverages))
    std_cov = float(np.std(coverages))
    logger.log(
        f"Coverage summary: min={min(coverages):.4f}, max={max(coverages):.4f}, mean={mean_cov:.4f}, std={std_cov:.4f}",
        header="BATCH",
    )
    for i, b in enumerate(batches):
        if std_cov > 0 and b.coverage < mean_cov - 2 * std_cov:
            logger.log(f"WARNING: Batch {i} coverage {b.coverage:.4f} is >2 std below mean", header="BATCH")

    return batches


def select_representative_subset(
    train_data: list[DataItem],
    val_data: list[DataItem],
    val_size: int,
    train_val_ratio: int = 5,
    embedding_model: str = "openrouter/baai/bge-m3",
) -> tuple[list[int], list[int]]:
    """Select a representative (train, val) subset covering all val clusters.

    Uses k-means to cluster val items, picks the closest item to each centroid,
    then uses facility location to select train items that cover the selected val items.

    If val_size >= len(val_data), returns all indices (degrades to FullDataset).

    Returns:
        (train_indices, val_indices)
    """
    logger = get_logger()

    if val_size >= len(val_data):
        logger.log(
            f"val_size ({val_size}) >= val data ({len(val_data)}), using all items",
            header="SUBSET",
        )
        val_indices = list(range(len(val_data)))
    else:
        val_texts = [item.question for item in val_data]
        logger.log(f"Embedding {len(val_texts)} val texts for representative selection...", header="SUBSET")
        val_embs = _embed_texts(val_texts, model=embedding_model)

        labels = _kmeans(val_embs, k=val_size)
        rng = np.random.RandomState(42)
        val_indices = []
        for c in range(val_size):
            members = [i for i, label in enumerate(labels) if label == c]
            if not members:
                continue
            val_indices.append(members[rng.randint(len(members))])

        logger.log(f"Selected {len(val_indices)} representative val items from {val_size} clusters", header="SUBSET")

    train_texts = [item.raw_text if item.raw_text else item.question for item in train_data]
    logger.log(f"Embedding {len(train_texts)} train texts...", header="SUBSET")
    train_embs = _embed_texts(train_texts, model=embedding_model)

    val_texts_for_embed = [item.question for item in val_data]
    if val_size < len(val_data):
        subset_val_embs = val_embs[val_indices]
    else:
        val_embs_full = _embed_texts(val_texts_for_embed, model=embedding_model)
        subset_val_embs = val_embs_full[val_indices]

    budget = len(train_data) if train_val_ratio < 0 else train_val_ratio * len(val_indices)
    train_indices, coverage = _select_train_subset(subset_val_embs, train_embs, budget=budget)
    logger.log(
        f"Selected {len(train_indices)} train items (budget={budget}, coverage={coverage:.4f})",
        header="SUBSET",
    )

    return train_indices, val_indices
