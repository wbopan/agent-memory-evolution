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

A **Knowledge Base Program** (code type: `KBProgram`) is a Python module defining three classes and four module-level string constants:
- `KnowledgeItem` — what to capture from incoming information (dataclass fields)
- `Query` — how to parameterize a retrieval request (dataclass fields)
- `KnowledgeBase` — `write(item, raw_text)` / `read(query)` logic using a `Toolkit` (SQLite, ChromaDB, LLM)
- `INSTRUCTION_KNOWLEDGE_ITEM` / `INSTRUCTION_QUERY` / `INSTRUCTION_RESPONSE` — directive sentences inserted into task agent prompts for knowledge item generation, query generation, and answer generation respectively
- `ALWAYS_ON_KNOWLEDGE` — persistent knowledge injected into every task agent prompt. Unlike INSTRUCTION_* (output format), this provides always-on context. Can be empty.

The task agent (fixed prompt, fixed model) uses whatever Knowledge Base Program it's given. Evolution changes *only* the Knowledge Base Program code (including instructions) — so performance differences are purely attributable to the knowledge base strategy.

```
Seed: append everything, return everything
  → Evaluate on benchmark → Reflect on failures → Mutate code → repeat
  → Evolved: task-specific schemas, structured storage, selective retrieval
```

### Evolution Loop (`evolution/loop.py`)

```
Evaluate(program, data) → EvalResult → Reflect(code, failures) → new KBProgram → repeat
```

Serial: one candidate, one child per iteration. By default, always continues with the latest child (even if score degrades); `drop_degraded_program=True` reverts to best on degradation. Reflector handles compile/smoke-test validation internally; loop.py does not call `smoke_test`.

### Key Modules (all under `src/programmaticmemory/evolution/`)

- **types.py** — Core types: `Scorer` protocol, `ValScorer` protocol (pluggable val scoring), `Dataset` (bundles train/val/test/scorer/val_scorer), `KBProgram`, `DataItem` (includes `metadata: dict` for benchmark-specific data), `EvalResult` (includes `runtime_violation`, `failed_cases`, `success_cases` fields), `FailedCase` (reused for both failed and success cases), `TrainExample`, `EvolutionState`
- **evaluator.py** — Two training pipelines, inferred from data (no explicit mode enum). Train items with `raw_text` → batch knowledge item ingestion (1 round). Train items without `raw_text` (QA only) → interactive training (3 rounds: query → answer → feedback-driven KnowledgeItem). Val has two phases: (1) shared KB retrieval (`_retrieve_for_val`), (2) pluggable scoring — either `_default_answer_and_score` (LLM answer + string scorer) or custom `ValScorer.score_batch` via `_val_scorer_path`. Both paths capture `train_examples` for reflection. Uses `ExactMatchScorer`, `TokenF1Scorer`, or `LLMJudgeScorer`. Runtime guards: `_guarded_write(kb, item, raw_text)`/`_guarded_read` wrap memory ops with timeout + output-size limits, raising `RuntimeViolationError` on violation. `raw_text` is a required positional parameter.
- **reflector.py** — Calls LLM with current code + failed cases + success cases, extracts last `` ```python ``` `` block as the improved program. Includes compile-fix loop: validates code via `compile_kb_program` + `smoke_test`, retries with a dedicated fix prompt up to `max_fix_attempts` (default 3). Returned `KBProgram` is guaranteed valid.
- **sandbox.py** — `compile_kb_program()`: AST parse → check 3 required classes (KnowledgeItem, Query, KnowledgeBase) → validate import whitelist → exec → check 4 required constants. Returns `CompiledProgram` (ki_cls, query_cls, kb_cls, instruction_knowledge_item, instruction_query, instruction_response, always_on_knowledge) on success, `CompileError` on failure. Also: `extract_dataclass_schema()` (outputs commented JSON example, includes `field(metadata={"description": ...})` if present), `smoke_test()`.
- **toolkit.py** — Resource bundle (`db`: SQLite, `chroma`: ChromaDB, `llm_completion`: budget-limited LLM, `logger`). Instantiate via `Toolkit(config)`, created fresh per evaluation.
- **prompts.py** — All prompt templates. `INITIAL_KB_PROGRAM` is the baseline (summary+observations dual-list, `write(item, raw_text)`). No system prompts — all LLM instructions are merged into user prompts via `build_reflection_user_prompt` and `build_compile_fix_prompt`. Reflection prompt uses XML tags (`<interface_spec>`, `<current_program>`, `<write_examples>`, `<success_cases>`, `<failed_cases>`, etc.) for structure. `ReflectionPromptConfig` controls limits (max failed/success cases, max examples, memory log budget).
- **benchmarks/kv_memory.py** — Simple factual recall (`ExactMatchScorer`). Train has `raw_text`.
- **benchmarks/locomo.py** — LoCoMo multi-session conversation QA (`TokenF1Scorer`). Train has `raw_text`.
- **benchmarks/mini_locomo.py** — Single-conversation LoCoMo subset for fast iteration (`TokenF1Scorer`). Train has `raw_text`.
- **benchmarks/tau_bench.py** — tau-bench retail/airline task completion (`ExactMatchScorer`). Train is QA-only.
- **benchmarks/alfworld.py** — ALFWorld embodied task completion. Train has `raw_text` (structured trajectory metadata). Val uses `ALFWorldValScorer` for real TextWorld env interaction (binary success); falls back to `ExactMatchScorer` if `alfworld` package not installed. Requires `pip install -e ".[alfworld]"` for env interaction.
- **benchmarks/nyt_connections.py** — NYT Connections word-grouping puzzles (`ConnectionsScorer`, partial credit 0.25/group). Train is QA-only. Data from HuggingFace (652 puzzles).
- **benchmarks/_download.py** — Shared download utilities (stdlib only: urllib, tarfile, zipfile).
- **benchmarks/__init__.py** — Imports all benchmark modules to trigger `@register_dataset` decorators. Must be updated when adding new benchmarks.
- **patcher.py** — `apply_patch()`: thin wrapper around `codex-apply-patch` for applying diffs to Knowledge Base Program source code.

### Other Modules (under `src/programmaticmemory/`)

- **cache.py** — `configure_cache("disk"|"redis"|"r2"|"s3")` / `disable_cache()` for litellm caching.
- **datasets.py** — `register_dataset(name)` decorator stores dataset loader functions. `load_dataset(name, ...)` calls the loader and applies train/val size limits. Auto-imports benchmarks package on first use.
- **logging/experiment_tracker.py** — Experiment tracking via wandb/weave.
- **logging/run_output.py** — `RunOutputManager` + `LLMCallLogger` (litellm `CustomLogger` callback). Creates timestamped `outputs/` dir with config, logs, summary, and per-call LLM JSON. Zero-invasive via litellm callback; thread-safe.
- **logging/logger.py** — `RichLogger` (global singleton via `get_logger()`), `set_logger()` for file-tee replacement, `LoggerProtocol`. User-facing progress output with log-level headers (`EVOLUTION`, `EVAL`, `REFLECT`, `CONFIG`, `OUTPUT`).
- **utils/stop_condition.py** — `StopperProtocol` and `SignalStopper` for graceful stopping via signal handlers.

### Two Separate LLM Roles

1. **Task agent** (`evaluator.py:_batch_llm_call`) — Fixed model that generates KnowledgeItem/Query JSON and answers questions via `litellm.batch_completion`. Separate from the knowledge base program.
2. **Toolkit LLM** (`toolkit.py:Toolkit.llm_completion`) — Available to Knowledge Base Programs via `toolkit.llm_completion()`, budget-limited (default 50 calls), with tenacity retry.

## Test Infrastructure

- **Pytest markers**: `@pytest.mark.llm` (real LLM calls), `@pytest.mark.uses_chroma` (real ChromaDB instead of mock), `@pytest.mark.alfworld` (requires alfworld package)
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
- Default models for evolution runs: `openrouter/deepseek/deepseek-v3.2` (task + toolkit), `openrouter/openai/gpt-5.3-codex` (reflection). LLM integration tests use `openrouter/openai/gpt-5.1-codex-mini` (task) and `openrouter/openai/gpt-5.3-codex` (reflection).
- Import whitelist for Knowledge Base Programs: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb
- A Knowledge Base Program is a **complete Python module**: four module-level string constants (INSTRUCTION_KNOWLEDGE_ITEM, INSTRUCTION_QUERY, INSTRUCTION_RESPONSE, ALWAYS_ON_KNOWLEDGE) + three class definitions (KnowledgeItem, Query, KnowledgeBase). LLM outputs the full module source.
- All tests that produce prompts (LLM calls, prompt construction, etc.) must use syrupy snapshots to capture the prompt content, so that prompt changes can be human-reviewed for semantic correctness
- Prompt template changes in `prompts.py` cascade to snapshots in `test_prompts.ambr` AND `test_reflector.ambr` — always run `--snapshot-update` for both after editing prompts
- LLM disk cache keys include all API parameters (model, messages, temperature, max_tokens, response_format). Changing any of these invalidates cached responses, requiring re-running LLM tests with an API key. Never delete `.llm_cache/cache.db` between individual test reruns — cache entries are shared across tests. Only delete as last resort, then run ALL LLM tests in one shot.
- LLM integration tests use two model tiers: `MODEL` (gpt-5.1-codex-mini, cheap) for task agent calls only, `REFLECT_MODEL` (gpt-5.3-codex, stronger) for ALL code generation (reflection, compile-fix, runtime-fix). Never use `MODEL` for `Reflector` calls.
- Inline test programs that use `write()` must include the `raw_text` parameter: `def write(self, item, raw_text=""):` — smoke_test passes `raw_text` to `kb.write()`.
- `_batch_llm_call` supports `json_mode=True` for knowledge item/query generation (adds `response_format={"type": "json_object"}`). Answer generation calls leave it off.
- Evaluator tests use `_make_batch_mock(response_batches)` + `mock_litellm.batch_completion = batch_mock` for all evaluation pipeline tests.
- Inline test programs that reach execution (step 4+) in `compile_kb_program` must include `INSTRUCTION_KNOWLEDGE_ITEM`, `INSTRUCTION_QUERY`, `INSTRUCTION_RESPONSE`, and `ALWAYS_ON_KNOWLEDGE` or they'll get `CompileError`. Programs that fail earlier (syntax/class/import checks) don't need them.
- All LLM calls use user-only messages (no system prompts). Instructions are merged into the user prompt.
- Knowledge Base Program logger interface is `toolkit.logger.debug(message)` (`log()` kept as backward-compatible alias).
- `DataItem.metadata` carries benchmark-specific data (e.g., `{"game_file": str, "task_type": str}` for ALFWorld val items). Defaults to empty dict.
- `Dataset.val_scorer` (optional `ValScorer`) overrides the default LLM-answer + string-compare val scoring. When set, evaluator calls `val_scorer.score_batch()` after shared KB retrieval instead of the default answer generation path.
- Val evaluation is two-phase: (1) `_retrieve_for_val` generates Query + calls `kb.read()` for all items, (2) either `_default_answer_and_score` (LLM answers + scorer) or `_val_scorer_path` (custom scorer, e.g. ALFWorld episodes). Both paths must include retrieval conversation in `FailedCase.conversation_history` for reflection diagnostics. `_val_scorer_path` builds 3-message history (query prompt, query JSON, retrieved prompt); default path adds a 4th (LLM answer).
