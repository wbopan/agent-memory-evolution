# AGENTS.md

## Project

**Mstar** — "Every Task Deserves Its Own Memory Harness" (arXiv:2604.11811).

Mstar evolves *how* an LLM agent organizes and retrieves information. The memory strategy is a Python program (a **KBProgram**, called a *memory program* or *memory harness* in the paper) that defines data schemas, storage logic (`write`), and retrieval logic (`read`). Evolution mutates this program via LLM reflection, guided by benchmark performance. The task agent itself is fixed; only the KBProgram changes.

**Core selling points:**
1. **Open search space** — searches over executable Python code (arbitrary data structures + logic), not a closed set of module choices or NL rules.
2. **Task-specific optima** — the same system, same seeds, evolves structurally different programs on different tasks (e.g., multi-signal episodic index on LoCoMo vs deterministic action cache on ALFWorld). No universal optimal memory design exists.

## Repository Structure

- `src/mstar/` — Main source code
- `paper/` — LaTeX paper (git submodule). Main file: `main.tex` (not `neurips_2025.tex`). Sections in `sections/`.

## Build & Test

```bash
# Install (editable, with dev deps)
uv pip install -e ".[dev]"

# Tests (fast, no API keys)
uv run pytest tests/evolution/ -m "not llm" -v

# All tests (needs OPENROUTER_API_KEY; replays from disk cache by default)
uv run pytest tests/evolution/ -v

# Update snapshots
uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v

# Lint & format
uv run ruff check src/ && uv run ruff format src/
```

## Reproduction

Paper results (Table 1) are produced by `scripts/run_experiments.sh`. See `scripts/README.md` for the full reproduction guide.

**Important:** the CLI defaults differ from the paper's setup. The paper uses Azure models (`gpt-5.4-mini` for task/toolkit, `gpt-5.3-codex` with thinking effort medium for reflection, `openrouter/baai/bge-m3` for embeddings), 20 iterations, `--eval-train-ratio 2`, and per-dataset `--eval-static-size`/`--test-size`/`--test-train-ratio` values. The CLI defaults (DeepSeek v3.2, 3 iterations) are for quick local experimentation and will **not** reproduce paper numbers.

```bash
# Reproduce paper results (reads Azure credentials from environment)
bash scripts/run_experiments.sh

# See full reproduction instructions
cat scripts/README.md
```

## CLI Recipes

```bash
# Run evolution (LoCoMo, 20 iterations, paper-style validation split)
uv run python -m mstar.evolution \
  --dataset locomo --iterations 20 \
  --eval-static-size 60 --eval-rotate-size 5 --eval-train-ratio 2

# Run HealthBench for a specific category
uv run python -m mstar.evolution \
  --dataset healthbench --category emergency_referrals --iterations 20

# Evaluate a baseline (no evolution, test only)
uv run python -m mstar.evolution \
  --dataset locomo --seed-program src/mstar/baselines/dynamic_cheatsheet.py \
  --iterations 0 --test-size 100

# Quick dev iteration (fast, single conversation)
uv run python -m mstar.evolution \
  --dataset mini_locomo --iterations 3 --no-weave

# Resume an interrupted run
uv run python -m mstar.evolution --resume outputs/<run-dir>/

# Run experiment scripts
bash scripts/run_experiments.sh
```

### Key CLI Flags

| Flag | Purpose | Default |
|------|---------|---------|
| `--dataset` | Benchmark: `locomo`, `alfworld`, `healthbench`, `prbench`, `mini_locomo`, `kv_memory`, `state_bench`, `tau_bench`, `agentboard`, `nyt_connections` | `kv_memory` |
| `--iterations N` | Evolution iterations (0 = seed eval + test only) | 3 |
| `--category` | Filter dataset to a specific category (e.g., `emergency_referrals`, `legal`, `finance`) | — |
| `--eval-static-size` | Static val subset for scoring/selection (paper: 60 for LoCoMo) | 25 |
| `--eval-rotate-size` | Rotating val sample per iteration for reflection | 5 |
| `--eval-train-ratio` | Train items per val item during evolution (paper: 2) | 5 |
| `--seed-program` | Path to .py file or directory of seeds | `src/mstar/seeds/` |
| `--task-model` | LLM for task agent (JSON extraction, QA) | `openrouter/deepseek/deepseek-v3.2` |
| `--reflect-model` | LLM for code reflection/mutation | `openrouter/openai/gpt-5.3-codex` |
| `--toolkit-model` | LLM available inside KB programs via `toolkit.llm_completion()` | `openrouter/deepseek/deepseek-v3.2` |
| `--toolkit-budget` | LLM call budget per write/read call | 1 |
| `--judge-model` | LLM for rubric scoring (HealthBench, PRBench); defaults to `--task-model` | — |
| `--embedding-model` | Text embedding model for train/val subset selection | `openrouter/baai/bge-m3` |
| `--task-lm-thinking-effort` | Reasoning effort for task/toolkit LLM: `low`, `medium`, `high` | — |
| `--seed` | Random seed (data split) | 42 |
| `--evolution-seed` | Separate seed for evolution randomness (defaults to `--seed`) | — |
| `--seed-program` | Path to .py seed file or directory | `src/mstar/seeds/` |
| `--test-size` | Held-out test split size (-1 = loader-aware default, 0 = skip, N = hold N from val) | -1 |
| `--test-train-ratio` | Train items per test item for final eval (-1 = all train) | -1 |
| `--selection-strategy` | Parent selection: `softmax`, `max`, `recency_decay` | `softmax` |
| `--selection-softmax-temperature` | Softmax temperature for parent selection | 0.15 |
| `--selection-recency-decay-rate` | Decay rate per generation for recency_decay selection | 0.8 |
| `--freeze-instructions` | Ablation: only code evolves, instruction constants frozen | off |
| `--freeze-code` | Ablation: only instructions evolve, code structure frozen | off |
| `--max-fix-attempts` | Max compile-fix attempts per reflection | 3 |
| `--reflection-max-failed-cases` | Max failed cases in reflection prompt | 3 |
| `--reflection-max-train-examples` | Max training examples in reflection prompt | 1 |
| `--reflection-max-memory-log-chars` | Max chars for memory logs in reflection prompt (0 = exclude) | 0 |
| `--no-references` | Disable cross-program reference context in reflection (default: disabled) | on |
| `--batch-concurrency` | Max concurrent LLM calls in batch evaluation (paper: 64) | 64 |
| `--output-dir` | Explicit output directory path | — |
| `--no-weave` | Disable wandb/weave tracking | off |
| `--no-output` | Disable local output directory | off |
| `--weave-project` | Weave project name | `mstar` |
| `--resume` | Resume from `outputs/<dir>/` | — |
| `--azure-api-base` | Azure OpenAI endpoint URL (also reads `AZURE_API_BASE` env var) | — |
| `--azure-api-version` | Azure OpenAI API version | `2024-12-01-preview` |

Benchmark-specific kwargs as positional `key=value` (e.g., `eval_split=unseen` for ALFWorld).

## Architecture Overview

### What Is a KBProgram

A KBProgram is a Python module (called a *memory program* or *memory harness* in the paper) that defines **how** an agent organizes and retrieves information. The paper describes it as having three dimensions: Schema, Logic, and Instruction, backed by a whitelisted Toolkit.

- **`KnowledgeItem`** (dataclass) — schema for what to capture from incoming data
- **`Query`** (dataclass) — schema for retrieval requests
- **`KnowledgeBase`** (class) — `write(item, raw_text)` stores data, `read(query)` retrieves it, using a `Toolkit` (lists/heaps, SQLite, ChromaDB, budget-limited LLM)
- **4 instruction constants** — `INSTRUCTION_KNOWLEDGE_ITEM`, `INSTRUCTION_QUERY`, `INSTRUCTION_RESPONSE`, `ALWAYS_ON_KNOWLEDGE` — injected into task agent prompts

The task agent is fixed. Evolution changes only the KBProgram. Performance differences are purely attributable to the memory strategy.

### Evolution Pipeline

```
Seeds → ProgramPool
  ↓
For each iteration:
  1. Sample parent (softmax on static val scores)
  2. Evaluate: ingest train via write() → score on val via read()
  3. Reflect: LLM sees code + failed/success cases → V4A patch → child
  4. Compile-fix loop (up to 3 retries via --max-fix-attempts)
  5. Evaluate child → add to pool unconditionally
  ↓
Final test eval using pool's best program
```

### Evaluation Pipeline (evaluator.py)

Two training modes, inferred from data:
- **Ingestion mode** (train items have `raw_text`): batch `write(item, raw_text)` into KB
- **Interactive mode** (train items are QA-only): 3-round query→answer→feedback loop

Val evaluation is two-phase:
1. **Retrieve**: generate Query → call `kb.read()` for all val items
2. **Score**: either default path (LLM answers + string scorer like TokenF1) or custom `ValScorer` (e.g., ALFWorld episodes, rubric grading)

**Split Validation** (`strategies.py:SplitValidation`): val is split into *static* (scoring, never shown to reflector) and *rotating* (sampled each iteration for reflection). This is the recommended strategy and the one used in the paper.

### Runtime Constraints

- `read()` output: max 3000 chars (truncated → score 0)
- `write()`/`read()` timeout: 60s per call
- LLM budget: reset per write/read call (default 1 call, configurable via `--toolkit-budget`)
- Allowed imports: `json`, `re`, `math`, `hashlib`, `collections`, `dataclasses`, `typing`, `datetime`, `textwrap`, `sqlite3`, `chromadb`

### Module Map (`src/mstar/evolution/`)

| Module | Role |
|--------|------|
| `types.py` | Core types: `Dataset`, `KBProgram`, `DataItem`, `EvalResult`, `FailedCase`, `ProgramPool`, `SelectionStrategy` variants |
| `evaluator.py` | Train ingestion + val scoring. Two LLM roles: task agent + toolkit LLM |
| `reflector.py` | LLM reflection → V4A patch → compile-fix loop. Returns guaranteed-valid `KBProgram` |
| `sandbox.py` | `compile_kb_program()`: AST parse → class/import/constant checks → exec. `smoke_test()` |
| `toolkit.py` | Resource bundle: SQLite + ChromaDB + budget-limited LLM + MemoryLogger |
| `prompts.py` | All prompt templates. XML-tagged reflection prompt with dual-dimension guidance (prompt optimization + memory design) |
| `strategies.py` | `SplitValidation` (recommended), `FullDataset`, `FixedRepresentative`, `RotatingBatch` |
| `loop.py` | Main evolution loop: pool → sample → reflect → evaluate → add |
| `patcher.py` | V4A patch application via `codex-apply-patch` |
| `checkpoint.py` | Serialization for `--resume` support |
| `batching.py` | K-means clustering + facility location for representative val/train selection |
| `__main__.py` | CLI entry point |

### Benchmarks (`src/mstar/benchmarks/`)

The four benchmarks used in the paper (Table 1) are marked with **(paper)**. The remaining benchmarks are auxiliary and were not used in the paper's main evaluation.

| Module | Scorer | Train type | Notes |
|--------|--------|------------|-------|
| `locomo.py` | TokenF1 | `raw_text` (conversations) | **(paper)** 10 convs, ~1986 QA pairs |
| `alfworld.py` | Binary success (TextWorld) | `raw_text` (trajectories) | **(paper)** seen + unseen splits; alfworld installs by default |
| `healthbench.py` | RubricValScorer (LLM judge) | `raw_text` (conv + ideal) | **(paper)** categories: `health_data_tasks`, `emergency_referrals`. **Expensive**: per-criterion LLM grading |
| `prbench.py` | RubricValScorer (LLM judge) | `raw_text` (task + expert) | **(paper)** categories: `legal`, `finance`. **Expensive**: supports negative scoring |
| `mini_locomo.py` | TokenF1 | `raw_text` | Auxiliary (not in paper): single-conv subset for dev |
| `kv_memory.py` | ExactMatch | `raw_text` | Auxiliary (not in paper): toy benchmark |
| `state_bench/` | — | — | Auxiliary (not in paper) |
| `tau_bench/` | — | — | Auxiliary (not in paper) |
| `agentboard/` | — | — | Auxiliary (not in paper) |
| `nyt_connections/` | — | — | Auxiliary (not in paper) |

### Baselines (`src/mstar/baselines/`)

Evaluate with `--seed-program src/mstar/baselines/<name>.py --iterations 0`:

`no_memory.py`, `vanilla_rag.py`, `trajectory_retrieval.py`, `reasoning_bank.py`, `dynamic_cheatsheet.py`, `g_memory.py`, `mem0.py`, `awm.py`

Paper Table 1 baselines: No Memory, Vector Search (= `vanilla_rag` / `seeds/vector_search.py`), G-Memory, Mem0, Trajectory Retrieval, ReasoningBank, Dynamic Cheatsheet, GEPA, GEPA+Vector Search.

Notes:
- **GEPA** is run via `scripts/run_gepa_baseline.py` (not a file in `baselines/`).
- `awm.py` exists in `baselines/` but AWM is **not** a Table-1 baseline — it appears only in related work.

### Other Modules

- `datasets.py` — `@register_dataset` registry + `load_dataset()` dispatcher
- `cache.py` — litellm disk/redis/r2/s3 caching
- `logging/` — `RichLogger`, `RunOutputManager`, `LLMCallLogger` (litellm callback)

## Environment

- Python 3.12+, `from __future__ import annotations` everywhere
- Package manager: `uv`
- `OPENROUTER_API_KEY` for real LLM calls; Azure credentials (`AZURE_API_BASE` + `AzureCliCredential`) for paper reproduction
- Outputs: `outputs/<run-name>/` (see structure below)
- Data: `data/` (gitignored, auto-downloaded by each benchmark)

### Output Run Structure

```
outputs/<run-name>/
├── config.json          # CLI flags and hyperparameters
├── run.log              # Full console log
├── state.json           # Checkpoint: ProgramPool + iteration state (used by --resume)
├── programs/
│   ├── seed_0.py        # Initial seed programs
│   ├── seed_1.py
│   ├── seed_2.py
│   ├── iter_1.py        # Mutated program from each iteration
│   ├── iter_2.py
│   └── ...
└── summary.json         # Final results (only present if run completed)
```

- **Baseline runs** (`--iterations 0`): only `seed_*.py` in `programs/`, no `iter_*.py`.
- **Incomplete runs**: have `state.json` but no `summary.json`. Resume with `--resume outputs/<run-name>/`.
- Run names follow `t1-<dataset>-<config>` convention (e.g., `t1-locomo-ours`, `t1-hb-emergency-vanilla-rag`).

Outputs are produced by `scripts/run_experiments.sh`, stored under `outputs/<run-name>/`, and version-tracked via DVC (`outputs.dvc`). The `outputs/` directory is not present in a fresh clone — pull via DVC to retrieve paper run artifacts.

## Conventions

- Ruff: line-length 120, rules E/W/F/I/C/B/UP/N/RUF/Q. S (bandit) NOT enabled — never add `# noqa: S...`
- All LLM calls use user-only messages (no system prompts)
- KBProgram `write()` signature: `write(self, item, raw_text="")`
- Test markers: `@pytest.mark.llm` (real LLM), `@pytest.mark.uses_chroma` (real ChromaDB), `@pytest.mark.alfworld`
- Disk cache at `tests/evolution/.llm_cache/` — committed to git, replays LLM tests without API keys
- Syrupy snapshots in `tests/evolution/__snapshots__/` — run `--snapshot-update` after prompt changes
- Prompt changes cascade: `prompts.py` → `test_prompts.ambr` + `test_reflector.ambr`
