# Selection Strategy Protocol Design

## Summary

Add a pluggable `SelectionStrategy` protocol to `ProgramPool`, enabling multiple parent selection strategies. Ship two implementations: the existing softmax (default) and a new recency-decay strategy.

## Research Context

No major LLM-based evolution system uses explicit per-program age/recency decay. FunSearch uses cycling temperature + island resets (indirect temporal pressure). ALPS (Hornby 2006, classical EA) is the canonical age-based system but hasn't been adopted in the LLM evolution community. This feature represents a research gap.

## Core Types

### `SelectionStrategy` Protocol

```python
class SelectionStrategy(Protocol):
    def sample(self, entries: list[PoolEntry]) -> PoolEntry: ...
    def weights(self, entries: list[PoolEntry]) -> list[float]: ...
```

### `SoftmaxSelection`

Extracted from current `ProgramPool._softmax_weights` + `sample_parent`.

- Constructor: `temperature: float = 0.15`
- `weights`: `exp((score - max_score) / T)`
- `sample`: `random.choices(entries, weights, k=1)[0]`
- `__repr__`: `SoftmaxSelection(T=0.15)`

### `RecencyDecaySelection`

New strategy. Roughly uniform sampling with exponential decay on older generations.

- Constructor: `decay_rate: float = 0.8`
- `weights`: `decay_rate ^ entry.program.generation`
- `sample`: `random.choices(entries, weights, k=1)[0]`
- `__repr__`: `RecencyDecaySelection(decay=0.8)`

Decay curve at `decay_rate=0.8`:

| Generation | Weight |
|------------|--------|
| 0 | 1.000 |
| 1 | 0.800 |
| 2 | 0.640 |
| 3 | 0.512 |
| 5 | 0.328 |
| 10 | 0.107 |

### `ProgramPool` Refactor

```python
class ProgramPool:
    def __init__(self, strategy: SelectionStrategy) -> None:
        self.entries: list[PoolEntry] = []
        self.strategy = strategy

    def sample_parent(self) -> PoolEntry:
        if len(self.entries) == 1:
            return self.entries[0]
        return self.strategy.sample(self.entries)

    def summary(self) -> str:
        # Uses self.strategy.weights() for probability display
        # Header: "Pool (N programs, <strategy repr>):"
```

`temperature` parameter removed from `ProgramPool`, now held by `SoftmaxSelection`.

## CLI Parameters

```
--selection-strategy softmax|recency_decay     (default: softmax)
--selection-softmax-temperature 0.15           (only used with softmax)
--selection-recency-decay-rate 0.8             (only used with recency_decay)
```

## Wiring (`__main__.py`)

```python
if args.selection_strategy == "recency_decay":
    strategy = RecencyDecaySelection(decay_rate=args.selection_recency_decay_rate)
else:
    strategy = SoftmaxSelection(temperature=args.selection_softmax_temperature)
```

## `EvolutionLoop` Changes

- Constructor: `temperature: float` → `strategy: SelectionStrategy`
- `run()`: `ProgramPool(temperature=...)` → `ProgramPool(strategy=self.strategy)`

## Logging

- Startup log includes strategy repr: `strategy=SoftmaxSelection(T=0.15)`
- `pool.summary()` header shows strategy type instead of `T=...`
- No additional per-sample logging — existing `summary()` output (called each iteration) already shows per-entry probabilities

## File Change List

| File | Change |
|------|--------|
| `types.py` | Add `SelectionStrategy`, `SoftmaxSelection`, `RecencyDecaySelection`; refactor `ProgramPool` |
| `loop.py` | `temperature` → `strategy` parameter |
| `__main__.py` | New CLI args, strategy construction |
| `__init__.py` | Export new types |
| `test_types.py` | New strategy tests + migrate existing pool tests |

## Test Plan

- `TestSoftmaxSelection`: migrated from existing `test_sample_parent_softmax_distribution`, verify weights and distribution
- `TestRecencyDecaySelection`: verify `weights` = `0.8 ^ generation`, verify higher generation = lower weight
- `TestProgramPool`: verify delegation to strategy, updated constructor
- All existing `ProgramPool(temperature=0.15)` → `ProgramPool(strategy=SoftmaxSelection(temperature=0.15))`
