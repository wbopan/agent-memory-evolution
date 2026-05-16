"""Tests for direct Azure OpenAI authentication configuration.

mstar talks to Azure directly (no reverse proxy): configure_azure_auth() builds
an AzureCliCredential token provider and stashes it; apply_azure_kwargs() injects
the endpoint + provider into each litellm call.
"""

from __future__ import annotations

import pytest

import mstar.evolution.azure_config as azure_config
from mstar.evolution.azure_config import (
    _has_azure_prefix,
    apply_azure_kwargs,
    configure_azure_auth,
)


@pytest.fixture(autouse=True)
def _reset_azure_config():
    """Each test starts and ends with a clean module-global config."""
    azure_config._CONFIG = None
    yield
    azure_config._CONFIG = None


class TestHasAzurePrefix:
    def test_detects_azure_prefixed_model(self):
        assert _has_azure_prefix(["azure/gpt-4o"]) is True

    def test_detects_azure_among_multiple_models(self):
        assert _has_azure_prefix(["openai/gpt-4", "azure/gpt-4o"]) is True

    def test_returns_false_for_no_azure_models(self):
        assert _has_azure_prefix(["openai/gpt-4", "openrouter/gpt-3.5"]) is False

    def test_returns_false_for_empty_list(self):
        assert _has_azure_prefix([]) is False

    def test_ignores_empty_strings(self):
        assert _has_azure_prefix(["", "openai/gpt-4"]) is False

    def test_does_not_match_azure_in_non_prefix_position(self):
        assert _has_azure_prefix(["openai/azure-gpt-4"]) is False

    def test_detects_bare_azure_prefix(self):
        assert _has_azure_prefix(["azure/"]) is True


class TestConfigureAzureAuth:
    def test_no_op_when_no_azure_models(self):
        """configure_azure_auth should leave config unset when no azure/ models."""
        configure_azure_auth(["openai/gpt-4"], azure_api_base="https://x.azure.com")
        assert azure_config._CONFIG is None

    def test_clears_stale_config_when_no_azure_models(self):
        """A non-azure reconfigure must clear any previously stored config."""
        configure_azure_auth(["azure/gpt-4o"], azure_api_base="https://x.azure.com")
        assert azure_config._CONFIG is not None
        configure_azure_auth(["openai/gpt-4"])
        assert azure_config._CONFIG is None

    def test_raises_when_azure_model_but_no_api_base(self):
        """Should raise ValueError if azure/ model present but no azure_api_base."""
        with pytest.raises(ValueError, match="--azure-api-base is required"):
            configure_azure_auth(["azure/gpt-4o"], azure_api_base=None)

    def test_stores_config_when_azure_model_present(self):
        """Endpoint, version, and a callable token provider should be stored."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://myresource.services.ai.azure.com",
            azure_api_version="2025-03-01-preview",
        )
        cfg = azure_config._CONFIG
        assert cfg is not None
        assert cfg.api_base == "https://myresource.services.ai.azure.com"
        assert cfg.api_version == "2025-03-01-preview"
        assert callable(cfg.token_provider)

    def test_uses_default_api_version(self):
        """Default api_version should be '2024-12-01-preview'."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://myresource.services.ai.azure.com",
        )
        assert azure_config._CONFIG is not None
        assert azure_config._CONFIG.api_version == "2024-12-01-preview"


class TestApplyAzureKwargs:
    def test_injects_endpoint_and_provider_for_azure_model(self):
        """azure/ models get api_base, api_version, and azure_ad_token_provider."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://myresource.services.ai.azure.com",
            azure_api_version="2025-03-01-preview",
        )
        kwargs: dict = {"model": "azure/gpt-4o", "messages": []}
        apply_azure_kwargs("azure/gpt-4o", kwargs)
        assert kwargs["api_base"] == "https://myresource.services.ai.azure.com"
        assert kwargs["api_version"] == "2025-03-01-preview"
        assert callable(kwargs["azure_ad_token_provider"])

    def test_noop_for_non_azure_model(self):
        """openrouter/ or openai/ models must not get Azure kwargs injected."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://myresource.services.ai.azure.com",
        )
        kwargs: dict = {"model": "openrouter/baai/bge-m3", "input": ["x"]}
        apply_azure_kwargs("openrouter/baai/bge-m3", kwargs)
        assert "api_base" not in kwargs
        assert "api_version" not in kwargs
        assert "azure_ad_token_provider" not in kwargs

    def test_noop_when_not_configured(self):
        """With no config (no azure run), apply_azure_kwargs is a no-op."""
        kwargs: dict = {"model": "azure/gpt-4o", "messages": []}
        apply_azure_kwargs("azure/gpt-4o", kwargs)
        assert kwargs == {"model": "azure/gpt-4o", "messages": []}

    def test_explicit_overrides_win(self):
        """setdefault semantics: caller-supplied kwargs are never overwritten."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://default.services.ai.azure.com",
            azure_api_version="2025-03-01-preview",
        )
        kwargs: dict = {
            "model": "azure/gpt-4o",
            "api_base": "https://override.services.ai.azure.com",
            "api_version": "2099-01-01",
        }
        apply_azure_kwargs("azure/gpt-4o", kwargs)
        assert kwargs["api_base"] == "https://override.services.ai.azure.com"
        assert kwargs["api_version"] == "2099-01-01"
        # provider still injected since the caller did not supply one
        assert callable(kwargs["azure_ad_token_provider"])

    def test_noop_for_non_string_model(self):
        """A missing/None model must not raise."""
        configure_azure_auth(
            ["azure/gpt-4o"],
            azure_api_base="https://myresource.services.ai.azure.com",
        )
        kwargs: dict = {"messages": []}
        apply_azure_kwargs(None, kwargs)
        assert kwargs == {"messages": []}
