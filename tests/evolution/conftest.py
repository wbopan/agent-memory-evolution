"""Shared test fixtures for evolution tests."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def mock_chromadb(request):
    """Mock chromadb.EphemeralClient to avoid slow initialization.

    Use @pytest.mark.uses_chroma to opt out for tests that need real ChromaDB.
    """
    if request.node.get_closest_marker("uses_chroma"):
        yield
        return

    mock_client = MagicMock()
    with patch("chromadb.EphemeralClient", return_value=mock_client):
        yield mock_client


@pytest.fixture(autouse=True, scope="session")
def enable_llm_disk_cache():
    """Enable litellm disk cache for LLM integration tests.

    Cache is committed to git so tests work without API key.
    Wraps litellm.completion to auto-inject caching=True.
    """
    import litellm

    from programmaticmemory.cache import configure_cache, disable_cache

    cache_dir = str(Path(__file__).parent / ".llm_cache")
    configure_cache("disk", disk_cache_dir=cache_dir)

    original = litellm.completion

    def cached_completion(*args, **kwargs):
        kwargs.setdefault("caching", True)
        return original(*args, **kwargs)

    litellm.completion = cached_completion
    yield
    litellm.completion = original
    disable_cache()
