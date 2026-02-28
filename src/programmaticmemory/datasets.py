"""Unified dataset loading for programmaticmemory."""

from __future__ import annotations

import importlib
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable

from programmaticmemory.core.adapter import DataInst
from programmaticmemory.evolution.types import Dataset

if TYPE_CHECKING:
    from programmaticmemory.evolution.evaluator import Scorer

# Built-in dataset name -> module path mapping (lazy-loaded)
_BUILTIN_DATASETS: dict[str, str] = {}


@dataclass
class BenchmarkConfig:
    """Metadata registered alongside a dataset loader."""

    loader: Callable[..., Dataset]
    scorer: Scorer | None = None


# User-registered datasets
_CUSTOM_REGISTRY: dict[str, BenchmarkConfig] = {}

_benchmarks_imported = False


def _ensure_benchmarks_imported() -> None:
    """Import the benchmarks package so @register_dataset decorators run."""
    global _benchmarks_imported
    if not _benchmarks_imported:
        import programmaticmemory.benchmarks  # noqa: F401

        _benchmarks_imported = True


def register_dataset(name: str, *, scorer: Scorer | None = None):
    """Decorator to register a dataset loader with optional scorer metadata."""

    def decorator(fn: Callable[..., Dataset]) -> Callable[..., Dataset]:
        _CUSTOM_REGISTRY[name] = BenchmarkConfig(loader=fn, scorer=scorer)
        return fn

    return decorator


def get_benchmark_config(name: str) -> BenchmarkConfig:
    """Retrieve the BenchmarkConfig for a registered dataset."""
    _ensure_benchmarks_imported()
    if name in _CUSTOM_REGISTRY:
        return _CUSTOM_REGISTRY[name]
    available = sorted(set(_BUILTIN_DATASETS) | set(_CUSTOM_REGISTRY))
    raise ValueError(f"Unknown dataset: {name!r}. Available: {available}")


def load_dataset(
    name: str,
    *,
    train_size: int | None = None,
    val_size: int | None = None,
    **kwargs: Any,
) -> Dataset:
    """Load a dataset by name."""
    _ensure_benchmarks_imported()
    if name in _CUSTOM_REGISTRY:
        dataset = _CUSTOM_REGISTRY[name].loader(**kwargs)
    elif name in _BUILTIN_DATASETS:
        module = importlib.import_module(_BUILTIN_DATASETS[name])
        dataset = module.init_dataset(**kwargs)
    else:
        available = sorted(set(_BUILTIN_DATASETS) | set(_CUSTOM_REGISTRY))
        raise ValueError(f"Unknown dataset: {name!r}. Available: {available}")

    if train_size is not None:
        dataset.train = dataset.train[:train_size]
    if val_size is not None:
        dataset.val = dataset.val[:val_size]
    return dataset


def list_datasets() -> list[str]:
    """Return sorted list of all available dataset names."""
    return sorted(set(_BUILTIN_DATASETS) | set(_CUSTOM_REGISTRY))


def split_and_shuffle(
    examples: list[DataInst],
    *,
    train_ratio: float = 0.5,
    seed: int = 0,
) -> tuple[list[DataInst], list[DataInst]]:
    """Deterministically shuffle and split examples into train/val."""
    examples = list(examples)
    random.Random(seed).shuffle(examples)
    mid = int(len(examples) * train_ratio)
    return examples[:mid], examples[mid:]
