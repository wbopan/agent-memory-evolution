# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Build & Test Commands

```bash
# Install (editable, with dev deps)
uv pip install -e ".[dev]"

# Run all tests
uv run pytest tests/evolution/ -v

# Run tests excluding real LLM calls (fast, no API key needed)
uv run pytest tests/evolution/ -m "not llm" -v

# Run only LLM integration tests (uses disk cache by default)
uv run pytest tests/evolution/ -m llm -v

# Update all snapshots (unit test snapshots don't need API keys)
uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v

# Update LLM snapshots (requires OPENROUTER_API_KEY or DEEPSEEK_API_KEY)
uv run pytest tests/evolution/test_llm_integration.py --snapshot-update

# Run a single test
uv run pytest tests/evolution/test_evaluator.py::TestTypeAEvaluation::test_basic_type_a -v

# Lint & format (also runs as pre-commit hooks)
uv run ruff check src/
uv run ruff format src/

# Run evolution on kv_memory benchmark
uv run python -m programmaticmemory.evolution --iterations 5 --num-items 10
# Weave/wandb tracing is ON by default; disable with --no-weave
# --seed 42 (default), --weave-project programmaticmemory (default)
```

## Architecture

This is a GEPA (Gradient-free Exploration with Population Advancement) framework that evolves **Memory Programs** — Python code defining `Observation`, `Query`, and `Memory` classes.

### Evolution Loop (`evolution/loop.py`)

```
Evaluate(program, data) → EvalResult → Reflect(code, failures) → new MemoryProgram → repeat
```

Greedy serial: one candidate, one child per iteration, accept if score improves.

### Key Modules (all under `src/programmaticmemory/evolution/`)

- **types.py** — Core dataclasses: `MemoryProgram`, `DataItem`, `EvalResult`, `FailedCase`, `EvolutionState`
- **evaluator.py** — Type A (batch-ingest train, then read-only val) and Type B (interleaved multi-turn train with feedback, then read-only val) pipelines. Uses `ExactMatchScorer` (containment-based) or `LLMJudgeScorer`.
- **reflector.py** — Calls LLM with current code + failed cases, extracts last `` ```python ``` `` block as the improved program.
- **sandbox.py** — `compile_memory_program()`: AST parse → check 3 required classes → validate import whitelist → exec. Also: `extract_dataclass_schema()` (outputs commented JSON example), `smoke_test()`.
- **toolkit.py** — Resource bundle (`db`: SQLite, `chroma`: ChromaDB, `llm_completion`: budget-limited LLM, `logger`). Created fresh per evaluation.
- **prompts.py** — All prompt templates. `INITIAL_MEMORY_PROGRAM` is the baseline (append-all/return-all). `REFLECTION_SYSTEM_PROMPT` instructs the reflector LLM.
- **benchmarks/kv_memory.py** — Simple factual recall benchmark (simple/compound difficulty).

### Other Modules (under `src/programmaticmemory/`)

- **cache.py** — `configure_cache("disk"|"redis"|"r2"|"s3")` / `disable_cache()` for litellm caching.
- **datasets.py** — Unified dataset loading with built-in and custom registries.
- **core/adapter.py** — Generic protocols: `EvaluationBatch`, `Candidate`, type vars for rollouts/trajectories.
- **core/callbacks.py** — Callback protocol for optimization instrumentation (e.g. `on_iteration_end`).
- **core/data_loader.py** — Data loader protocols and split helpers.
- **logging/experiment_tracker.py** — Experiment tracking via wandb/weave.
- **logging/weave_tracing.py** — Weave call tracing utilities with feedback.

- **utils/stop_condition.py** — Graceful stopping (signal handlers, convergence checks).

### Two Separate LLM Roles

1. **Task agent** (`evaluator.py:_llm_call`) — Fixed model that generates Observation/Query JSON and answers questions. Separate from the memory program.
2. **Toolkit LLM** (`toolkit.py:Toolkit.llm_completion`) — Available to Memory Programs via `toolkit.llm_completion()`, budget-limited (default 50 calls), with tenacity retry.

## Test Infrastructure

- **Pytest markers**: `@pytest.mark.llm` (real LLM calls), `@pytest.mark.uses_chroma` (real ChromaDB instead of mock)
- **Disk cache**: `tests/evolution/.llm_cache/` — litellm disk cache committed to git, so LLM tests replay without API keys. Configured in `tests/evolution/conftest.py` via session-scoped fixture that wraps `litellm.completion` with `caching=True`.
- **Syrupy snapshots**: `tests/evolution/__snapshots__/*.ambr` — 4 snapshot files:
  - `test_prompts.ambr` — prompt template outputs from `build_*` functions and formatted system prompts
  - `test_evaluator.ambr` — full `captured_calls` (all messages sent to mock LLM per test)
  - `test_reflector.ambr` — reflection LLM call messages (system + user prompts)
  - `test_llm_integration.ambr` — `{prompt, output}` dicts with real LLM responses
- **ChromaDB mock**: `conftest.py` auto-mocks `chromadb.EphemeralClient`; opt out with `@pytest.mark.uses_chroma`.

## Knowledge Files

- `knowledge/evolution-design.md` — Design document for the evolution system (phases, flows, testing strategy)
- `knowledge/system-design-original.md` — Original system design notes (motivation, Observation/Query/Memory lifecycle, Type A/B specs)

## Environment Setup

- Package manager: `uv` (required)
- `OPENROUTER_API_KEY` — needed for real LLM calls and snapshot updates
- LLM tests replay from disk cache (`tests/evolution/.llm_cache/`) without API keys
- Worktree directory: `.worktrees/` (gitignored, used for feature branch isolation)

## Conventions

- Python 3.12+, `from __future__ import annotations` in all modules
- Ruff: line-length 120, rules E/W/F/I/C/B/UP/N/RUF/Q
- LLM integration test model: `openrouter/deepseek/deepseek-v3.2`
- Import whitelist for Memory Programs: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb
- All tests that produce prompts (LLM calls, prompt construction, etc.) must use syrupy snapshots to capture the prompt content, so that prompt changes can be human-reviewed for semantic correctness
- Evaluator tests: use `mock_fn = _mock_completion_factory(...)` pattern, snapshot `mock_fn.captured_calls` for prompt verification
