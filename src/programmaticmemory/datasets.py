"""Unified dataset loading for programmaticmemory."""

from __future__ import annotations

from typing import Any, Callable

from programmaticmemory.evolution.types import Dataset

# User-registered datasets
_CUSTOM_REGISTRY: dict[str, Callable[..., Dataset]] = {}

_benchmarks_imported = False


def _ensure_benchmarks_imported() -> None:
    """Import the benchmarks package so @register_dataset decorators run."""
    global _benchmarks_imported
    if not _benchmarks_imported:
        import programmaticmemory.benchmarks  # noqa: F401

        _benchmarks_imported = True


def register_dataset(name: str):
    """Decorator to register a dataset loader."""

    def decorator(fn: Callable[..., Dataset]) -> Callable[..., Dataset]:
        _CUSTOM_REGISTRY[name] = fn
        return fn

    return decorator


def load_dataset(
    name: str,
    *,
    category: str | None = None,
    **kwargs: Any,
) -> Dataset:
    """Load a dataset by name."""
    _ensure_benchmarks_imported()
    if name not in _CUSTOM_REGISTRY:
        available = sorted(_CUSTOM_REGISTRY)
        raise ValueError(f"Unknown dataset: {name!r}. Available: {available}")
    return _CUSTOM_REGISTRY[name](category=category, **kwargs)


def list_datasets() -> list[str]:
    """Return sorted list of all available dataset names."""
    _ensure_benchmarks_imported()
    return sorted(_CUSTOM_REGISTRY)
