# Azure Token Provider Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Azure OpenAI authentication support (API key + DefaultAzureCredential token provider) so users can run evolution with `azure/` model prefixes.

**Architecture:** When any model uses an `azure/` prefix, auto-detect whether `AZURE_API_KEY` is set. If not, enable `litellm.enable_azure_ad_token_refresh = True` which uses `DefaultAzureCredential` internally. New CLI flags `--azure-api-base` and `--azure-api-version` provide required Azure endpoint config. All changes are concentrated in `__main__.py` (CLI + startup) and `pyproject.toml` (dependency).

**Tech Stack:** litellm (existing), azure-identity (new required dep)

**Spec:** `docs/superpowers/specs/2026-03-24-azure-token-provider-auth-design.md`

---

### File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `pyproject.toml` | Modify | Add `azure-identity` to dependencies |
| `src/programmaticmemory/evolution/__main__.py` | Modify | Add `--azure-api-base`, `--azure-api-version` CLI flags; detect Azure models; set litellm globals |
| `tests/evolution/test_azure_auth.py` | Create | Unit tests for Azure detection and litellm config |

---

### Task 1: Add azure-identity dependency

**Files:**
- Modify: `pyproject.toml:13-24`

- [ ] **Step 1: Add azure-identity to dependencies**

In `pyproject.toml`, add `azure-identity` to the `dependencies` list:

```toml
dependencies = [
    "litellm>=1.81.5",
    "tenacity>=9.1.2",
    "rich>=13.0.0",
    "weave[litellm]>=0.51.0",
    "redis>=7.1.0",
    "chromadb>=1.0.0",
    "codex-apply-patch>=0.4.0",
    "chardet<6",
    "alfworld>=0.2.2",
    "fastembed>=0.6.0",
    "azure-identity>=1.17.0",
]
```

- [ ] **Step 2: Sync dependencies**

Run: `uv sync`
Expected: Installs `azure-identity` and its transitive deps (msal, etc.)

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock
git commit -m "deps: add azure-identity as required dependency"
```

---

### Task 2: Write failing tests for Azure detection logic

**Files:**
- Create: `tests/evolution/test_azure_auth.py`

- [ ] **Step 1: Write tests**

```python
"""Tests for Azure authentication auto-detection in __main__.py."""

from __future__ import annotations

import os
from unittest.mock import patch

import litellm
import pytest


def _has_azure_prefix(*models: str) -> bool:
    """Check if any model string starts with 'azure/'."""
    return any(m.startswith("azure/") for m in models if m)


def _configure_azure_auth(
    models: list[str],
    azure_api_base: str | None = None,
    azure_api_version: str = "2024-12-01-preview",
) -> None:
    """Configure litellm for Azure auth if any model uses azure/ prefix.

    Sets litellm.enable_azure_ad_token_refresh = True when no AZURE_API_KEY
    is found in environment. Sets api_base and api_version globals.
    """
    from programmaticmemory.evolution.azure_config import configure_azure_auth

    configure_azure_auth(models, azure_api_base, azure_api_version)


class TestHasAzurePrefix:
    def test_no_azure(self):
        assert not _has_azure_prefix("openrouter/deepseek/deepseek-v3.2", "openrouter/openai/gpt-5.3-codex")

    def test_one_azure(self):
        assert _has_azure_prefix("azure/gpt-4o", "openrouter/openai/gpt-5.3-codex")

    def test_all_azure(self):
        assert _has_azure_prefix("azure/gpt-4o", "azure/gpt-4o-mini")

    def test_none_values(self):
        assert not _has_azure_prefix(None, "openrouter/deepseek/deepseek-v3.2")

    def test_azure_with_none(self):
        assert _has_azure_prefix("azure/gpt-4o", None)


class TestConfigureAzureAuth:
    def setup_method(self):
        # Reset litellm state before each test
        litellm.enable_azure_ad_token_refresh = False
        litellm.api_base = None
        litellm.api_version = None

    def test_no_azure_models_noop(self):
        configure_azure_auth(
            models=["openrouter/deepseek/deepseek-v3.2"],
            azure_api_base=None,
            azure_api_version="2024-12-01-preview",
        )
        assert litellm.enable_azure_ad_token_refresh is False

    @patch.dict(os.environ, {}, clear=False)
    def test_azure_model_no_api_key_enables_token_refresh(self):
        # Ensure no AZURE_API_KEY in env
        os.environ.pop("AZURE_API_KEY", None)
        configure_azure_auth(
            models=["azure/gpt-4o"],
            azure_api_base="https://myresource.openai.azure.com/",
            azure_api_version="2024-12-01-preview",
        )
        assert litellm.enable_azure_ad_token_refresh is True

    @patch.dict(os.environ, {"AZURE_API_KEY": "test-key"}, clear=False)
    def test_azure_model_with_api_key_skips_token_refresh(self):
        configure_azure_auth(
            models=["azure/gpt-4o"],
            azure_api_base="https://myresource.openai.azure.com/",
            azure_api_version="2024-12-01-preview",
        )
        assert litellm.enable_azure_ad_token_refresh is False

    def test_azure_model_sets_api_base(self):
        os.environ.pop("AZURE_API_KEY", None)
        configure_azure_auth(
            models=["azure/gpt-4o"],
            azure_api_base="https://myresource.openai.azure.com/",
            azure_api_version="2024-12-01-preview",
        )
        assert litellm.api_base == "https://myresource.openai.azure.com/"

    def test_azure_model_missing_api_base_raises(self):
        os.environ.pop("AZURE_API_KEY", None)
        with pytest.raises(ValueError, match="--azure-api-base"):
            configure_azure_auth(
                models=["azure/gpt-4o"],
                azure_api_base=None,
                azure_api_version="2024-12-01-preview",
            )

    def teardown_method(self):
        litellm.enable_azure_ad_token_refresh = False
        litellm.api_base = None
        litellm.api_version = None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_azure_auth.py -v`
Expected: FAIL — `programmaticmemory.evolution.azure_config` does not exist

- [ ] **Step 3: Commit failing tests**

```bash
git add tests/evolution/test_azure_auth.py
git commit -m "test: add failing tests for Azure auth detection"
```

---

### Task 3: Implement Azure config module

**Files:**
- Create: `src/programmaticmemory/evolution/azure_config.py`

- [ ] **Step 1: Create azure_config.py**

```python
"""Azure OpenAI authentication configuration for litellm."""

from __future__ import annotations

import os

import litellm


def configure_azure_auth(
    models: list[str],
    azure_api_base: str | None = None,
    azure_api_version: str = "2024-12-01-preview",
) -> None:
    """Configure litellm for Azure OpenAI if any model uses azure/ prefix.

    Auth priority:
    1. AZURE_API_KEY env var → litellm handles natively (no action needed)
    2. No API key → enable DefaultAzureCredential token refresh

    Args:
        models: List of model strings from CLI (task, reflect, toolkit, judge, embedding).
        azure_api_base: Azure endpoint URL. Required when any model is azure/.
        azure_api_version: Azure API version string.

    Raises:
        ValueError: If azure/ models detected but --azure-api-base not provided.
    """
    has_azure = any(m.startswith("azure/") for m in models if m)
    if not has_azure:
        return

    if not azure_api_base:
        raise ValueError(
            "--azure-api-base is required when using azure/ model prefix. "
            "Example: --azure-api-base https://myresource.openai.azure.com/"
        )

    litellm.api_base = azure_api_base
    litellm.api_version = azure_api_version

    if not os.environ.get("AZURE_API_KEY"):
        litellm.enable_azure_ad_token_refresh = True
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_azure_auth.py -v`
Expected: All 7 tests PASS

- [ ] **Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/azure_config.py
git commit -m "feat: add Azure auth config module with token provider support"
```

---

### Task 4: Wire Azure config into CLI

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py:86-120` (argument parsing) and `:527-535` (startup logic)

- [ ] **Step 1: Add CLI flags to argparse**

After line 117 (`--embedding-model`), add:

```python
    parser.add_argument(
        "--azure-api-base",
        default=os.environ.get("AZURE_API_BASE"),
        help="Azure OpenAI endpoint URL (e.g. https://myresource.openai.azure.com/). "
        "Also reads AZURE_API_BASE env var.",
    )
    parser.add_argument(
        "--azure-api-version",
        default="2024-12-01-preview",
        help="Azure OpenAI API version (default: 2024-12-01-preview)",
    )
```

Add `import os` at the top of the file (after existing imports, line 12).

- [ ] **Step 2: Call configure_azure_auth at startup**

After `configure_cache("disk")` (around line 533), add:

```python
    # Configure Azure auth if any model uses azure/ prefix
    from programmaticmemory.evolution.azure_config import configure_azure_auth

    all_models = [args.task_model, args.reflect_model, args.toolkit_model, args.judge_model, args.embedding_model]
    configure_azure_auth(all_models, args.azure_api_base, args.azure_api_version)
```

Also add the same block in the resume path, after `configure_cache("disk")` (around line 313).

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All tests pass (existing + new Azure tests)

- [ ] **Step 4: Run lint**

Run: `uv run ruff check src/ && uv run ruff format src/`
Expected: No errors

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: wire Azure auth CLI flags into evolution entry point"
```

---

### Task 5: Update CLAUDE.md with Azure usage docs

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add Azure section to CLI Recipes**

After the existing CLI recipes section in `CLAUDE.md`, add documentation for Azure usage:

```markdown
### Azure OpenAI

```bash
# With API key
export AZURE_API_KEY=xxx
export AZURE_API_BASE=https://myresource.openai.azure.com/
uv run python -m programmaticmemory.evolution \
  --dataset locomo --task-model azure/gpt-4o

# With DefaultAzureCredential (az login, Managed Identity, etc.)
az login
uv run python -m programmaticmemory.evolution \
  --dataset locomo --task-model azure/gpt-4o \
  --azure-api-base https://myresource.openai.azure.com/
```
```

- [ ] **Step 2: Add new CLI flags to the Key CLI Flags table**

Add rows for `--azure-api-base` and `--azure-api-version`.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add Azure OpenAI authentication usage to CLAUDE.md"
```
