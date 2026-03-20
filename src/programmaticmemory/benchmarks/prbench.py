"""PRBench benchmark — finance and legal professional reasoning with rubric scoring."""

from __future__ import annotations

import json
import random
from pathlib import Path

from programmaticmemory.benchmarks._download import get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import RubricValScorer
from programmaticmemory.evolution.types import DataItem, Dataset

_WEIGHT_MAP = {
    "critically_important": 10,
    "important": 5,
    "nice_to_have": 2,
    "critically_detrimental": -10,
    "detrimental": -5,
}


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download PRBench from HuggingFace if not already present."""
    dest_dir = get_data_dir("prbench", data_dir)
    data_file = dest_dir / "prbench.jsonl"
    if data_file.exists():
        return dest_dir

    try:
        from datasets import load_dataset as hf_load
    except ImportError as exc:
        raise ImportError("pip install datasets  # required for PRBench") from exc

    # Load only main splits (finance + legal).  The _hard splits are strict
    # subsets and would duplicate records if loaded separately.
    # We mark hard items via a second pass against the hard splits.
    records: list[dict] = []
    for split_name in ["finance", "legal"]:
        try:
            ds = hf_load("ScaleAI/PRBench", split=split_name)
        except Exception:
            continue
        for row in ds:
            row_dict = dict(row)
            if "field" not in row_dict or not row_dict.get("field"):
                row_dict["field"] = split_name
            row_dict["is_hard"] = False
            records.append(row_dict)

    # Mark hard items by matching prompt_0 against the hard splits
    hard_prompts: set[str] = set()
    for hard_split in ["finance_hard", "legal_hard"]:
        try:
            ds_hard = hf_load("ScaleAI/PRBench", split=hard_split)
        except Exception:
            continue
        for row in ds_hard:
            p = (dict(row).get("prompt_0") or "")[:200]
            if p:
                hard_prompts.add(p)

    for rec in records:
        p = (rec.get("prompt_0") or "")[:200]
        if p and p in hard_prompts:
            rec["is_hard"] = True

    with data_file.open("w", encoding="utf-8") as fp:
        for row in records:
            fp.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    return dest_dir


def _format_prompt(record: dict) -> str:
    """Extract prompt text from a PRBench row."""
    if record.get("prompt_0"):
        return str(record["prompt_0"])
    return str(record.get("task", ""))


def _encode_rubric(rubric_items: list[dict]) -> list[dict[str, object]]:
    """Build rubric criteria list from PRBench rubric entries."""
    criteria: list[dict[str, object]] = []
    for item in rubric_items or []:
        if not isinstance(item, dict):
            continue
        desc = item.get("criteria_description", "")
        if not isinstance(desc, str) or not desc.strip():
            continue
        weight_class = item.get("weight_class", "nice_to_have")
        points = _WEIGHT_MAP.get(weight_class, 2)
        criteria.append({"criterion": desc.strip(), "points": points})

    return criteria


@register_dataset("prbench")
def load_prbench(
    *,
    train_ratio: float = 0.5,
    category: str | None = None,
    seed: int = 42,
    data_dir: str | Path | None = None,
    judge_model: str = "openrouter/openai/gpt-4.1",
) -> Dataset:
    """Load PRBench dataset."""
    data_dir_path = ensure_data(data_dir)
    data_file = data_dir_path / "prbench.jsonl"
    records = [json.loads(line) for line in data_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    all_categories = ["finance", "legal"]

    if category is not None:
        if category not in all_categories:
            raise ValueError(f"Unknown category {category!r}. Available: {all_categories}")
        records = [r for r in records if (r.get("field") or "").lower() == category]

    # Hard items are excluded from both train and val — they are the hardest
    # subset and would distort evolution scoring. Only non-hard items are used.
    normal_records = [r for r in records if not r.get("is_hard")]

    rng = random.Random(seed)
    rng.shuffle(normal_records)

    split = int(len(normal_records) * train_ratio)
    train_records = normal_records[:split]
    val_records = normal_records[split:]

    train: list[DataItem] = []
    for record in train_records:
        prompt = _format_prompt(record)
        response = str(record.get("response_0", ""))
        raw_text = f"Task:\n{prompt}\n\nExpert response:\n{response}" if response else prompt
        train.append(DataItem(raw_text=raw_text, question="", expected_answer=""))

    val: list[DataItem] = []
    for record in val_records:
        prompt = _format_prompt(record)
        rubric_criteria = _encode_rubric(record.get("rubric", []))
        field = str(record.get("field", "unknown"))
        val.append(
            DataItem(
                raw_text="",
                question=prompt,
                expected_answer="",
                metadata={
                    "domain": (field or "").lower(),
                    "topic": str(record.get("topic", "")),
                    "rubric_criteria": rubric_criteria,
                },
            )
        )

    return Dataset(
        train=train,
        val=val,
        test=[],
        scorer=None,
        val_scorer=RubricValScorer(judge_model=judge_model),
        available_categories=all_categories,
        category_key="domain",
    )
