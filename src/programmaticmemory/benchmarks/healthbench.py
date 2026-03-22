"""HealthBench benchmark — medical conversation QA with rubric scoring."""

from __future__ import annotations

import json
import random
from pathlib import Path

from programmaticmemory.benchmarks._download import download_file, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import RubricValScorer
from programmaticmemory.evolution.types import DataItem, Dataset

_HEALTHBENCH_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/healthbench/2025-05-07-06-14-12_oss_eval.jsonl"
)


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download HealthBench JSONL from official blob storage if not present."""
    dest_dir = get_data_dir("healthbench", data_dir)
    data_file = dest_dir / "healthbench.jsonl"
    if data_file.exists():
        return dest_dir

    download_file(_HEALTHBENCH_URL, data_file)
    return dest_dir


def _format_conversation(prompt: list[dict]) -> str:
    """Format a multi-turn conversation list into readable lines."""
    lines: list[str] = []
    for msg in prompt:
        role = str(msg.get("role", "user")).capitalize()
        content = str(msg.get("content", "")).strip()
        if content:
            lines.append(f"{role}: {content}")
    return "\n\n".join(lines)


def _extract_theme(example_tags: list[str]) -> str:
    """Extract the theme string from an example_tags list."""
    for tag in example_tags or []:
        if isinstance(tag, str) and tag.startswith("theme:"):
            return tag.split(":", 1)[1]
    return "unknown"


def _encode_rubric(rubrics: list[dict]) -> list[dict[str, object]]:
    """Build rubric criteria list from HealthBench rubric entries."""
    criteria: list[dict[str, object]] = []
    for rubric in rubrics or []:
        if not isinstance(rubric, dict):
            continue
        criterion = rubric.get("criterion", "")
        points = rubric.get("points", 0)
        if not isinstance(criterion, str) or not criterion.strip():
            continue
        if not isinstance(points, int | float) or points == 0:
            continue
        criteria.append({"criterion": criterion.strip(), "points": points})

    return criteria


@register_dataset("healthbench")
def load_healthbench(
    *,
    train_ratio: float = 0.54,
    category: str | None = None,
    seed: int = 42,
    data_dir: str | Path | None = None,
    judge_model: str = "openrouter/openai/gpt-4.1",
) -> Dataset:
    """Load HealthBench dataset."""
    data_dir_path = ensure_data(data_dir)
    data_file = data_dir_path / "healthbench.jsonl"
    records = [json.loads(line) for line in data_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    all_themes = sorted(
        {theme for r in records for theme in [_extract_theme(r.get("example_tags", []))] if theme != "unknown"}
    )

    if category is not None:
        if category not in all_themes:
            raise ValueError(f"Unknown category {category!r}. Available: {all_themes}")
        records = [r for r in records if _extract_theme(r.get("example_tags", [])) == category]

    rng = random.Random(seed)
    rng.shuffle(records)

    split = int(len(records) * train_ratio)
    train_records = records[:split]
    val_records = records[split:]

    train: list[DataItem] = []
    for record in train_records:
        conv_text = _format_conversation(record.get("prompt", []))
        ideal = ""
        ideal_data = record.get("ideal_completions_data")
        if isinstance(ideal_data, dict):
            ideal = str(ideal_data.get("ideal_completion", "")) or ""
        if not ideal:
            ideal = str(record.get("completion", "")) or ""
        raw_text = f"{conv_text}\n\nIdeal response:\n{ideal}" if ideal else conv_text
        train.append(DataItem(raw_text=raw_text, question="", expected_answer=""))

    val: list[DataItem] = []
    for record in val_records:
        conv_text = _format_conversation(record.get("prompt", []))
        rubric_criteria = _encode_rubric(record.get("rubrics", []))
        theme = _extract_theme(record.get("example_tags", []))
        val.append(
            DataItem(
                raw_text="",
                question=conv_text,
                expected_answer="",
                metadata={"theme": theme, "rubric_criteria": rubric_criteria},
            )
        )

    return Dataset(
        train=train,
        val=val,
        test=[],
        val_scorer=RubricValScorer(judge_model=judge_model),
        available_categories=all_themes,
        category_key="theme",
    )
