# Experiment Reproduction Guide

This document explains how to configure the environment and run all experiments from the M★ paper.

## 1. Environment Setup

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)

### Installation

```bash
git clone https://github.com/wbopan/mstar.git
cd mstar
uv sync
```

### Verify installation

```bash
# Quick test — no API key required
uv run pytest tests/evolution/ -m "not llm" -v
```

## 2. Model Configuration

The system uses 4 LLM roles, routed to different providers via [LiteLLM](https://docs.litellm.ai/docs/providers).

| Role | CLI flag | Default model ID | Purpose |
|------|---------|-----------------|---------|
| Task Agent | `--task-model` | `openrouter/deepseek/deepseek-v3.2` | Knowledge extraction, query generation, answer generation |
| Reflect Agent | `--reflect-model` | `openrouter/openai/gpt-5.3-codex` | Generate/mutate Python programs |
| Toolkit LLM | `--toolkit-model` | `openrouter/deepseek/deepseek-v3.2` | LLM calls inside KB programs |
| Judge (optional) | `--judge-model` | defaults to task-model | Rubric scoring for HealthBench/PRBench |

### Switching providers

Model IDs follow LiteLLM's `provider/model` format. The defaults use the `openrouter/` prefix (routed via OpenRouter). To use a different provider, change the prefix. See the [LiteLLM Providers documentation](https://docs.litellm.ai/docs/providers) for all supported routing formats.

Examples:
- OpenRouter: `openrouter/deepseek/deepseek-v3.2`
- Direct DeepSeek: `deepseek/deepseek-chat`
- Direct OpenAI: `gpt-4o`
- Azure: `azure/gpt-4o`

### Overriding via environment variables

The run scripts support overriding model IDs via environment variables — no need to edit the scripts:

```bash
export TASK_MODEL="deepseek/deepseek-chat"
export REFLECT_MODEL="gpt-4o"
export TOOLKIT_MODEL="deepseek/deepseek-chat"
bash scripts/run_experiments.sh
```

### API Keys

Set the environment variable for whichever provider you use:

```bash
# OpenRouter
export OPENROUTER_API_KEY="sk-or-v1-..."

# Or direct DeepSeek
export DEEPSEEK_API_KEY="sk-..."

# Or direct OpenAI
export OPENAI_API_KEY="sk-..."

# Or Azure OpenAI (see full example below)
export AZURE_API_KEY="your-azure-key"
export AZURE_API_BASE="https://your-resource.openai.azure.com"
export AZURE_API_VERSION="2025-04-01-preview"  # optional; code default is 2024-12-01-preview
```

#### Azure OpenAI full example

The system auto-detects `azure/`-prefixed models via `azure_config.py` and configures LiteLLM's Azure authentication. Two authentication methods are supported:

1. **API Key** (recommended): set `AZURE_API_KEY`
2. **DefaultAzureCredential** (keyless): when `AZURE_API_KEY` is not set, the system automatically enables Azure AD token refresh (requires `azure-identity`, already included in project dependencies). Works with Managed Identity, `az login`, etc.

Both methods require `AZURE_API_BASE` (via environment variable or `--azure-api-base` flag).

Azure endpoints do not provide an embedding API; using a local embedding model is recommended:

```bash
# Azure auth — method 1: API Key
export AZURE_API_KEY="your-azure-key"
export AZURE_API_BASE="https://your-resource.openai.azure.com"
export AZURE_API_VERSION="2025-04-01-preview"  # optional; overrides the code default

# Azure auth — method 2: keyless (DefaultAzureCredential)
# Leave AZURE_API_KEY unset; ensure you have authenticated via az login or Managed Identity
export AZURE_API_BASE="https://your-resource.openai.azure.com"

# Models
export TASK_MODEL="azure/gpt-5.4-mini"
export REFLECT_MODEL="azure/gpt-5.3-codex"
export TOOLKIT_MODEL="azure/gpt-5.4-mini"

# Local embedding (skip API, use FastEmbed)
export EMBEDDING_MODEL="local"

# Run
bash scripts/run_experiments.sh table1
```

### Embedding model

Embeddings are used only for train/val subset selection (k-means clustering) and **do not affect the core experiment logic**.

The default is to call `openrouter/baai/bge-m3` via API. If your provider does not support an embedding API, **no configuration is needed** — the system automatically falls back to a local model (FastEmbed BAAI/bge-small-en-v1.5, ONNX CPU; ~50 MB download on first run).

You can also explicitly force the local model:

```bash
export EMBEDDING_MODEL="local"
```

## 3. Experiment Overview

Three scripts cover Table 1 and Table 2 of the paper:

| Script | Table | Runs | Contents |
|--------|-------|------|---------|
| `run_experiments.sh table1` | Table 1 | 21 | No Memory / Vanilla RAG / Ours × 7 datasets |
| `run_experiments.sh baselines` | Table 1 | 35 | 5 baselines × 7 datasets |
| `run_ablation.sh` | Table 2 | 4 | 4 ablation variants × LoCoMo |

**7 datasets**: LoCoMo, ALFWorld unseen, ALFWorld seen, HealthBench data_tasks, HealthBench emergency_referrals, PRBench legal, PRBench finance

**60 runs total.**

## 4. Running Experiments

All commands are run from the repository root:

```bash
# Run everything
bash scripts/run_experiments.sh
bash scripts/run_ablation.sh

# Or in parts
bash scripts/run_experiments.sh table1      # Table 1 main results (21 runs)
bash scripts/run_experiments.sh baselines   # Table 1 baselines (35 runs)
bash scripts/run_ablation.sh               # Table 2 ablation (4 runs)
```

### Automatic resume

Each run has a unique output directory (e.g. `outputs/t1-locomo-ours/`). **If a run is interrupted, simply re-run the same script** — the system detects `state.json` in the output directory and resumes from the last checkpoint.

### Output directories

```
outputs/
  t1-locomo-no-memory/       # Table 1: LoCoMo / No Memory
  t1-locomo-vanilla-rag/     # Table 1: LoCoMo / Vanilla RAG
  t1-locomo-ours/            # Table 1: LoCoMo / Evolution
  t1-alfworld-unseen-ours/   # Table 1: ALFWorld unseen / Evolution
  bl-locomo-traj-retr/       # Baseline: LoCoMo / Trajectory Retrieval
  t2-locomo-freeze-inst/     # Ablation: freeze instruction constants
  ...
```

Each directory contains:
- `summary.json` — final scores (**primary results**)
- `config.json` — run configuration
- `state.json` — checkpoint for resume
- `programs/` — programs produced by evolution
- `run.log` — full execution log

### Collecting results

```bash
# Table 1 scores
for d in outputs/t1-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# Baseline scores
for d in outputs/bl-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done

# Ablation scores
for d in outputs/t2-*/; do
  echo "$(basename $d): $(jq -r '.test_evaluation | to_entries[] | "\(.key): \(.value)"' $d/summary.json 2>/dev/null)"
done
```

## 5. Troubleshooting

| Issue | Solution |
|-------|---------|
| Embedding API error | Set `EMBEDDING_MODEL=local` or add `--embedding-model local` |
| Rate limit | Lower `--batch-concurrency` (default 64; try 2) |
| ALFWorld data missing | `uv run python -c "from mstar.benchmarks.alfworld import ensure_data; ensure_data()"` |
| HealthBench/PRBench data missing | Downloaded automatically from HuggingFace on first run |
| Run interrupted | Re-run the same script; it resumes automatically |
| Want to switch model provider | Set `TASK_MODEL` / `REFLECT_MODEL` / `TOOLKIT_MODEL` environment variables |
