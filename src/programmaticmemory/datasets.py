"""Unified dataset loading for programmaticmemory."""

from __future__ import annotations

import importlib
import random
from typing import Any, Callable

from programmaticmemory.core.adapter import DataInst

# Built-in dataset name -> module path mapping (lazy-loaded)
_BUILTIN_DATASETS: dict[str, str] = {}

# User-registered datasets
_CUSTOM_REGISTRY: dict[str, Callable[..., tuple[list, list, list]]] = {}


def register_dataset(name: str):
    """Decorator to register a custom dataset loader."""

    def decorator(fn):
        _CUSTOM_REGISTRY[name] = fn
        return fn

    return decorator


def load_dataset(
    name: str,
    *,
    train_size: int | None = None,
    val_size: int | None = None,
    **kwargs: Any,
) -> tuple[list[DataInst], list[DataInst], list[DataInst]]:
    """Load a dataset by name."""
    if name in _CUSTOM_REGISTRY:
        trainset, valset, testset = _CUSTOM_REGISTRY[name](**kwargs)
    elif name in _BUILTIN_DATASETS:
        module = importlib.import_module(_BUILTIN_DATASETS[name])
        trainset, valset, testset = module.init_dataset(**kwargs)
    else:
        available = sorted(set(_BUILTIN_DATASETS) | set(_CUSTOM_REGISTRY))
        raise ValueError(f"Unknown dataset: {name!r}. Available: {available}")

    if train_size is not None:
        trainset = trainset[:train_size]
    if val_size is not None:
        valset = valset[:val_size]
    return trainset, valset, testset


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
