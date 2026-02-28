# Entry Point + Weave Tracing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire up ExperimentTracker to CLI and add @weave.op() tracing to key evolution methods.

**Architecture:** Add weave.op decorators to 4 methods (loop.run, evaluator.evaluate, reflector.reflect_and_mutate, sandbox.smoke_test). Update __main__.py to instantiate ExperimentTracker with new CLI flags (--no-weave, --weave-project, --seed).

**Tech Stack:** weave SDK (@weave.op), existing ExperimentTracker (wandb+weave), argparse

---

### Task 1: Add @weave.op() to sandbox.smoke_test

**Files:**
- Modify: `src/programmaticmemory/evolution/sandbox.py:1-4,224`

**Step 1: Add weave import and decorator**

At top of `sandbox.py`, add `import weave` after the existing imports (line 10). Then add `@weave.op()` before `smoke_test` (line 224).

```python
# After line 10 (after `from programmaticmemory.evolution.toolkit import ...`)
import weave
```

```python
# Line 224: add decorator before function
@weave.op()
def smoke_test(
```

**Step 2: Verify no import issues**

Run: `python -c "from programmaticmemory.evolution.sandbox import smoke_test; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/sandbox.py
git commit -m "feat: add @weave.op() to smoke_test"
```

---

### Task 2: Add @weave.op() to evaluator.evaluate

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:1-14,117`

**Step 1: Add weave import and decorator**

Add `import weave` after existing imports (after line 11, the `import litellm` line). Then add `@weave.op()` before `evaluate` method (line 117).

```python
# After line 13 (after `import litellm`)
import weave
```

```python
# Line 117: add decorator before method
    @weave.op()
    def evaluate(
```

**Step 2: Verify import**

Run: `python -c "from programmaticmemory.evolution.evaluator import MemoryEvaluator; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py
git commit -m "feat: add @weave.op() to MemoryEvaluator.evaluate"
```

---

### Task 3: Add @weave.op() to reflector.reflect_and_mutate

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py:1-8,38`

**Step 1: Add weave import and decorator**

Add `import weave` after existing imports (after line 7, `import litellm`). Then add `@weave.op()` before `reflect_and_mutate` method (line 38).

```python
# After line 7 (after `import litellm`)
import weave
```

```python
# Line 38: add decorator before method
    @weave.op()
    def reflect_and_mutate(
```

**Step 2: Verify import**

Run: `python -c "from programmaticmemory.evolution.reflector import Reflector; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py
git commit -m "feat: add @weave.op() to Reflector.reflect_and_mutate"
```

---

### Task 4: Add @weave.op() to EvolutionLoop.run

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py:1-5,51`

**Step 1: Add weave import and decorator**

Add `import weave` after line 4 (`from typing import Literal`). Then add `@weave.op()` before `run` method (line 51).

```python
# After line 5 (after `from typing import Literal`)
import weave
```

```python
# Line 51: add decorator before method
    @weave.op()
    def run(self) -> EvolutionState:
```

**Step 2: Verify import**

Run: `python -c "from programmaticmemory.evolution.loop import EvolutionLoop; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py
git commit -m "feat: add @weave.op() to EvolutionLoop.run"
```

---

### Task 5: Wire up ExperimentTracker in CLI entry point

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py`

**Step 1: Update __main__.py with new CLI args and tracker wiring**

Replace the entire `main()` function:

```python
"""Entry point: python -m programmaticmemory.evolution

Runs evolution on the kv_memory benchmark for a quick end-to-end test.
"""

from __future__ import annotations

import argparse
import random

from programmaticmemory.benchmarks.kv_memory import load_kv_memory
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.logging.experiment_tracker import ExperimentTracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Memory Program evolution")
    parser.add_argument("--iterations", type=int, default=5, help="Max evolution iterations")
    parser.add_argument("--num-items", type=int, default=10, help="Number of benchmark items")
    parser.add_argument("--difficulty", choices=["simple", "compound"], default="simple")
    parser.add_argument("--dataset-type", choices=["A", "B"], default="A")
    parser.add_argument("--task-model", default="openai/gpt-4o-mini", help="Model for task agent")
    parser.add_argument("--reflect-model", default="openai/gpt-4o", help="Model for reflection")
    parser.add_argument("--toolkit-model", default="openai/gpt-4o-mini", help="Model for toolkit LLM")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-weave", action="store_true", help="Disable weave/wandb tracking")
    parser.add_argument("--weave-project", default="programmaticmemory", help="Weave project name")
    args = parser.parse_args()

    # Set seed
    random.seed(args.seed)

    # Load data
    train, val, _ = load_kv_memory(num_items=args.num_items, difficulty=args.difficulty)

    # Configure
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=ExactMatchScorer(),
        task_model=args.task_model,
        toolkit_config=toolkit_config,
    )
    reflector = Reflector(model=args.reflect_model)
    tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

    # Run
    with tracker:
        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            dataset_type=args.dataset_type,
            max_iterations=args.iterations,
            toolkit_config=toolkit_config,
            tracker=tracker,
        )
        state = loop.run()

    # Output
    print(f"\n{'=' * 60}")
    print("Evolution complete!")
    print(f"Best score: {state.best_score:.3f}")
    print(f"Iterations: {state.total_iterations}")
    print(f"Best program (gen {state.best_program.generation}, hash {state.best_program.hash}):")
    print(f"{'=' * 60}")
    print(state.best_program.source_code)


if __name__ == "__main__":
    main()
```

**Step 2: Verify the entry point parses correctly**

Run: `python -m programmaticmemory.evolution --help`
Expected: Help output showing `--seed`, `--no-weave`, `--weave-project` flags

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: wire ExperimentTracker into CLI with --no-weave, --seed flags"
```

---

### Task 6: Verify all pieces work together

**Step 1: Dry-run import check**

Run: `python -c "from programmaticmemory.evolution.loop import EvolutionLoop; from programmaticmemory.evolution.evaluator import MemoryEvaluator; from programmaticmemory.evolution.reflector import Reflector; from programmaticmemory.evolution.sandbox import smoke_test; print('All imports OK')"`

**Step 2: Run existing tests (no LLM)**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All tests pass

**Step 3: Lint**

Run: `ruff check src/programmaticmemory/evolution/__main__.py src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/evaluator.py src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/sandbox.py`
Expected: No errors
