# Co-Selected Evaluation Batching

## Problem

Evaluating a Knowledge Base Program costs O(N + 2M) LLM calls (N train writes + M query generations + M answer generations). In the evolution loop, every candidate runs a full evaluation. Reducing per-evaluation cost is the core leverage for search efficiency.

Naive val-only splitting doesn't help: each batch still needs all N train items, so total cost increases. Random train splitting risks selecting train items that don't support the corresponding val questions. We need a method to jointly select train and val subsets.

## Design

### Overview

A new `batching.py` module serves as a preprocessing layer between dataset loading and the evolution loop. It splits (train, val) into K semantically aligned evaluation batches. Each evolution run uses exactly 1 batch.

```
Dataset loading (datasets.py)
        |
        v
  build_eval_batches()    <-- new module batching.py
        |
        v
  select batch[--batch-index]
        |
        v
  dataset.train = batch.train_subset
  dataset.val = batch.val_subset
        |
        v
  EvolutionLoop (unchanged)
  MemoryEvaluator (unchanged)
```

Loop and evaluator require zero changes.

### Algorithm

1. **Embedding**: Encode all train and val texts via `litellm.embedding(model="openrouter/baai/bge-m3", caching=True)`. BGE-M3 (1024 dims) requires no query/passage prefix. Train items use `raw_text` (offline pipeline) or `question` (online pipeline). Val items use `question`.

2. **K-means clustering**: Cluster val embeddings into K clusters (hand-written Lloyd's algorithm with cosine distance, no scikit-learn dependency). Balance clusters to m = len(val) // K samples each.

3. **Facility location greedy**: For each cluster, greedily select train items from the full train set that maximize coverage (submodular facility location objective). Budget: `train_budget_per_val * m` items per batch.

4. **Return** K `EvalBatch` objects with indices and coverage scores.

### Core Types

```python
@dataclass
class EvalBatch:
    val_indices: list[int]
    train_indices: list[int]
    coverage: float  # mean max cosine similarity
```

### Public API

```python
def build_eval_batches(
    train_data: list[DataItem],
    val_data: list[DataItem],
    num_batches: int = 10,
    train_budget_per_val: int = 5,
    coverage_threshold: float | None = None,
    embedding_model: str = "openrouter/baai/bge-m3",
) -> list[EvalBatch]:
```

### CLI Integration

New arguments in `__main__.py`:
```
--num-batches K     # Build K batches (default 0 = no batching, full evaluation)
--batch-index I     # Use batch I (0-indexed, must be < K)
```

When `--num-batches > 0`:
1. Load full dataset
2. Call `build_eval_batches()`
3. Slice `dataset.train` and `dataset.val` using `batches[I]` indices
4. Proceed with evolution as normal

### Embedding

```python
def _embed_texts(texts: list[str], model: str) -> np.ndarray:
    response = litellm.embedding(model=model, input=texts, caching=True)
    vectors = np.array([d["embedding"] for d in response.data])
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-10)
```

`caching=True` ensures repeated runs hit litellm disk cache (already configured in `__main__.py`).

### K-means (no scikit-learn)

Hand-written Lloyd's algorithm (~30 lines) using cosine distance on L2-normalized vectors. Avoids adding scikit-learn as a dependency.

### Facility Location Greedy

For each batch k, greedy selection maximizing marginal coverage:

```
d* = argmax_{d in D \ D_k} sum_{q in Q_k} max(0, sim(q, d) - max_{d' in D_k} sim(q, d'))
```

Submodular guarantee: (1 - 1/e) ~= 0.63 approximation ratio.

### Text Extraction

- Offline pipeline (raw_text non-empty): embed `item.raw_text`
- Online pipeline (raw_text empty): embed `item.question`
- Val items: always embed `item.question`

### Cluster Balancing

- Oversized clusters: keep m items closest to centroid
- Undersized clusters: fill from unassigned items nearest to centroid
- Remainder handling: last batch may have slightly different m if len(val) % K != 0

### Logging

Uses `get_logger()` with `header="BATCH"`:

1. **Start**: parameters (num_batches, train/val size, embedding model, budget)
2. **Embedding phase**: train/val embedding complete, vector dimensions
3. **K-means phase**: per-cluster raw size and balanced size
4. **Facility location phase**: per-batch train items selected and coverage score
5. **Quality summary**: coverage statistics (min/max/mean), flag low-quality batches (>2 std below mean)

### Dependencies

- `numpy` (already a transitive dependency via chromadb)
- `litellm` (already present)
- No new dependencies

### Cost Analysis

| Scenario | LLM calls | Relative |
|----------|-----------|----------|
| Full (N=1000, M=300) | 1600 | 1x |
| Single batch (m=30, n=150) | 210 | 0.13x |

Embedding API cost for batch construction: ~$0.01 (one-time, cached).

### Assumptions and Limitations

1. Semantic similarity ~= knowledge dependency. Works for factual retrieval (kv_memory, locomo). May not hold for reasoning tasks (nyt_connections).
2. Fragment independence. Facility location assumes each fragment independently contributes coverage.
3. KB Program behavior not modeled. Selection uses raw text embedding similarity, but the Program may transform text in complex ways.

### Test Plan

1. Unit tests: mock `litellm.embedding`, verify K-means, facility location, batch construction
2. Edge cases: num_batches >= len(val), single batch, empty data, online vs offline pipeline text extraction
3. Integration: `--num-batches 3 --batch-index 0` end-to-end on kv_memory
