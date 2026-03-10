# Mini-Batch Rotation Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rotate evaluation batches across evolution iterations so each iteration uses a different (train, val) subset, cycling via simple modulo.

**Architecture:** `EvolutionLoop` receives an optional `batches: list[EvalBatch]`. When set, `_select_batch(iteration)` indexes into the full dataset using `batches[iteration % len(batches)]` to produce per-iteration (train, val) subsets. `__main__.py` passes all batches to the loop instead of pre-slicing.

**Tech Stack:** Python, existing `batching.py` (unchanged), existing test patterns with `unittest.mock`.

---

## File Map

- **Modify:** `src/programmaticmemory/evolution/loop.py` — add `batches` param, `_select_batch` helper, use per-iteration subsets
- **Modify:** `src/programmaticmemory/evolution/__main__.py` — remove `--batch-index`, pass `batches` list to loop
- **Modify:** `tests/evolution/test_loop.py` — add batch rotation tests

---

### Task 1: Add `_select_batch` and batch rotation to `loop.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py`
- Test: `tests/evolution/test_loop.py`

- [ ] **Step 1: Write failing tests for batch rotation**

Add the following tests to `tests/evolution/test_loop.py`:

```python
from programmaticmemory.evolution.batching import EvalBatch


class TestBatchRotation:
    def _make_batches_and_dataset(self):
        """Create 2 batches over a dataset of 4 train + 4 val items."""
        train = [
            DataItem(raw_text=f"train_{i}", question=f"tq{i}?", expected_answer=f"ta{i}")
            for i in range(4)
        ]
        val = [
            DataItem(raw_text="", question=f"vq{i}?", expected_answer=f"va{i}")
            for i in range(4)
        ]
        ds = Dataset(train=train, val=val, test=[])
        batches = [
            EvalBatch(val_indices=[0, 1], train_indices=[0, 1], coverage=0.9),
            EvalBatch(val_indices=[2, 3], train_indices=[2, 3], coverage=0.8),
        ]
        return ds, batches

    def test_seeds_evaluated_on_batch_0(self):
        """All seeds should be evaluated using batch 0 data."""
        ds, batches = self._make_batches_and_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=ds,
            max_iterations=0, batches=batches,
        )
        loop.run()

        # Seed should be evaluated with batch 0 subsets
        call_args = evaluator.evaluate.call_args
        train_arg = call_args[0][1]
        val_arg = call_args[0][2]
        assert len(train_arg) == 2
        assert len(val_arg) == 2
        assert train_arg[0].raw_text == "train_0"
        assert train_arg[1].raw_text == "train_1"
        assert val_arg[0].question == "vq0?"
        assert val_arg[1].question == "vq1?"

    def test_iterations_rotate_through_batches(self):
        """Iteration 1 uses batch 1, iteration 2 wraps to batch 0."""
        ds, batches = self._make_batches_and_dataset()
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),  # seed on batch 0
            EvalResult(score=0.6),                    # iter 1 on batch 1
            EvalResult(score=0.7),                    # iter 2 on batch 0 (wrap)
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=ds,
            max_iterations=2, batches=batches,
        )
        loop.run()

        # Check iteration 1 used batch 1 (train_indices=[2,3], val_indices=[2,3])
        iter1_call = evaluator.evaluate.call_args_list[1]
        assert iter1_call[0][1][0].raw_text == "train_2"
        assert iter1_call[0][2][0].question == "vq2?"

        # Check iteration 2 wrapped to batch 0 (train_indices=[0,1], val_indices=[0,1])
        iter2_call = evaluator.evaluate.call_args_list[2]
        assert iter2_call[0][1][0].raw_text == "train_0"
        assert iter2_call[0][2][0].question == "vq0?"

    def test_runtime_fix_uses_same_batch(self):
        """Runtime violation re-eval should use the same batch as initial child eval."""
        ds, batches = self._make_batches_and_dataset()
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),                    # seed on batch 0
            EvalResult(score=0.0, runtime_violation="timeout"),  # iter 1 on batch 1
            EvalResult(score=0.8),                    # re-eval on batch 1
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed"
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=ds,
            max_iterations=1, batches=batches,
        )
        loop.run()

        # Both iter 1 evals (initial + fix) should use batch 1
        iter1_initial = evaluator.evaluate.call_args_list[1]
        iter1_fix = evaluator.evaluate.call_args_list[2]
        assert iter1_initial[0][1][0].raw_text == "train_2"
        assert iter1_fix[0][1][0].raw_text == "train_2"

    def test_no_batches_uses_full_dataset(self):
        """Without batches, full ds.train/ds.val are used (existing behavior)."""
        ds, _batches = self._make_batches_and_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=ds,
            max_iterations=0,
        )
        loop.run()

        call_args = evaluator.evaluate.call_args
        assert len(call_args[0][1]) == 4  # full train
        assert len(call_args[0][2]) == 4  # full val
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_loop.py::TestBatchRotation -v`
Expected: FAIL — `EvolutionLoop.__init__` does not accept `batches` parameter.

- [ ] **Step 3: Implement `_select_batch` and wire batches into `EvolutionLoop`**

In `src/programmaticmemory/evolution/loop.py`:

1. Add import: `from programmaticmemory.evolution.batching import EvalBatch`
2. Add `batches: list[EvalBatch] | None = None` to `__init__` and store as `self.batches`.
3. Add helper method:

```python
def _select_batch(self, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
    """Select (train, val) subset for the given iteration."""
    ds = self.dataset
    if self.batches is None:
        return ds.train, ds.val
    batch = self.batches[iteration % len(self.batches)]
    train = [ds.train[i] for i in batch.train_indices]
    val = [ds.val[i] for i in batch.val_indices]
    return train, val
```

4. In `run()`, replace all `ds.train`/`ds.val` usage:
   - Seed evaluation: `train, val = self._select_batch(0)` then `self.evaluator.evaluate(seed, train, val)`
   - Child evaluation (line 148): `train, val = self._select_batch(i)` then `self.evaluator.evaluate(child, train, val)`
   - Runtime fix re-eval (line 167): use the same `train, val` (already in scope from `_select_batch(i)` above)
   - Add batch logging when `self.batches` is set: `self.logger.log(f"Using batch {i % len(self.batches)}/{len(self.batches)} (train={len(train)}, val={len(val)})", header="EVOLUTION")`
5. Update the startup log to show batch rotation info when `self.batches` is set.

- [ ] **Step 4: Add `DataItem` import if not already present**

`DataItem` is needed for the `_select_batch` return type hint. Check the existing imports in `loop.py` — `DataItem` is not currently imported from `types.py`. The type hint uses `list[DataItem]`, but since `from __future__ import annotations` is present, this only needs to be importable at type-check time. However, the actual list comprehension creates `DataItem` objects from `ds.train`/`ds.val`, which are already `DataItem` instances — no import needed for runtime. Still, add `DataItem` to the existing `from programmaticmemory.evolution.types import (...)` block for clarity.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_loop.py -v`
Expected: ALL PASS (both old tests and new `TestBatchRotation` tests).

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_loop.py
git commit -m "feat(loop): add mini-batch rotation support"
```

---

### Task 2: Update `__main__.py` to wire batches into loop

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py`

- [ ] **Step 1: Remove `--batch-index` flag**

Remove lines 102-107 (the `--batch-index` argument definition).

- [ ] **Step 2: Remove single-batch slicing, pass batches list to loop**

Replace lines 124-148 (the `_batch_info` / single-batch selection block) with:

```python
_batches = None
_batch_info = None
if args.num_batches > 0:
    from programmaticmemory.evolution.batching import build_eval_batches

    batches_list = build_eval_batches(
        dataset.train,
        dataset.val,
        num_batches=args.num_batches,
    )
    _batches = batches_list
    _batch_info = {
        "num_batches": args.num_batches,
        "batch_sizes": [(len(b.train_indices), len(b.val_indices)) for b in batches_list],
    }
```

- [ ] **Step 3: Update config logging**

Replace the existing batch info log (lines 169-175) with:

```python
if _batch_info:
    logger.log(
        f"Batch rotation: {_batch_info['num_batches']} batches, "
        f"sizes(train,val)={_batch_info['batch_sizes']}",
        header="CONFIG",
    )
```

- [ ] **Step 4: Pass `batches` to `EvolutionLoop`**

Add `batches=_batches` to the `EvolutionLoop(...)` constructor call (around line 216).

- [ ] **Step 5: Remove `args.batch_index` from config dict**

The `output_manager = RunOutputManager(base_dir="outputs", config=vars(args))` will no longer have `batch_index` in `args` since we removed the flag. No action needed — it's automatically gone.

- [ ] **Step 6: Run the full test suite to verify nothing is broken**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: ALL PASS.

- [ ] **Step 7: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat(cli): wire batch rotation into evolution loop, remove --batch-index"
```
