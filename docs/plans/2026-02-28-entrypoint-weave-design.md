# Entry Point + Weave Tracing Design

## Goal

Wire up existing ExperimentTracker to the CLI entry point, add `@weave.op()` to key methods for trace trees in Weave UI, and add missing CLI parameters.

## CLI Changes (`evolution/__main__.py`)

New parameters:
- `--no-weave` — disable weave/wandb (on by default)
- `--weave-project` — weave project name (default `"programmaticmemory"`)
- `--seed` — random seed (default 42)

ExperimentTracker wraps the run as context manager:

```python
tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)
with tracker:
    loop = EvolutionLoop(..., tracker=tracker)
    state = loop.run()
```

## @weave.op() Trace Points

| Method | File | Trace role |
|--------|------|------------|
| `EvolutionLoop.run()` | `loop.py` | Top-level trace |
| `MemoryEvaluator.evaluate()` | `evaluator.py` | Evaluation span |
| `Reflector.reflect_and_mutate()` | `reflector.py` | Reflection span |
| `smoke_test()` | `sandbox.py` | Smoke test span |

`weave[litellm]` auto-traces litellm calls as child spans.

Trace tree in Weave UI:

```
evolution_run
├─ evaluate (initial)
│  └─ litellm calls (auto)
├─ reflect_and_mutate (iter 1)
│  └─ litellm.completion (auto)
├─ smoke_test (iter 1)
├─ evaluate (iter 1)
│  └─ litellm calls (auto)
└─ ...
```

## Files Changed

| File | Change |
|------|--------|
| `evolution/__main__.py` | Add CLI params, instantiate tracker |
| `evolution/loop.py` | `@weave.op()` on `run()` |
| `evolution/evaluator.py` | `@weave.op()` on `evaluate()` |
| `evolution/reflector.py` | `@weave.op()` on `reflect_and_mutate()` |
| `evolution/sandbox.py` | `@weave.op()` on `smoke_test()` |

No changes to: `experiment_tracker.py`, `pyproject.toml`, `weave_tracing.py`.
