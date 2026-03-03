"""Programmatic Memory — A framework for optimizing text components using LLM-based reflection."""

# Suppress noisy output from dependencies at import time
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import httpx  # noqa: E402
import litellm  # noqa: E402

litellm.suppress_debug_info = True

# Configure shared HTTP clients to prevent "too many open files" (Errno 24).
# Without this, litellm creates a new httpx client per call, leaking file descriptors.
# See: https://github.com/BerriAI/litellm/issues/1070
if litellm.client_session is None:
    litellm.client_session = httpx.Client(
        timeout=httpx.Timeout(600.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )
if litellm.aclient_session is None:
    litellm.aclient_session = httpx.AsyncClient(
        timeout=httpx.Timeout(600.0, connect=10.0),
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
    )

from programmaticmemory.cache import configure_cache, disable_cache  # noqa: E402
from programmaticmemory.datasets import load_dataset, register_dataset  # noqa: E402
from programmaticmemory.logging.logger import get_logger  # noqa: E402

__all__ = [
    "configure_cache",
    "disable_cache",
    "get_logger",
    "load_dataset",
    "register_dataset",
]
