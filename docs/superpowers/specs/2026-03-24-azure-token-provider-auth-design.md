# Azure Token Provider Auth Support

**Date:** 2026-03-24
**Status:** Draft

## Problem

The project currently authenticates all LLM calls via environment variables (primarily `OPENROUTER_API_KEY`) through litellm. Users with Azure OpenAI deployments that use Azure AD token-based authentication (e.g., `DefaultAzureCredential`) cannot use the system without manual workarounds.

## Solution

Leverage litellm's built-in `enable_azure_ad_token_refresh` global flag to automatically handle Azure AD token provider authentication when Azure models are detected and no API key is present.

## Design

### Trigger Condition

When any CLI model parameter (`--task-model`, `--reflect-model`, `--toolkit-model`, `--judge-model`, `--embedding-model`) uses an `azure/` prefix, Azure authentication is activated.

### Authentication Priority

1. `AZURE_API_KEY` environment variable set → litellm native API key auth (no code change needed)
2. No API key + any model uses `azure/` prefix → set `litellm.enable_azure_ad_token_refresh = True`, which uses `DefaultAzureCredential` internally
3. Non-Azure models → unaffected

### New CLI Flags

| Flag | Required | Default | Purpose |
|------|----------|---------|---------|
| `--azure-api-base` | When using Azure models | `AZURE_API_BASE` env var | Azure endpoint URL (e.g., `https://myresource.openai.azure.com/`) |
| `--azure-api-version` | When using Azure models | `2024-12-01-preview` | Azure OpenAI API version |

### File Changes

**`__main__.py`:**
- At startup, check if any model argument has `azure/` prefix
- If yes and `AZURE_API_KEY` not set → `litellm.enable_azure_ad_token_refresh = True`
- Parse `--azure-api-base` and `--azure-api-version`
- Store Azure config in a dataclass or dict, pass through to toolkit config

**`toolkit.py` (`completion_with_retry`):**
- When model has `azure/` prefix, inject `api_base` and `api_version` into the `litellm.completion()` call
- No other changes to retry logic, caching, or budget enforcement

**`pyproject.toml`:**
- Add `azure-identity` as a required dependency

### Unchanged Modules

- `evaluator.py` — no changes
- `reflector.py` — no changes
- `cache.py` — no changes (litellm caching works transparently with Azure)
- `strategies.py` — no changes
- `loop.py` — no changes
- `prompts.py` — no changes
- `logging/` — no changes (litellm callbacks capture Azure calls automatically)

### User Workflows

```bash
# API key mode
export AZURE_API_KEY=xxx
export AZURE_API_BASE=https://myresource.openai.azure.com/
uv run python -m programmaticmemory.evolution \
  --dataset locomo --task-model azure/gpt-4o \
  --azure-api-version 2024-12-01-preview

# Token provider mode (after az login)
az login
uv run python -m programmaticmemory.evolution \
  --dataset locomo --task-model azure/gpt-4o \
  --azure-api-base https://myresource.openai.azure.com/ \
  --azure-api-version 2024-12-01-preview
```

### Out of Scope

- litellm Router or config file based setup
- Custom credential chain selection (DefaultAzureCredential covers CLI, Managed Identity, env vars, etc.)
- Per-model Azure endpoint configuration (all Azure models share one endpoint)

## Dependencies

- `azure-identity` (required dependency)
- litellm >= recent version with `enable_azure_ad_token_refresh` support and embedding bug fix (PR #14374)
