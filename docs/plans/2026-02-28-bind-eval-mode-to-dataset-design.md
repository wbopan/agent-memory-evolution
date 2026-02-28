# Bind Eval Mode to Dataset

## Problem

Dataset type (Type A / Type B) is specified at runtime via `--dataset-type` CLI flag, but it's an inherent property of each benchmark. Running kv_memory with `--dataset-type B` silently fails with zero scores.

## Design

### Naming

- Type A → `EvalMode.OFFLINE` (batch ingest train, then read-only val)
- Type B → `EvalMode.ONLINE` (interleaved multi-turn train with feedback, then read-only val)

### New Types (`types.py`)

```python
class EvalMode(str, Enum):
    OFFLINE = "offline"
    ONLINE = "online"

@dataclass
class Dataset:
    train: list[DataItem]
    val: list[DataItem]
    test: list[DataItem]
    eval_mode: EvalMode
```

### Changes

1. **Benchmark loaders** return `Dataset` instead of `tuple[list, list, list]`:
   - kv_memory → `EvalMode.OFFLINE`
   - locomo → `EvalMode.OFFLINE`
   - tau_bench → `EvalMode.ONLINE`
   - alfworld → `EvalMode.ONLINE`

2. **`datasets.py`**: `load_dataset()` returns `Dataset`.

3. **`loop.py`**: `EvolutionLoop.__init__` takes `Dataset` (or just `eval_mode: EvalMode`), removes `dataset_type` param.

4. **`evaluator.py`**: `evaluate()` takes `EvalMode` instead of `Literal["A", "B"]`. Rename `_evaluate_type_a` → `_evaluate_offline`, `_evaluate_type_b` → `_evaluate_online`.

5. **`__main__.py`**: Remove `--dataset-type` CLI arg.

6. **Tests**: Update all references from `"A"`/`"B"` to `EvalMode.OFFLINE`/`EvalMode.ONLINE`.
