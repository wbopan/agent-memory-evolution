"""LoCoMo benchmark — long conversation memory with multi-session QA."""

from __future__ import annotations

import json
import random
import re
from pathlib import Path

from programmaticmemory.benchmarks._download import download_file, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import TokenF1Scorer
from programmaticmemory.evolution.types import DataItem, Dataset

_LOCOMO_URL = "https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json"


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download LoCoMo dataset if not already present."""
    dest_dir = get_data_dir("locomo", data_dir)
    dest = dest_dir / "locomo10.json"
    download_file(_LOCOMO_URL, dest)
    return dest


def _format_session(turns: list[dict], date_time: str) -> str:
    """Format a single conversation session as text."""
    header = f"[{date_time}]"
    lines = [f"{t['speaker']}: {t['text']}" for t in turns]
    return header + "\n" + "\n".join(lines)


_SESSION_KEY_RE = re.compile(r"^session_(\d+)$")


@register_dataset("locomo")
def load_locomo(
    *,
    num_conversations: int | None = None,
    categories: tuple[int, ...] = (1, 2, 3, 4),
    category: str | None = None,
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load LoCoMo benchmark.

    Args:
        num_conversations: Limit number of conversations (None = all).
        categories: QA category filter (1-4 only; 5 excluded by default).
        category: Select a single conversation by index (post-shuffle). None = all.
        seed: Random seed for shuffling.
        data_dir: Override data directory.

    Returns:
        Dataset with TokenF1Scorer.
    """
    data_path = ensure_data(data_dir)
    samples = json.loads(data_path.read_text())

    rng = random.Random(seed)
    rng.shuffle(samples)
    if num_conversations is not None:
        samples = samples[:num_conversations]

    # Category filtering: select a single conversation by index
    if category is not None:
        idx = int(category)
        if idx < 0 or idx >= len(samples):
            raise ValueError(f"category {category!r} out of range: only {len(samples)} conversations available")
        samples = [samples[idx]]

    train: list[DataItem] = []
    val: list[DataItem] = []

    for sample in samples:
        conv = sample["conversation"]

        # Extract session keys in order
        session_keys = sorted(
            (k for k in conv if _SESSION_KEY_RE.match(k)),
            key=lambda k: int(_SESSION_KEY_RE.match(k).group(1)),
        )

        for key in session_keys:
            n = _SESSION_KEY_RE.match(key).group(1)
            date_key = f"session_{n}_date_time"
            date_time = conv.get(date_key, "")
            text = _format_session(conv[key], date_time)
            train.append(DataItem(raw_text=text, question="", expected_answer=""))

        # QA pairs for validation
        for qa in sample.get("qa", []):
            cat = qa.get("category")
            if cat not in categories:
                continue
            val.append(DataItem(raw_text="", question=qa["question"], expected_answer=qa["answer"]))

    return Dataset(train=train, val=val, test=[], scorer=TokenF1Scorer())
