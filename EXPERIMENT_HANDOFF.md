# Experiment Handoff Guide

This document explains how to set up the environment and run all experiments for the Engram paper (NeurIPS 2026).

## 1. Environment Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- OpenRouter API key (for LLM calls)

### Install

```bash
git clone <repo-url> && cd programmaticmemory
git submodule update --init
uv pip install -e ".[dev]"
```

### API Keys

Set the following environment variable:

```bash
export OPENROUTER_API_KEY="sk-or-v1-..."
```

This key is used for:
- **Task agent** (deepseek-v3.2) — generates knowledge items, queries, answers
- **Reflect agent** (gpt-5.3-codex) — generates/mutates Python code during evolution
- **Embedding** (bge-m3) — used for train/val subset selection

If you don't have an OpenRouter key or the embedding API is unavailable, add `--embedding-model local` to use a local ONNX-based embedding model (auto-downloaded, ~50MB).

### Verify Installation

```bash
# Quick sanity check (no API calls)
uv run pytest tests/evolution/ -m "not llm" -v

# Smoke test with real API (costs ~$0.02)
uv run python -m programmaticmemory.evolution \
  --dataset locomo --test-size 3 --test-train-ratio 1 \
  --iterations 0 --eval-strategy none --no-weave \
  --seed-program src/programmaticmemory/baselines/no_memory.py \
  --output-dir outputs/smoke-test
```

## 2. Experiments Overview

We have 3 scripts covering 2 tables:

| Script | Table | Runs | Description |
|--------|-------|------|-------------|
| `run_experiments.sh table1` | Table 1 | 21 | No Memory / Vanilla RAG / Ours × 7 datasets |
| `run_experiments.sh baselines` | Table 1 | 35 | 5 ALMA baselines × 7 datasets |
| `run_ablation.sh` | Table 2 | 4 | 4 ablation variants × LoCoMo |

**7 datasets**: LoCoMo, ALFWorld unseen, ALFWorld seen, HealthBench data_tasks, HealthBench emergency_referrals, PRBench legal, PRBench finance.

**Total: 60 runs.**

## 3. Running Experiments

All commands are run from the `programmaticmemory/` directory.

```bash
# Run everything (Table 1 + baselines + ablation)
bash scripts/run_experiments.sh
bash scripts/run_ablation.sh

# Or run specific sections
bash scripts/run_experiments.sh table1      # Table 1 main (21 runs)
bash scripts/run_experiments.sh baselines   # Table 1 baselines (35 runs)
bash scripts/run_ablation.sh               # Table 2 ablation (4 runs)
```

### Auto-Resume

Each run writes to a unique output directory (e.g. `outputs/t1-locomo-ours/`). **If a run is interrupted, just re-run the same script** — it detects `state.json` in the output directory and automatically resumes from where it stopped. No manual `--resume` needed.

### Output Directory Convention

```
outputs/
  t1-locomo-no-memory/       # Table 1: LoCoMo / No Memory
  t1-locomo-vanilla-rag/     # Table 1: LoCoMo / Vanilla RAG
  t1-locomo-ours/            # Table 1: LoCoMo / Evolution
  t1-alfworld-unseen-ours/   # Table 1: ALFWorld unseen / Evolution
  bl-locomo-traj-retr/       # Baseline: LoCoMo / Trajectory Retrieval
  t2-locomo-freeze-inst/     # Ablation: freeze instructions
  ...
```

Each directory contains:
- `config.json` — run configuration
- `summary.json` — final scores (the key result)
- `state.json` — checkpoint for auto-resume
- `programs/` — evolved programs (seed_0.py, iter_1.py, ...)
- `run.log` — full execution log

### Collecting Results

```bash
# View all Table 1 scores
for d in outputs/t1-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# View all baseline scores
for d in outputs/bl-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# View ablation scores
for d in outputs/t2-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done
```

## 4. Cost & Time Estimates

- **Baseline runs** (--iterations 0): ~$0.05-0.20 each, ~1-5 min
- **Evolution runs** (--iterations 20): ~$2-5 each, ~30-90 min (ALFWorld is slowest due to TextWorld episodes)
- **Ablation runs** (--iterations 20): same as evolution
- **Total estimated cost**: ~$30-60 across all 60 runs

Monitor your OpenRouter balance:
```bash
curl -s https://openrouter.ai/api/v1/auth/key \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" | python3 -c \
  "import sys,json; d=json.load(sys.stdin)['data']; print(f'Remaining: \${d[\"limit_remaining\"]:.2f}')"
```

## 5. Troubleshooting

| Problem | Solution |
|---------|----------|
| `NotFoundError: OpenrouterException` on embedding | Add `--embedding-model local` to the MODELS line in the script |
| Rate limit errors | Reduce `--batch-concurrency` (default 4) to 2 |
| ALFWorld `game.tw-pddl` not found | Run `uv run python -c "from programmaticmemory.benchmarks.alfworld import ensure_data; ensure_data()"` |
| HealthBench/PRBench data missing | Data auto-downloads on first run via HuggingFace |
| Run interrupted | Just re-run the same script — auto-resumes via `--output-dir` |
