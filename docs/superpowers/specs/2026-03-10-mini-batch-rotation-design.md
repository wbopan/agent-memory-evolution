# Mini-Batch Rotation for Evolution Loop

## Problem

When `--num-batches` is used, a single fixed batch is selected for the entire evolution run. All iterations evaluate on the same (train, val) subset. This risks overfitting the Knowledge Base Program to one batch's failure patterns.

## Solution

Rotate through batches across iterations, like mini-batch SGD. Iteration `i` evaluates on batch `i % num_batches`. Reflection uses the parent's eval result from whatever batch it was originally evaluated on — typically a different batch than the child will see. This "stale feedback" is by design, preventing overfitting.

## Design Decisions

1. **Cross-batch reflection** — reflection uses the parent's eval result from whatever batch it was originally scored on. The child is then evaluated on a different batch (determined by the current iteration index). The parent could have been scored on any previous batch, not necessarily the immediately preceding one. This prevents overfitting to a single batch's failure patterns.
2. **All seeds evaluated on batch 0** — simple and consistent. All seeds get comparable initial scores from the same data. The loop starts rotating from batch 1.
3. **`--batch-index` flag removed** — no users exist. When `--num-batches > 0`, rotation is always on.
4. **Simple modulo cycling** — iteration `i` uses batch `i % num_batches`. No shuffle at epoch boundaries. Batches are already semantically diverse from k-means clustering.
5. **Scores treated as comparable** — no EMA or re-evaluation. Val sizes are balanced by k-means + balancing. Train sizes may vary between batches (greedy facility location selects different amounts per cluster), but this variance is acceptable — the alternative (re-evaluation) doubles cost and defeats the mini-batch purpose.

## Changes

### `__main__.py`

- Remove `--batch-index` flag, its validation (`if args.batch_index >= args.num_batches`), and the single-batch slicing code (lines 147-148 that mutate `dataset.train`/`dataset.val`).
- When `--num-batches > 0`: build all batches via `build_eval_batches`, pass the full `batches` list to `EvolutionLoop`. The dataset stays unsliced — `loop.py` indexes into the full lists using `EvalBatch.train_indices`/`val_indices`.
- Update config logging to show rotation mode and batch count.

### `loop.py` — `EvolutionLoop`

- New optional parameter: `batches: list[EvalBatch] | None = None`.
- Store reference to full `dataset` for index-based subset selection.
- Helper method `_select_batch(iteration) -> tuple[list[DataItem], list[DataItem]]` computes `batch_idx = iteration % len(batches)` and returns the (train_subset, val_subset). Caller logs the batch index separately (trivial: `iteration % len(batches)`).
- When `batches` is set:
  - Seed evaluation: all seeds use `_select_batch(0)` (batch 0).
  - Iteration `i`: child evaluation uses `_select_batch(i)`.
  - Runtime violation fix loop: re-evaluations use the same batch as the initial child evaluation (same iteration index).
  - Reflection uses the parent's `eval_result` as-is (from whatever batch it was originally scored on).
- When `batches` is None: current behavior (use full `ds.train`/`ds.val` every iteration).
- Log which batch index is used at each iteration.

### No changes

- `evaluator.py` — still receives `(train_data, val_data)` per call.
- `reflector.py` — still receives parent's `EvalResult`.
- `batching.py` — `build_eval_batches` already returns `list[EvalBatch]`.
- `types.py` — `EvalResult`, `PoolEntry` unchanged.

## Data Flow

```
Pre-loop:
  batches = build_eval_batches(dataset.train, dataset.val, num_batches=K)

Seed evaluation (all seeds on batch 0):
  train_0, val_0 = _select_batch(0)  # batch_idx = 0 % K = 0
  for seed in seeds:
    eval_result = evaluator.evaluate(seed, train_0, val_0)
    pool.add(seed, eval_result)

Iteration i (i=1..max_iterations):
  train_i, val_i = _select_batch(i)  # batch_idx = i % K

  parent = pool.sample_parent()          # scored on some earlier batch (unknown which)
  child = reflector.reflect(parent, parent.eval_result)  # parent's old failed_cases
  child_result = evaluator.evaluate(child, train_i, val_i)  # scored on current batch

  # Runtime fix loop (if needed) uses same train_i, val_i
  pool.add(child, child_result)
```
