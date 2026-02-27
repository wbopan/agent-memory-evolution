"""Programmatic Memory — A framework for optimizing text components using LLM-based reflection."""

# Suppress noisy output from dependencies at import time
import warnings

warnings.filterwarnings("ignore", category=UserWarning, module="pydantic")

import litellm

litellm.suppress_debug_info = True

from programmaticmemory.cache import configure_cache, disable_cache
from programmaticmemory.core.adapter import EvaluationBatch
from programmaticmemory.datasets import load_dataset, register_dataset
from programmaticmemory.logging.logger import get_logger

__all__ = [
    "configure_cache",
    "disable_cache",
    "EvaluationBatch",
    "get_logger",
    "load_dataset",
    "register_dataset",
]
