"""tau-bench benchmark — retail/airline task completion evaluation."""

from __future__ import annotations

import ast
import random
from pathlib import Path

from programmaticmemory.benchmarks._download import download_file, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

_BASE_URL = "https://raw.githubusercontent.com/sierra-research/tau-bench/main"

_FILES = {
    "retail": [
        ("tau_bench/envs/retail/tasks.py", "retail/tasks.py"),
        ("tau_bench/envs/retail/wiki.md", "retail/wiki.md"),
    ],
    "airline": [
        ("tau_bench/envs/airline/tasks.py", "airline/tasks.py"),
        ("tau_bench/envs/airline/wiki.md", "airline/wiki.md"),
    ],
}


def ensure_data(domain: str = "retail", data_dir: str | Path | None = None) -> Path:
    """Download tau-bench data files for the given domain."""
    dest_dir = get_data_dir("tau_bench", data_dir)
    for src_path, dest_rel in _FILES[domain]:
        url = f"{_BASE_URL}/{src_path}"
        download_file(url, dest_dir / dest_rel)
    return dest_dir


def _parse_tasks_file(path: Path) -> list[dict]:
    """Parse tasks list from a tau-bench tasks.py file using AST."""
    source = path.read_text()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("tasks", "TASKS"):
                    segment = ast.get_source_segment(source, node.value)
                    if segment:
                        return ast.literal_eval(segment)
    raise ValueError(f"No tasks list found in {path}")


def _derive_expected(task: dict) -> str:
    """Derive expected answer from a task dict."""
    outputs = task.get("outputs", [])
    if outputs:
        return outputs[0]
    actions = task.get("actions", [])
    if actions:
        return actions[-1].get("name", actions[-1].get("action", ""))
    return ""


@register_dataset("tau_bench")
def load_tau_bench(
    *,
    domain: str = "retail",
    train_ratio: float = 0.7,
    seed: int = 42,
    data_dir: str | Path | None = None,
    category: str | None = None,
) -> Dataset:
    """Load tau-bench benchmark.

    Args:
        domain: "retail" or "airline".
        train_ratio: Fraction of tasks for training.
        seed: Random seed for shuffling.
        data_dir: Override data directory.
        category: Filter by domain ("retail" or "airline"). Overrides domain= if set.

    Returns:
        Dataset with ExactMatchScorer.
    """
    all_domains = sorted(_FILES.keys())
    if category is not None:
        if category not in _FILES:
            raise ValueError(f"Unknown tau_bench category {category!r}. Available: {', '.join(all_domains)}")
        domain = category
    dest_dir = ensure_data(domain, data_dir)
    tasks_path = dest_dir / domain / "tasks.py"
    tasks = _parse_tasks_file(tasks_path)

    items = []
    for task in tasks:
        instruction = task.get("instruction", "")
        expected = _derive_expected(task)
        if not instruction:
            continue
        items.append(DataItem(raw_text="", question=instruction, expected_answer=expected))

    rng = random.Random(seed)
    rng.shuffle(items)

    split = int(len(items) * train_ratio)
    train = items[:split]
    val = items[split:]

    return Dataset(train=train, val=val, test=[], scorer=ExactMatchScorer(), available_categories=all_domains)
