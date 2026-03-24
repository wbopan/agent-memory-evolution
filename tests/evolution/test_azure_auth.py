"""Tests for Azure OpenAI authentication configuration."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from programmaticmemory.evolution.azure_config import (
    _has_azure_prefix,
    configure_azure_auth,
)


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
        """configure_azure_auth should do nothing when no azure/ models."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            configure_azure_auth(["openai/gpt-4"])
            # No attributes should be set on litellm
            assert not mock_litellm.api_base.called
            mock_litellm.assert_not_called()

    def test_raises_when_azure_model_but_no_api_base(self):
        """Should raise ValueError if azure/ model present but no azure_api_base."""
        with pytest.raises(ValueError, match="--azure-api-base is required"):
            configure_azure_auth(["azure/gpt-4o"], azure_api_base=None)

    def test_sets_api_base_when_azure_model_present(self):
        """litellm.api_base should be set to the provided azure_api_base."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            with patch.dict("os.environ", {"AZURE_API_KEY": "test-key"}):
                configure_azure_auth(
                    ["azure/gpt-4o"],
                    azure_api_base="https://myresource.openai.azure.com/",
                )
            assert mock_litellm.api_base == "https://myresource.openai.azure.com/"

    def test_sets_api_version_when_azure_model_present(self):
        """litellm.api_version should be set to the provided version."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            with patch.dict("os.environ", {"AZURE_API_KEY": "test-key"}):
                configure_azure_auth(
                    ["azure/gpt-4o"],
                    azure_api_base="https://myresource.openai.azure.com/",
                    azure_api_version="2024-06-01",
                )
            assert mock_litellm.api_version == "2024-06-01"

    def test_enables_token_refresh_when_no_api_key(self):
        """enable_azure_ad_token_refresh should be True when AZURE_API_KEY not set."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            with patch.dict("os.environ", {}, clear=True):
                # Ensure AZURE_API_KEY is not present
                import os
                os.environ.pop("AZURE_API_KEY", None)
                configure_azure_auth(
                    ["azure/gpt-4o"],
                    azure_api_base="https://myresource.openai.azure.com/",
                )
            assert mock_litellm.enable_azure_ad_token_refresh is True

    def test_skips_token_refresh_when_api_key_set(self):
        """enable_azure_ad_token_refresh should NOT be set when AZURE_API_KEY is present."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            with patch.dict("os.environ", {"AZURE_API_KEY": "my-secret-key"}):
                configure_azure_auth(
                    ["azure/gpt-4o"],
                    azure_api_base="https://myresource.openai.azure.com/",
                )
            # enable_azure_ad_token_refresh should not have been set
            assert not hasattr(mock_litellm, "enable_azure_ad_token_refresh") or \
                mock_litellm.enable_azure_ad_token_refresh != True  # noqa: E712

    def test_uses_default_api_version(self):
        """Default api_version should be '2024-12-01-preview'."""
        with patch("programmaticmemory.evolution.azure_config.litellm") as mock_litellm:
            with patch.dict("os.environ", {"AZURE_API_KEY": "test-key"}):
                configure_azure_auth(
                    ["azure/gpt-4o"],
                    azure_api_base="https://myresource.openai.azure.com/",
                )
            assert mock_litellm.api_version == "2024-12-01-preview"
