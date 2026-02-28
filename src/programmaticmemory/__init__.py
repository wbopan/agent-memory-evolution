"""Programmatic Memory — A framework for optimizing text components using LLM-based reflection."""

# Suppress noisy output from dependencies at import time
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import litellm  # noqa: E402

litellm.suppress_debug_info = True

from programmaticmemory.cache import configure_cache, disable_cache  # noqa: E402
from programmaticmemory.core.adapter import EvaluationBatch  # noqa: E402
from programmaticmemory.datasets import load_dataset, register_dataset  # noqa: E402
from programmaticmemory.logging.logger import get_logger  # noqa: E402

__all__ = [
    "configure_cache",
    "disable_cache",
    "EvaluationBatch",
    "get_logger",
    "load_dataset",
    "register_dataset",
]
