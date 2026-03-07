# Dataset Category Filtering

## Problem

When `--train-size` / `--val-size` truncate datasets, train and val items may come from unrelated domains:
- **locomo**: train sessions from conversation A, val QAs from conversation B
- **alfworld**: train items all "heat" tasks, val items all "pick_and_place"

## Design

Add a generic `--category` CLI flag that filters a dataset to a single domain/category before size truncation. Each benchmark loader interprets the category string itself.

### Flow

```
CLI --category heat
  → load_dataset(name, category="heat", train_size=N, val_size=M)
    → benchmark loader filters items to category
    → return Dataset (already single-domain)
  → train[:N], val[:M]  # safe, all same domain
```

### `load_dataset` change

```python
def load_dataset(name, *, category=None, train_size=None, val_size=None, **kwargs):
    dataset = _CUSTOM_REGISTRY[name](category=category, **kwargs)
    # truncate after category filtering
    ...
```

### Per-benchmark behavior

| Dataset | category semantics | Example values | Without category |
|---|---|---|---|
| **locomo** | Conversation index (post-shuffle) | `"0"`, `"1"`, `"2"` | All conversations (current behavior) |
| **alfworld** | Task type prefix | `"heat"`, `"cool"`, `"clean"`, `"pick_and_place"`, `"pick_two"`, `"look_at_obj_in_light"` | All task types (current behavior) |
| **kv_memory** | Not supported | — | Only behavior |
| **mini_locomo** | Not supported | — | Only behavior |
| **tau_bench** | Not supported (use existing `domain=` kwarg) | — | Only behavior |

Unsupported benchmarks ignore `category=None` silently. If passed a non-None category, they raise `ValueError`.

### locomo implementation

- Shuffle conversations with seed (existing behavior)
- `category="0"` → take only `samples[0]`: its sessions → train, its QAs → val
- This guarantees train/val semantic alignment

### alfworld implementation

- Parse all trials (existing behavior)
- `category="heat"` → filter items where `task_type` contains `"heat"`
- Shuffle filtered items, then split into train/val

### CLI

```
python -m programmaticmemory.evolution --dataset locomo --category 0 --train-size 5 --val-size 5
python -m programmaticmemory.evolution --dataset alfworld --category heat --train-size 10
```

## Files to change

1. `datasets.py` — add `category` param to `load_dataset`
2. `evolution/__main__.py` — add `--category` argparse flag
3. `benchmarks/locomo.py` — accept `category`, filter to single conversation
4. `benchmarks/mini_locomo.py` — accept `category`, raise if non-None
5. `benchmarks/alfworld.py` — accept `category`, filter by task type
6. `benchmarks/kv_memory.py` — accept `category`, raise if non-None
7. `benchmarks/tau_bench.py` — accept `category`, raise if non-None
