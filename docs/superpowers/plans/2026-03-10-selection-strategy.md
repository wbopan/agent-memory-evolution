# SelectionStrategy Protocol Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pluggable `SelectionStrategy` protocol to `ProgramPool` with two implementations: `SoftmaxSelection` (default, extracted from current code) and `RecencyDecaySelection` (new, `decay_rate^generation`).

**Architecture:** Extract the existing softmax weight/sample logic from `ProgramPool` into a `SoftmaxSelection` class conforming to a `SelectionStrategy` protocol. Add `RecencyDecaySelection` as a second implementation. `ProgramPool` delegates to the strategy. CLI gets `--selection-strategy` to choose between them.

**Tech Stack:** Python 3.12+, dataclasses, typing.Protocol, pytest

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/programmaticmemory/evolution/types.py` | Modify | Add `SelectionStrategy`, `SoftmaxSelection`, `RecencyDecaySelection`; refactor `ProgramPool` |
| `src/programmaticmemory/evolution/loop.py` | Modify | `temperature` → `strategy` parameter |
| `src/programmaticmemory/evolution/__main__.py` | Modify | New CLI args, strategy construction |
| `src/programmaticmemory/evolution/__init__.py` | Modify | Export new types |
| `tests/evolution/test_types.py` | Modify | New strategy tests, update pool tests |
| `tests/evolution/test_loop.py` | Modify | Update `EvolutionLoop` construction |

---

### Task 1: Add `SelectionStrategy` protocol and `SoftmaxSelection`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py`
- Modify: `tests/evolution/test_types.py`

- [ ] **Step 1: Write failing tests for `SoftmaxSelection`**

Add to `tests/evolution/test_types.py`:

```python
class TestSoftmaxSelection:
    def test_weights_favor_higher_scores(self):
        from programmaticmemory.evolution.types import SoftmaxSelection
        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.8)),
            PoolEntry(program=KBProgram(source_code="b"), eval_result=EvalResult(score=0.2)),
        ]
        weights = strategy.weights(entries)
        assert weights[0] > weights[1]

    def test_sample_returns_pool_entry(self):
        from programmaticmemory.evolution.types import SoftmaxSelection
        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="a"), eval_result=EvalResult(score=0.5)),
        ]
        result = strategy.sample(entries)
        assert isinstance(result, PoolEntry)

    def test_distribution_matches_softmax(self):
        """Verify softmax selection matches expected probabilities.

        Scores [0.6, 0.4, 0.4, 0.2] at T=0.15:
        P(0.6) ≈ 63%, P(0.4) ≈ 16.5% each, P(0.2) ≈ 4.3%
        """
        import math
        import random as _random
        from collections import Counter

        from programmaticmemory.evolution.types import SoftmaxSelection

        _random.seed(42)
        strategy = SoftmaxSelection(temperature=0.15)
        entries = [
            PoolEntry(program=KBProgram(source_code="best"), eval_result=EvalResult(score=0.6)),
            PoolEntry(program=KBProgram(source_code="mid1"), eval_result=EvalResult(score=0.4)),
            PoolEntry(program=KBProgram(source_code="mid2"), eval_result=EvalResult(score=0.4)),
            PoolEntry(program=KBProgram(source_code="weak"), eval_result=EvalResult(score=0.2)),
        ]

        n = 10000
        counts = Counter()
        for _ in range(n):
            entry = strategy.sample(entries)
            counts[entry.program.source_code] += 1

        scores = [0.6, 0.4, 0.4, 0.2]
        max_s = max(scores)
        weights = [math.exp((s - max_s) / 0.15) for s in scores]
        z = sum(weights)
        expected = [w / z for w in weights]

        labels = ["best", "mid1", "mid2", "weak"]
        for label, exp_p in zip(labels, expected, strict=True):
            empirical_p = counts[label] / n
            assert abs(empirical_p - exp_p) < 0.05, (
                f"{label}: expected {exp_p:.3f}, got {empirical_p:.3f}"
            )

    def test_repr(self):
        from programmaticmemory.evolution.types import SoftmaxSelection
        strategy = SoftmaxSelection(temperature=0.15)
        assert repr(strategy) == "SoftmaxSelection(T=0.15)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestSoftmaxSelection -v`
Expected: FAIL with `ImportError` (SoftmaxSelection doesn't exist yet)

- [ ] **Step 3: Implement `SelectionStrategy` protocol and `SoftmaxSelection`**

Add to `src/programmaticmemory/evolution/types.py` (after `PoolEntry`, before `ProgramPool`):

```python
class SelectionStrategy(Protocol):
    """Strategy for selecting a parent from the program pool."""

    def sample(self, entries: list[PoolEntry]) -> PoolEntry: ...
    def weights(self, entries: list[PoolEntry]) -> list[float]: ...


class SoftmaxSelection:
    """Score-proportional selection using softmax weighting."""

    def __init__(self, temperature: float = 0.15) -> None:
        if temperature <= 0:
            raise ValueError(f"temperature must be positive, got {temperature}")
        self.temperature = temperature

    def weights(self, entries: list[PoolEntry]) -> list[float]:
        max_score = max(e.score for e in entries)
        return [math.exp((e.score - max_score) / self.temperature) for e in entries]

    def sample(self, entries: list[PoolEntry]) -> PoolEntry:
        return random.choices(entries, weights=self.weights(entries), k=1)[0]

    def __repr__(self) -> str:
        return f"SoftmaxSelection(T={self.temperature})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestSoftmaxSelection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add SelectionStrategy protocol and SoftmaxSelection"
```

---

### Task 2: Add `RecencyDecaySelection`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py`
- Modify: `tests/evolution/test_types.py`

- [ ] **Step 1: Write failing tests for `RecencyDecaySelection`**

Add to `tests/evolution/test_types.py`:

```python
class TestRecencyDecaySelection:
    def test_weights_decay_by_generation(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection
        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="old", generation=5), eval_result=EvalResult(score=0.9)),
            PoolEntry(program=KBProgram(source_code="new", generation=0), eval_result=EvalResult(score=0.1)),
        ]
        weights = strategy.weights(entries)
        # gen=0 has weight 1.0, gen=5 has weight 0.8^5 ≈ 0.328
        assert weights[1] > weights[0]  # newer entry has higher weight despite lower score

    def test_weights_ignore_score(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection
        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=2), eval_result=EvalResult(score=0.9)),
            PoolEntry(program=KBProgram(source_code="b", generation=2), eval_result=EvalResult(score=0.1)),
        ]
        weights = strategy.weights(entries)
        assert weights[0] == weights[1]  # same generation = same weight regardless of score

    def test_weights_values(self):
        import math
        from programmaticmemory.evolution.types import RecencyDecaySelection
        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=0), eval_result=EvalResult(score=0.5)),
            PoolEntry(program=KBProgram(source_code="b", generation=3), eval_result=EvalResult(score=0.5)),
        ]
        weights = strategy.weights(entries)
        assert math.isclose(weights[0], 1.0)
        assert math.isclose(weights[1], 0.8**3)

    def test_sample_returns_pool_entry(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection
        strategy = RecencyDecaySelection(decay_rate=0.8)
        entries = [
            PoolEntry(program=KBProgram(source_code="a", generation=0), eval_result=EvalResult(score=0.5)),
        ]
        result = strategy.sample(entries)
        assert isinstance(result, PoolEntry)

    def test_repr(self):
        from programmaticmemory.evolution.types import RecencyDecaySelection
        strategy = RecencyDecaySelection(decay_rate=0.8)
        assert repr(strategy) == "RecencyDecaySelection(decay=0.8)"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestRecencyDecaySelection -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement `RecencyDecaySelection`**

Add to `src/programmaticmemory/evolution/types.py` (after `SoftmaxSelection`):

```python
class RecencyDecaySelection:
    """Roughly uniform selection with exponential decay on older generations."""

    def __init__(self, decay_rate: float = 0.8) -> None:
        if not 0 < decay_rate <= 1:
            raise ValueError(f"decay_rate must be in (0, 1], got {decay_rate}")
        self.decay_rate = decay_rate

    def weights(self, entries: list[PoolEntry]) -> list[float]:
        return [self.decay_rate ** e.program.generation for e in entries]

    def sample(self, entries: list[PoolEntry]) -> PoolEntry:
        return random.choices(entries, weights=self.weights(entries), k=1)[0]

    def __repr__(self) -> str:
        return f"RecencyDecaySelection(decay={self.decay_rate})"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestRecencyDecaySelection -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add RecencyDecaySelection strategy"
```

---

### Task 3: Refactor `ProgramPool` to use `SelectionStrategy`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py`
- Modify: `tests/evolution/test_types.py`

- [ ] **Step 1: Update `ProgramPool` to accept `SelectionStrategy`**

In `src/programmaticmemory/evolution/types.py`, replace the `ProgramPool` class:

```python
class ProgramPool:
    """Unbounded pool of evaluated programs with pluggable parent selection."""

    def __init__(self, strategy: SelectionStrategy) -> None:
        self.entries: list[PoolEntry] = []
        self.strategy = strategy

    def add(self, program: KBProgram, eval_result: EvalResult, name: str = "seed_0") -> None:
        self.entries.append(PoolEntry(program=program, eval_result=eval_result, name=name))

    def sample_parent(self) -> PoolEntry:
        """Sample a parent using the configured selection strategy."""
        if len(self.entries) == 1:
            return self.entries[0]
        return self.strategy.sample(self.entries)

    @property
    def best(self) -> PoolEntry:
        return max(self.entries, key=lambda e: e.score)

    def __len__(self) -> int:
        return len(self.entries)

    def summary(self) -> str:
        """Format pool status: entries sorted by score with selection probabilities."""
        if not self.entries:
            return "Pool: empty"
        sorted_entries = sorted(self.entries, key=lambda e: e.score, reverse=True)
        weights = self.strategy.weights(sorted_entries)
        total = sum(weights)
        lines = [f"Pool ({len(self.entries)} programs, {self.strategy!r}):"]
        for entry, w in zip(sorted_entries, weights, strict=True):
            prob = w / total
            lines.append(
                f"  {entry.program.hash}  score={entry.score:.3f}  P={prob:.1%}"
                f"  gen={entry.program.generation}  programs/{entry.name}.py"
            )
        return "\n".join(lines)
```

- [ ] **Step 2: Update all `ProgramPool` tests to use `SoftmaxSelection`**

In `tests/evolution/test_types.py`, update every `ProgramPool(temperature=0.15)` to `ProgramPool(strategy=SoftmaxSelection(temperature=0.15))`. Add the import:

```python
from programmaticmemory.evolution.types import (
    ...,
    SelectionStrategy,
    SoftmaxSelection,
    RecencyDecaySelection,
)
```

Update these tests:
- `TestEvolutionState.test_construction`: `ProgramPool(strategy=SoftmaxSelection(temperature=0.15))`
- `TestEvolutionState.test_with_history`: same
- `TestProgramPool.test_add_and_best`: same
- `TestProgramPool.test_best_with_single_entry`: same
- `TestProgramPool.test_sample_parent_returns_pool_entry`: same
- `TestProgramPool.test_sample_parent_single_entry_always_returns_it`: same
- `TestProgramPool.test_len`: same
- `TestProgramPool.test_entries_accessible`: same

Remove `TestProgramPool.test_sample_parent_softmax_distribution` (logic now lives in `TestSoftmaxSelection.test_distribution_matches_softmax`).

- [ ] **Step 3: Run all types tests**

Run: `uv run pytest tests/evolution/test_types.py -v`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "refactor: ProgramPool delegates to SelectionStrategy"
```

---

### Task 4: Update `EvolutionLoop` and `__main__.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py`
- Modify: `src/programmaticmemory/evolution/__main__.py`
- Modify: `tests/evolution/test_loop.py`

- [ ] **Step 1: Update `EvolutionLoop` constructor**

In `src/programmaticmemory/evolution/loop.py`:

1. Add import: `SelectionStrategy, SoftmaxSelection` from types
2. Change constructor parameter `temperature: float = 0.15` → `strategy: SelectionStrategy | None = None`
3. Replace `self._temperature = temperature` with `self.strategy = strategy or SoftmaxSelection()`
4. In `run()`, replace `pool = ProgramPool(temperature=self._temperature)` with `pool = ProgramPool(strategy=self.strategy)`
5. In the startup log, replace `temperature={pool.temperature}` with `strategy={self.strategy!r}`

- [ ] **Step 2: Update `__main__.py` CLI args**

In `src/programmaticmemory/evolution/__main__.py`:

1. Add imports: `SoftmaxSelection, RecencyDecaySelection` from types
2. Replace `--temperature` arg with three new args:

```python
parser.add_argument(
    "--selection-strategy",
    choices=["softmax", "recency_decay"],
    default="softmax",
    help="Parent selection strategy (default: softmax)",
)
parser.add_argument(
    "--selection-softmax-temperature",
    type=float,
    default=0.15,
    help="Softmax temperature for parent selection (default: 0.15, lower = more greedy)",
)
parser.add_argument(
    "--selection-recency-decay-rate",
    type=float,
    default=0.8,
    help="Decay rate per generation for recency_decay selection (default: 0.8)",
)
```

3. Before `EvolutionLoop` construction, build the strategy:

```python
if args.selection_strategy == "recency_decay":
    strategy = RecencyDecaySelection(decay_rate=args.selection_recency_decay_rate)
else:
    strategy = SoftmaxSelection(temperature=args.selection_softmax_temperature)
```

4. Replace `temperature=args.temperature` with `strategy=strategy` in the `EvolutionLoop(...)` call.

- [ ] **Step 3: Update `test_loop.py`**

No constructor changes needed — all existing `EvolutionLoop(...)` calls in `test_loop.py` don't pass `temperature`, so they'll use the default `strategy=None` → `SoftmaxSelection()`. Verify tests still pass.

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/evolution/test_types.py tests/evolution/test_loop.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/__main__.py tests/evolution/test_loop.py
git commit -m "feat: wire SelectionStrategy through EvolutionLoop and CLI"
```

---

### Task 5: Update `__init__.py` exports and final verification

**Files:**
- Modify: `src/programmaticmemory/evolution/__init__.py`

- [ ] **Step 1: Add new types to `__init__.py` exports**

Add `SelectionStrategy`, `SoftmaxSelection`, `RecencyDecaySelection` to the imports from `types`:

```python
from programmaticmemory.evolution.types import (
    DataItem,
    EvalResult,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    PoolEntry,
    ProgramPool,
    RecencyDecaySelection,
    Scorer,
    SelectionStrategy,
    SoftmaxSelection,
)
```

- [ ] **Step 2: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: ALL PASS

- [ ] **Step 3: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/types.py src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/__main__.py`
Expected: Clean

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/__init__.py
git commit -m "chore: export SelectionStrategy types from evolution package"
```
