# Population-Based Parent Selection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the serial (1+1) greedy evolution loop with a population pool and softmax-weighted parent selection.

**Architecture:** Add `ProgramPool` class to `types.py` with softmax sampling. Modify `EvolutionLoop` to accept multiple seed programs, maintain a pool, and sample parents from it each iteration. Remove `drop_degraded_program` — all children are unconditionally added to the pool.

**Tech Stack:** Python 3.12+, math.exp for softmax, random.choices for weighted sampling.

---

### Task 1: Add `PoolEntry` and `ProgramPool` to types.py

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:108-129`
- Test: `tests/evolution/test_types.py`

**Step 1: Write failing tests**

Add to `tests/evolution/test_types.py`:

```python
import math
import random
from collections import Counter

from programmaticmemory.evolution.types import PoolEntry, ProgramPool


class TestPoolEntry:
    def test_construction(self):
        p = KBProgram(source_code="x")
        er = EvalResult(score=0.8)
        entry = PoolEntry(program=p, eval_result=er, score=0.8)
        assert entry.score == 0.8
        assert entry.program == p
        assert entry.eval_result == er


class TestProgramPool:
    def test_add_and_best(self):
        pool = ProgramPool(temperature=0.15)
        p1 = KBProgram(source_code="a")
        p2 = KBProgram(source_code="b")
        pool.add(p1, EvalResult(score=0.3))
        pool.add(p2, EvalResult(score=0.8))
        assert pool.best.score == 0.8
        assert pool.best.program == p2

    def test_best_with_single_entry(self):
        pool = ProgramPool(temperature=0.15)
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.5))
        assert pool.best.score == 0.5

    def test_sample_parent_returns_pool_entry(self):
        pool = ProgramPool(temperature=0.15)
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.5))
        entry = pool.sample_parent()
        assert isinstance(entry, PoolEntry)
        assert entry.program == p

    def test_sample_parent_softmax_distribution(self):
        """Higher-scoring programs should be sampled more often."""
        random.seed(42)
        pool = ProgramPool(temperature=0.15)
        pool.add(KBProgram(source_code="high"), EvalResult(score=0.6))
        pool.add(KBProgram(source_code="low"), EvalResult(score=0.2))

        counts = Counter()
        for _ in range(1000):
            entry = pool.sample_parent()
            counts[entry.program.source_code] += 1

        # With T=0.15, score 0.6 vs 0.2: exp(0.6/0.15)/exp(0.2/0.15) = exp(2.67) ≈ 14.4x
        # So "high" should dominate heavily
        assert counts["high"] > counts["low"] * 5

    def test_sample_parent_single_entry_always_returns_it(self):
        pool = ProgramPool(temperature=0.15)
        p = KBProgram(source_code="only")
        pool.add(p, EvalResult(score=0.5))
        for _ in range(10):
            assert pool.sample_parent().program == p

    def test_len(self):
        pool = ProgramPool(temperature=0.15)
        assert len(pool) == 0
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.5))
        assert len(pool) == 1

    def test_entries_accessible(self):
        pool = ProgramPool(temperature=0.15)
        pool.add(KBProgram(source_code="a"), EvalResult(score=0.5))
        assert len(pool.entries) == 1
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestPoolEntry -v && uv run pytest tests/evolution/test_types.py::TestProgramPool -v`
Expected: FAIL with ImportError (PoolEntry, ProgramPool not defined)

**Step 3: Implement `PoolEntry` and `ProgramPool`**

Add to `src/programmaticmemory/evolution/types.py` before `EvolutionRecord`:

```python
import math
import random

@dataclass
class PoolEntry:
    """A program in the population pool with its evaluation result."""

    program: KBProgram
    eval_result: EvalResult
    score: float


class ProgramPool:
    """Unbounded pool of evaluated programs with softmax parent selection."""

    def __init__(self, temperature: float = 0.15) -> None:
        self.entries: list[PoolEntry] = []
        self.temperature = temperature

    def add(self, program: KBProgram, eval_result: EvalResult) -> None:
        self.entries.append(PoolEntry(program=program, eval_result=eval_result, score=eval_result.score))

    def sample_parent(self) -> PoolEntry:
        """Sample a parent using softmax-weighted selection."""
        if len(self.entries) == 1:
            return self.entries[0]
        max_score = max(e.score for e in self.entries)
        weights = [math.exp((e.score - max_score) / self.temperature) for e in self.entries]
        return random.choices(self.entries, weights=weights, k=1)[0]

    @property
    def best(self) -> PoolEntry:
        return max(self.entries, key=lambda e: e.score)

    def __len__(self) -> int:
        return len(self.entries)
```

Note: subtract `max_score` before exp to avoid overflow.

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestPoolEntry tests/evolution/test_types.py::TestProgramPool -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add PoolEntry and ProgramPool with softmax selection"
```

---

### Task 2: Update `EvolutionRecord` and `EvolutionState`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:111-129`
- Test: `tests/evolution/test_types.py`

**Step 1: Update the dataclasses**

In `types.py`, change `EvolutionRecord`:

```python
@dataclass
class EvolutionRecord:
    """Record of a single evolution iteration."""

    iteration: int
    program: KBProgram
    score: float
    parent_hash: str | None = None
```

Change `EvolutionState`:

```python
@dataclass
class EvolutionState:
    """Full state of an evolution run."""

    pool: ProgramPool
    best_score: float
    history: list[EvolutionRecord] = field(default_factory=list)
    total_iterations: int = 0

    @property
    def best_program(self) -> KBProgram:
        return self.pool.best.program
```

**Step 2: Update existing tests in `TestEvolutionState`**

Replace `TestEvolutionState` in `test_types.py`:

```python
class TestEvolutionState:
    def test_construction(self):
        pool = ProgramPool(temperature=0.15)
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.8))
        state = EvolutionState(pool=pool, best_score=0.8)
        assert state.history == []
        assert state.total_iterations == 0
        assert state.best_program == p

    def test_with_history(self):
        pool = ProgramPool(temperature=0.15)
        p = KBProgram(source_code="x")
        pool.add(p, EvalResult(score=0.9))
        record = EvolutionRecord(iteration=1, program=p, score=0.9, parent_hash=None)
        state = EvolutionState(
            pool=pool,
            best_score=0.9,
            history=[record],
            total_iterations=1,
        )
        assert len(state.history) == 1
        assert state.history[0].parent_hash is None
```

**Step 3: Run tests**

Run: `uv run pytest tests/evolution/test_types.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: update EvolutionRecord and EvolutionState for pool-based evolution"
```

---

### Task 3: Update `EvolutionLoop` to use pool-based selection

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py`
- Test: `tests/evolution/test_loop.py`

**Step 1: Rewrite `EvolutionLoop.__init__` and `run`**

Key changes to `loop.py`:
- `initial_program` → `initial_programs: list[KBProgram]`
- Remove `drop_degraded_program`
- Add `temperature: float = 0.15`
- Import `ProgramPool` from types
- `run()` creates pool, evaluates all seeds, then loops with `pool.sample_parent()`

```python
class EvolutionLoop:
    """Population-based evolution loop for Knowledge Base Programs."""

    def __init__(
        self,
        evaluator: MemoryEvaluator,
        reflector: Reflector,
        dataset: Dataset,
        initial_programs: list[KBProgram] | None = None,
        max_iterations: int = 20,
        temperature: float = 0.15,
        stop_condition: StopperProtocol | None = None,
        tracker: ExperimentTracker | None = None,
        output_manager: RunOutputManager | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.reflector = reflector
        self.dataset = dataset
        self.initial_programs = initial_programs or [KBProgram(source_code=INITIAL_KB_PROGRAM)]
        self.max_iterations = max_iterations
        self.temperature = temperature
        self.stop_condition = stop_condition
        self.tracker = tracker
        self.output_manager = output_manager
        self.logger = get_logger()

    @weave.op()
    def run(self) -> EvolutionState:
        """Execute the evolution loop and return final state."""
        ds = self.dataset
        pool = ProgramPool(temperature=self.temperature)

        self.logger.log(
            f"Starting evolution: max_iter={self.max_iterations}, seeds={len(self.initial_programs)}, "
            f"train={len(ds.train)}, val={len(ds.val)}, temperature={self.temperature}",
            header="EVOLUTION",
        )

        # Evaluate all seed programs
        seed_eval_results = []
        for idx, seed in enumerate(self.initial_programs):
            if self.output_manager:
                self.output_manager.set_phase(0, "train")
            self.logger.log(
                f"Evaluating seed {idx + 1}/{len(self.initial_programs)} (hash={seed.hash})",
                header="EVOLUTION",
            )
            eval_result = self.evaluator.evaluate(seed, ds.train, ds.val)
            pool.add(seed, eval_result)
            seed_eval_results.append(eval_result)
            self.logger.log(f"Seed {idx + 1} score: {eval_result.score:.3f}", header="EVOLUTION")

            if self.output_manager:
                self.output_manager.write_program(0, seed.source_code, accepted=True, score=eval_result.score)
            if self.output_manager and eval_result.failed_cases:
                self.output_manager.write_failed_cases(0, _serialize_failed_cases(eval_result.failed_cases))

        best_score = pool.best.score
        if self.tracker:
            self.tracker.log_metrics({"score": best_score, "accepted": 1}, iteration=0)

        state = EvolutionState(
            pool=pool,
            best_score=best_score,
            history=[
                EvolutionRecord(iteration=0, program=seed, score=er.score)
                for seed, er in zip(self.initial_programs, seed_eval_results)
            ],
            total_iterations=0,
        )

        for i in range(1, self.max_iterations + 1):
            if self.stop_condition and self.stop_condition(state):
                self.logger.log(f"Stop condition triggered at iteration {i}", header="EVOLUTION")
                break

            self.logger.log(f"--- Iteration {i}/{self.max_iterations} ---", header="EVOLUTION")

            # Sample parent from pool
            parent_entry = pool.sample_parent()
            parent = parent_entry.program
            parent_eval = parent_entry.eval_result
            self.logger.log(
                f"Selected parent (hash={parent.hash}, score={parent_entry.score:.3f})",
                header="EVOLUTION",
            )

            # Reflect and mutate
            if self.output_manager:
                self.output_manager.set_phase(i, "reflect")
            self.logger.log("Starting reflection", header="EVOLUTION")
            child = self.reflector.reflect_and_mutate(parent, parent_eval, i)
            if child is None:
                self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
                state.history.append(EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash))
                state.total_iterations = i
                continue

            # Evaluate child
            if self.output_manager:
                self.output_manager.set_phase(i, "train")
            self.logger.log(
                f"Evaluating child program (gen={child.generation}, hash={child.hash})",
                header="EVOLUTION",
            )
            child_result = self.evaluator.evaluate(child, ds.train, ds.val)

            # Runtime violation fix loop
            for _fix_attempt in range(self.reflector.max_fix_attempts):
                if not child_result.runtime_violation:
                    break
                self.logger.log(
                    f"Runtime violation: {child_result.runtime_violation}, attempting fix",
                    header="EVOLUTION",
                )
                fixed_code = self.reflector.fix_runtime_violation(child.source_code, child_result.runtime_violation)
                if fixed_code is None:
                    self.logger.log("Runtime fix failed, giving up", header="EVOLUTION")
                    break
                child = KBProgram(
                    source_code=fixed_code,
                    generation=parent.generation + 1,
                    parent_hash=parent.hash,
                )
                child_result = self.evaluator.evaluate(child, ds.train, ds.val)

            child_score = child_result.score

            # Add child to pool unconditionally
            pool.add(child, child_result)

            improved = child_score > best_score
            self.logger.log(
                f"Child score: {child_score:.3f} (best: {best_score:.3f})",
                header="EVOLUTION",
            )
            if self.output_manager:
                self.output_manager.write_program(i, child.source_code, accepted=improved, score=child_score)
            if self.output_manager and child_result.failed_cases:
                self.output_manager.write_failed_cases(i, _serialize_failed_cases(child_result.failed_cases))

            if improved:
                self.logger.log(f"New best! {best_score:.3f} -> {child_score:.3f}", header="EVOLUTION")
                best_score = child_score

            state.history.append(
                EvolutionRecord(iteration=i, program=child, score=child_score, parent_hash=parent.hash)
            )
            state.best_score = best_score
            state.total_iterations = i

            if self.tracker:
                self.tracker.log_metrics(
                    {"score": child_score, "best_score": best_score, "pool_size": len(pool)},
                    iteration=i,
                )

        # Final summary
        self.logger.log(
            f"Evolution complete: {state.total_iterations} iterations, best score: {state.best_score:.3f}",
            header="EVOLUTION",
        )
        summary = {
            "best_score": state.best_score,
            "total_iterations": state.total_iterations,
            "best_program_hash": state.best_program.hash,
            "best_program_generation": state.best_program.generation,
            "pool_size": len(pool),
            "score_history": [
                {"iteration": r.iteration, "score": r.score, "parent_hash": r.parent_hash}
                for r in state.history
            ],
            "best_program_source": state.best_program.source_code,
        }
        if self.tracker:
            self.tracker.log_summary(summary)
        if self.output_manager:
            self.output_manager.write_summary(summary)

        return state
```

**Step 2: Rewrite tests in `test_loop.py`**

Replace all tests. Key changes:
- No more `state.current_program` — use `state.pool` and `state.best_program`
- No more `accepted` field — use `parent_hash`
- No more `drop_degraded_program` test — replaced with pool behavior
- Use `initial_programs=[...]` instead of `initial_program=...`

```python
class TestEvolutionLoop:
    def test_initial_evaluation_only(self):
        """With max_iterations=0, only seed programs are evaluated."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, per_case_scores=[0.5])
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert state.total_iterations == 0
        assert evaluator.evaluate.call_count == 1
        assert reflector.reflect_and_mutate.call_count == 0
        assert len(state.pool) == 1

    def test_child_improves_best(self):
        """Child with higher score becomes new best."""
        dataset = _make_dataset()
        child_program = KBProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset, max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.8
        assert state.best_program == child_program
        assert len(state.pool) == 2  # seed + child both in pool

    def test_child_worse_still_added_to_pool(self):
        """Child with lower score is still added to pool."""
        dataset = _make_dataset()
        initial = KBProgram(source_code=INITIAL_KB_PROGRAM)
        child = KBProgram(source_code="worse", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.7, failed_cases=[]),
            EvalResult(score=0.3, per_case_scores=[0.3]),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.7
        assert state.best_program == initial
        assert len(state.pool) == 2  # both in pool

    def test_multiple_seeds(self):
        """Multiple seed programs are all evaluated and added to pool."""
        dataset = _make_dataset()
        seed1 = KBProgram(source_code="seed1")
        seed2 = KBProgram(source_code="seed2")

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3),
            EvalResult(score=0.7),
        ]
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[seed1, seed2], max_iterations=0,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 2
        assert len(state.pool) == 2
        assert state.best_score == 0.7
        assert state.best_program == seed2

    def test_reflection_failure_skips_iteration(self):
        """If reflector returns None, iteration is skipped."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = None

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset, max_iterations=2,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1  # Only seed
        assert reflector.reflect_and_mutate.call_count == 2
        assert state.total_iterations == 2
        assert len(state.pool) == 1  # No children added

    def test_stop_condition_halts_loop(self):
        """Stop condition should terminate the loop early."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])
        reflector = MagicMock(spec=Reflector)
        stop = MagicMock()
        stop.return_value = True

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            max_iterations=10, stop_condition=stop,
        )
        state = loop.run()

        assert state.total_iterations == 0
        assert reflector.reflect_and_mutate.call_count == 0

    def test_tracker_receives_metrics(self):
        """ExperimentTracker should receive log_metrics calls."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = KBProgram(source_code="child", generation=1)
        reflector.max_fix_attempts = 3
        tracker = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            max_iterations=1, tracker=tracker,
        )
        loop.run()

        assert tracker.log_metrics.call_count >= 2
        assert tracker.log_summary.call_count == 1

    def test_parent_hash_tracked_in_history(self):
        """EvolutionRecord should track which parent was selected."""
        dataset = _make_dataset()
        initial = KBProgram(source_code=INITIAL_KB_PROGRAM)
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        # Last record should have parent_hash pointing to initial
        last_record = state.history[-1]
        assert last_record.parent_hash == initial.hash


class TestEvolutionLoopRuntimeFix:
    """Tests for runtime violation fix loop in EvolutionLoop."""

    def test_runtime_violation_triggers_fix_and_reeval(self):
        """Runtime violation -> fix_runtime_violation called -> re-eval succeeds."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),
            EvalResult(score=0.8),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed code"
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        reflector.fix_runtime_violation.assert_called_once_with(
            "child", "memory.read() returned 5000 chars (limit: 1000)"
        )
        assert evaluator.evaluate.call_count == 3
        assert state.best_score == 0.8

    def test_runtime_violation_fix_returns_none(self):
        """Runtime violation -> fix returns None -> child added with score=0."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() timed out after 5.0s"),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = None
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert evaluator.evaluate.call_count == 2
        assert len(state.pool) == 2  # both added

    def test_runtime_violation_fix_loop_retries(self):
        """First fix still violates -> loop retries -> second fix succeeds."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 3000 chars (limit: 1000)"),
            EvalResult(score=0.7),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.side_effect = ["fix1", "fix2"]
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert reflector.fix_runtime_violation.call_count == 2
        assert evaluator.evaluate.call_count == 4
        assert state.best_score == 0.7
```

**Step 3: Run tests**

Run: `uv run pytest tests/evolution/test_loop.py -v`
Expected: PASS

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_loop.py
git commit -m "feat: rewrite EvolutionLoop for pool-based parent selection"
```

---

### Task 4: Update `__init__.py` exports

**Files:**
- Modify: `src/programmaticmemory/evolution/__init__.py`

**Step 1: Add new exports**

Add `PoolEntry` and `ProgramPool` to the imports from `types`:

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
    Scorer,
)
```

**Step 2: Commit**

```bash
git add src/programmaticmemory/evolution/__init__.py
git commit -m "feat: export PoolEntry and ProgramPool from evolution package"
```

---

### Task 5: Update CLI (`__main__.py`)

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py`

**Step 1: Update CLI**

Changes:
- Remove `--drop-degraded-program` arg (line 79-83)
- Add `--temperature` arg
- Change `EvolutionLoop` constructor call: remove `drop_degraded_program`, add `temperature`
- Update final output to use `state.best_program` (property still works, no change needed for that)

Replace the `--drop-degraded-program` arg with:
```python
parser.add_argument(
    "--temperature",
    type=float,
    default=0.15,
    help="Softmax temperature for parent selection (default: 0.15, lower = more greedy)",
)
```

Update the `EvolutionLoop` construction:
```python
loop = EvolutionLoop(
    evaluator=evaluator,
    reflector=reflector,
    dataset=dataset,
    max_iterations=args.iterations,
    temperature=args.temperature,
    tracker=tracker,
    output_manager=output_manager,
)
```

**Step 2: Run quick sanity check**

Run: `uv run python -m programmaticmemory.evolution --help`
Expected: Shows `--temperature` flag, no `--drop-degraded-program`

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: add --temperature CLI flag, remove --drop-degraded-program"
```

---

### Task 6: Run full test suite and fix any breakage

**Step 1: Run all non-LLM tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: PASS

**Step 2: Fix any import errors or test failures from other test files**

Check if any other test files reference `state.current_program`, `state.current_score`, `accepted`, or `drop_degraded_program`. Fix as needed.

**Step 3: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/ && uv run ruff format src/programmaticmemory/evolution/`

**Step 4: Commit any fixes**

```bash
git add -A
git commit -m "fix: resolve test and lint issues from pool-based evolution refactor"
```
