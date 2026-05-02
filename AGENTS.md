# AGENTS.md

## Project

**Engram** — "Memory Is a Program: Evolving Agent Memory Through Executable Code Search".

Engram evolves *how* an LLM agent organizes and retrieves information. The memory strategy is a Python program (a **KBProgram**) that defines data schemas, storage logic (`write`), and retrieval logic (`read`). Evolution mutates this program via LLM reflection, guided by benchmark performance. The task agent itself is fixed; only the KBProgram changes.

**Core selling points:**
1. **Open search space** — searches over executable Python code (arbitrary data structures + logic), not a closed set of module choices or NL rules.
2. **Task-specific optima** — the same system, same seeds, evolves structurally different programs on different tasks (e.g., multi-signal episodic index on LoCoMo vs deterministic action cache on ALFWorld). No universal optimal memory design exists.

## Repository Structure

- `src/programmaticmemory/` — Main source code
- `paper/` — LaTeX paper (git submodule). Main file: `main.tex` (not `neurips_2025.tex`). Sections in `sections/`.
- `TASKS.md` — Task board

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

## CLI Recipes

```bash
# Run evolution (LoCoMo, 10 iterations, split validation)
uv run python -m programmaticmemory.evolution \
  --dataset locomo --iterations 10 \
  --eval-strategy split --eval-static-size 60

# Evaluate a baseline (no evolution, test only)
uv run python -m programmaticmemory.evolution \
  --dataset locomo --seed-program src/programmaticmemory/baselines/dynamic_cheatsheet.py \
  --iterations 0 --eval-strategy none --test-size 100

# Quick dev iteration (fast, single conversation)
uv run python -m programmaticmemory.evolution \
  --dataset mini_locomo --iterations 3 --no-weave

# Resume an interrupted run
uv run python -m programmaticmemory.evolution --resume outputs/<run-dir>/

# Run experiment scripts
bash scripts/run_experiments.sh table1

# Paper (from paper/ submodule)
cd paper && latexmk -pdf main.tex && open main.pdf
```

### Key CLI Flags

| Flag | Purpose | Default |
|------|---------|---------|
| `--dataset` | Benchmark: `locomo`, `alfworld`, `healthbench`, `prbench`, `mini_locomo`, `kv_memory` | required |
| `--iterations N` | Evolution iterations (0 = seed eval + test only) | 20 |
| `--eval-strategy` | Per-iteration val eval: `split` (recommended), `none`, `full` | `split` |
| `--eval-static-size` | Static val subset for scoring in split strategy | 25 |
| `--eval-rotate-size` | Rotating val sample for reflection in split strategy | 5 |
| `--eval-train-ratio` | Train items per val item during evolution | 5 |
| `--seed-program` | Path to .py file or directory of seeds | `src/programmaticmemory/seeds/` |
| `--task-model` | LLM for task agent (JSON extraction, QA) | `openrouter/deepseek/deepseek-v3.2` |
| `--reflect-model` | LLM for code reflection/mutation | `openrouter/openai/gpt-5.3-codex` |
| `--toolkit-model` | LLM available inside KB programs via `toolkit.llm_completion()` | task-model |
| `--judge-model` | LLM for rubric scoring (HealthBench, PRBench) | task-model |
| `--freeze-instructions` | Ablation: only code evolves, instruction constants frozen | off |
| `--freeze-code` | Ablation: only instructions evolve, code structure frozen | off |
| `--selection-strategy` | Parent selection: `softmax` (T=0.15), `max`, `recency_decay` | `softmax` |
| `--test-size N` | Held-out test split size (0=skip, -1=copy val) | 0 |
| `--no-weave` | Disable wandb/weave tracking | off |
| `--resume` | Resume from `outputs/<dir>/` | — |

Benchmark-specific kwargs as positional `key=value` (e.g., `eval_split=unseen` for ALFWorld).

## Architecture Overview

### What Is a KBProgram

A KBProgram is a Python module that defines **how** an agent organizes and retrieves information:

- **`KnowledgeItem`** (dataclass) — schema for what to capture from incoming data
- **`Query`** (dataclass) — schema for retrieval requests
- **`KnowledgeBase`** (class) — `write(item, raw_text)` stores data, `read(query)` retrieves it, using a `Toolkit` (SQLite + ChromaDB + budget-limited LLM)
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
  4. Compile-fix loop (up to 3 retries)
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

**Split Validation** (`strategies.py:SplitValidation`): val is split into *static* (scoring, never shown to reflector) and *rotating* (sampled each iteration for reflection). This is the recommended strategy.

### Runtime Constraints

- `read()` output: max 3000 chars (truncated → score 0)
- `write()`/`read()` timeout: 60s per call
- LLM budget: reset per write/read call (default 1 call, configurable via `--toolkit-budget`)
- Allowed imports: `json`, `re`, `math`, `hashlib`, `collections`, `dataclasses`, `typing`, `datetime`, `textwrap`, `sqlite3`, `chromadb`

### Module Map (`src/programmaticmemory/evolution/`)

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

### Benchmarks (`src/programmaticmemory/benchmarks/`)

| Module | Scorer | Train type | Notes |
|--------|--------|------------|-------|
| `locomo.py` | TokenF1 | `raw_text` (conversations) | 10 convs, ~1986 QA pairs |
| `alfworld.py` | Binary success (TextWorld) | `raw_text` (trajectories) | Requires `pip install -e ".[alfworld]"` |
| `healthbench.py` | RubricValScorer (LLM judge) | `raw_text` (conv + ideal) | **Expensive**: per-criterion LLM grading |
| `prbench.py` | RubricValScorer (LLM judge) | `raw_text` (task + expert) | **Expensive**: supports negative scoring |
| `mini_locomo.py` | TokenF1 | `raw_text` | Single-conv subset for dev |
| `kv_memory.py` | ExactMatch | `raw_text` | Toy benchmark |

### Baselines (`src/programmaticmemory/baselines/`)

Evaluate with `--seed-program src/programmaticmemory/baselines/<name>.py --iterations 0`:

`no_memory.py`, `vanilla_rag.py`, `trajectory_retrieval.py`, `reasoning_bank.py`, `dynamic_cheatsheet.py`, `g_memory.py`, `mem0.py`, `awm.py`

### Other Modules

- `datasets.py` — `@register_dataset` registry + `load_dataset()` dispatcher
- `cache.py` — litellm disk/redis/r2/s3 caching
- `logging/` — `RichLogger`, `RunOutputManager`, `LLMCallLogger` (litellm callback)

## Environment

- Python 3.12+, `from __future__ import annotations` everywhere
- Package manager: `uv`
- `OPENROUTER_API_KEY` for real LLM calls
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

#### Current runs in `outputs/`

| Run | Dataset | Config | Target Iters | Status |
|-----|---------|--------|-------------|--------|
| `t1-locomo-ours` | LoCoMo | evolution | 20 | **done** |
| `t1-hb-emergency-ours` | HealthBench emergency | evolution | 20 | interrupted (6/20) |
| `t1-alfworld-seen-ours` | ALFWorld seen | evolution | 20 | interrupted (2/20) |
| `t1-alfworld-unseen-ours` | ALFWorld unseen | evolution | 20 | interrupted (0/20) |
| `t1-pr-legal-ours` | PRBench legal | evolution | 20 | interrupted (3/20) |
| `t1-*-no-memory` | various | no_memory baseline | 0 | done |
| `t1-*-vanilla-rag` | various | vanilla_rag baseline | 0 | done |

## Conventions

- Ruff: line-length 120, rules E/W/F/I/C/B/UP/N/RUF/Q. S (bandit) NOT enabled — never add `# noqa: S...`
- All LLM calls use user-only messages (no system prompts)
- KBProgram `write()` signature: `write(self, item, raw_text="")`
- Test markers: `@pytest.mark.llm` (real LLM), `@pytest.mark.uses_chroma` (real ChromaDB), `@pytest.mark.alfworld`
- Disk cache at `tests/evolution/.llm_cache/` — committed to git, replays LLM tests without API keys
- Syrupy snapshots in `tests/evolution/__snapshots__/` — run `--snapshot-update` after prompt changes
- Prompt changes cascade: `prompts.py` → `test_prompts.ambr` + `test_reflector.ambr`
