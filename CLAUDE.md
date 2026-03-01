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
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorOffline::test_basic_offline_evaluation -v

# Lint & format (also runs as pre-commit hooks)
uv run ruff check src/
uv run ruff format src/

# Run evolution on kv_memory benchmark
uv run python -m programmaticmemory.evolution --iterations 3 num_items=10
# Run on mini_locomo (fast, single conversation, TokenF1Scorer)
uv run python -m programmaticmemory.evolution --dataset mini_locomo --iterations 3 --no-weave
# Benchmark-specific kwargs passed as positional key=value args
# --train-size / --val-size to limit dataset size
# Weave/wandb tracing is ON by default; disable with --no-weave
# --seed 42 (default), --weave-project programmaticmemory (default)
# --dataset locomo/tau_bench/alfworld/mini_locomo for other benchmarks
# Local output directory (default: outputs/YYYY-MM-DD-HH-mm-SS/)
# Contains config.json, run.log, summary.json, llm_calls/ with per-call JSON
# Disable with --no-output
```

## Architecture

LLMs can retrieve information but can't figure out *how to organize* it. This project evolves the **organizing strategy itself** — as executable Python code.

A **Memory Program** is a Python module defining three classes:
- `Observation` — what to capture from incoming information (dataclass fields)
- `Query` — how to parameterize a retrieval request (dataclass fields)
- `Memory` — `write(obs)` / `read(query)` logic using a `Toolkit` (SQLite, ChromaDB, LLM)

The task agent (fixed prompt, fixed model) uses whatever Memory Program it's given. Evolution changes *only* the Memory Program code — so performance differences are purely attributable to the memory strategy.

```
Seed: append everything, return everything
  → Evaluate on benchmark → Reflect on failures → Mutate code → repeat
  → Evolved: task-specific schemas, structured storage, selective retrieval
```

### Evolution Loop (`evolution/loop.py`)

```
Evaluate(program, data) → EvalResult → Reflect(code, failures) → new MemoryProgram → repeat
```

Greedy serial: one candidate, one child per iteration, accept if score improves. Reflector handles compile/smoke-test validation internally; loop.py does not call `smoke_test`.

### Key Modules (all under `src/programmaticmemory/evolution/`)

- **types.py** — Core types: `Scorer` protocol, `Dataset` (bundles train/val/test/scorer), `MemoryProgram`, `DataItem`, `EvalResult` (includes `runtime_violation` field), `FailedCase`, `TrainExample`, `EvolutionState`
- **evaluator.py** — Two training pipelines, inferred from data (no explicit mode enum). Train items with `raw_text` → batch observation ingestion (1 round). Train items without `raw_text` (QA only) → interactive training (3 rounds: query → answer → feedback-driven obs). Val is always 2 rounds (query → read → answer). Both paths capture `train_examples` for reflection. Uses `ExactMatchScorer`, `TokenF1Scorer`, or `LLMJudgeScorer`. Runtime guards: `_guarded_write`/`_guarded_read` wrap memory ops with timeout + output-size limits, raising `RuntimeViolationError` on violation.
- **reflector.py** — Calls LLM with current code + failed cases, extracts last `` ```python ``` `` block as the improved program. Includes compile-fix loop: validates code via `compile_memory_program` + `smoke_test`, retries with a dedicated fix prompt up to `max_fix_attempts` (default 3). Returned `MemoryProgram` is guaranteed valid.
- **sandbox.py** — `compile_memory_program()`: AST parse → check 3 required classes → validate import whitelist → exec. Returns `CompileError` on failure. Also: `extract_dataclass_schema()` (outputs commented JSON example, includes `field(metadata={"description": ...})` if present), `smoke_test()`.
- **toolkit.py** — Resource bundle (`db`: SQLite, `chroma`: ChromaDB, `llm_completion`: budget-limited LLM, `logger`). Instantiate via `Toolkit(config)`, created fresh per evaluation.
- **prompts.py** — All prompt templates. `INITIAL_MEMORY_PROGRAM` is the baseline (append-all/return-all). No system prompts — all LLM instructions are merged into user prompts via `build_reflection_user_prompt` and `build_compile_fix_prompt`.
- **benchmarks/kv_memory.py** — Simple factual recall (`ExactMatchScorer`). Train has `raw_text`.
- **benchmarks/locomo.py** — LoCoMo multi-session conversation QA (`TokenF1Scorer`). Train has `raw_text`.
- **benchmarks/mini_locomo.py** — Single-conversation LoCoMo subset for fast iteration (`TokenF1Scorer`). Train has `raw_text`.
- **benchmarks/tau_bench.py** — tau-bench retail/airline task completion (`ExactMatchScorer`). Train is QA-only.
- **benchmarks/alfworld.py** — ALFWorld embodied task key-element recall (`ExactMatchScorer`). Train is QA-only.
- **benchmarks/_download.py** — Shared download utilities (stdlib only: urllib, tarfile, zipfile).
- **benchmarks/__init__.py** — Imports all benchmark modules to trigger `@register_dataset` decorators. Must be updated when adding new benchmarks.

### Other Modules (under `src/programmaticmemory/`)

- **cache.py** — `configure_cache("disk"|"redis"|"r2"|"s3")` / `disable_cache()` for litellm caching.
- **datasets.py** — `register_dataset(name)` decorator stores dataset loader functions. `load_dataset(name, ...)` calls the loader and applies train/val size limits. Auto-imports benchmarks package on first use.
- **logging/experiment_tracker.py** — Experiment tracking via wandb/weave.
- **logging/run_output.py** — `RunOutputManager` + `LLMCallLogger` (litellm `CustomLogger` callback). Creates timestamped `outputs/` dir with config, logs, summary, and per-call LLM JSON. Zero-invasive via litellm callback; thread-safe.
- **utils/stop_condition.py** — `StopperProtocol` and `SignalStopper` for graceful stopping via signal handlers.

### Two Separate LLM Roles

1. **Task agent** (`evaluator.py:_batch_llm_call`) — Fixed model that generates Observation/Query JSON and answers questions via `litellm.batch_completion`. Separate from the memory program.
2. **Toolkit LLM** (`toolkit.py:Toolkit.llm_completion`) — Available to Memory Programs via `toolkit.llm_completion()`, budget-limited (default 50 calls), with tenacity retry.

## Test Infrastructure

- **Pytest markers**: `@pytest.mark.llm` (real LLM calls), `@pytest.mark.uses_chroma` (real ChromaDB instead of mock)
- **Disk cache**: `tests/evolution/.llm_cache/` — litellm disk cache committed to git, so LLM tests replay without API keys. Configured in `tests/evolution/conftest.py` via session-scoped fixture that wraps `litellm.completion` with `caching=True`.
- **Syrupy snapshots**: `tests/evolution/__snapshots__/*.ambr` — 4 snapshot files:
  - `test_prompts.ambr` — prompt template outputs from `build_*` functions
  - `test_evaluator.ambr` — full `captured_calls` (all messages sent to mock LLM per test)
  - `test_reflector.ambr` — reflection LLM call messages (user-only prompts)
  - `test_llm_integration.ambr` — `{prompt, output}` dicts with real LLM responses
- **ChromaDB mock**: `conftest.py` auto-mocks `chromadb.EphemeralClient`; opt out with `@pytest.mark.uses_chroma`.

## Knowledge Files

- `knowledge/evolution-design.md` — Design document for the evolution system (phases, flows, testing strategy)
- `knowledge/system-design-original.md` — Original system design notes (motivation, Observation/Query/Memory lifecycle, offline/online specs)

## Environment Setup

- Package manager: `uv` (required)
- `OPENROUTER_API_KEY` — needed for real LLM calls and snapshot updates
- LLM tests replay from disk cache (`tests/evolution/.llm_cache/`) without API keys
- Worktree directory: `.worktrees/` (gitignored, used for feature branch isolation)
- Downloaded dataset files go in `data/` (gitignored). Each benchmark module has `ensure_data()` for auto-download.

## Conventions

- Python 3.12+, `from __future__ import annotations` in all modules
- Ruff: line-length 120, rules E/W/F/I/C/B/UP/N/RUF/Q. S (bandit) rules are NOT enabled — do not add `# noqa: S...` directives (causes RUF100 errors)
- Default model for all LLM roles: `openrouter/deepseek/deepseek-v3.2`
- Import whitelist for Memory Programs: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb
- A Memory Program is a **complete Python module**: import statements + three class definitions (Observation, Query, Memory). LLM outputs the full module source.
- All tests that produce prompts (LLM calls, prompt construction, etc.) must use syrupy snapshots to capture the prompt content, so that prompt changes can be human-reviewed for semantic correctness
- Evaluator tests use `_make_batch_mock(response_batches)` + `mock_litellm.batch_completion = batch_mock` for all evaluation pipeline tests.
- All LLM calls use user-only messages (no system prompts). Instructions are merged into the user prompt.
- Memory Program logger interface is `toolkit.logger.debug(message)` (`log()` kept as backward-compatible alias).
