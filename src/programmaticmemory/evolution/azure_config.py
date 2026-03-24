"""Azure OpenAI authentication configuration for litellm."""

from __future__ import annotations

import os

import litellm


def _has_azure_prefix(models: list[str]) -> bool:
    """Return True if any model string starts with 'azure/'."""
    return any(m.startswith("azure/") for m in models if m)


def configure_azure_auth(
    models: list[str],
    azure_api_base: str | None = None,
    azure_api_version: str = "2024-12-01-preview",
) -> None:
    """Configure litellm for Azure OpenAI if any model uses azure/ prefix.

    Auth priority:
    1. AZURE_API_KEY env var -> litellm handles natively (no action needed)
    2. No API key -> enable DefaultAzureCredential token refresh

    Args:
        models: List of model strings from CLI.
        azure_api_base: Azure endpoint URL. Required when any model is azure/.
        azure_api_version: Azure API version string.

    Raises:
        ValueError: If azure/ models detected but --azure-api-base not provided.
    """
    if not _has_azure_prefix(models):
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
