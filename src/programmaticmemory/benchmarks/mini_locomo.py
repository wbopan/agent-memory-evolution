"""Mini LoCoMo benchmark — single conversation subset for fast iteration."""

from __future__ import annotations

import random
import re
from pathlib import Path

from programmaticmemory.benchmarks.locomo import _SESSION_KEY_RE, _format_session, ensure_data
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import TokenF1Scorer
from programmaticmemory.evolution.types import DataItem, Dataset

_EVIDENCE_RE = re.compile(r"^D(\d+):(\d+)$")


@register_dataset("mini_locomo")
def load_mini_locomo(
    *,
    num_val: int = 10,
    categories: tuple[int, ...] = (1, 2, 3, 4),
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load a single-conversation subset of LoCoMo for fast iteration.

    Uses all sessions from one conversation as train, and filters val QAs
    to only those whose evidence is fully contained in the train sessions.

    Args:
        num_val: Maximum number of val QA pairs.
        categories: QA category filter (1-4 only; 5 excluded by default).
        seed: Random seed for shuffling.
        data_dir: Override data directory.

    Returns:
        Dataset with TokenF1Scorer.
    """
    import json

    data_path = ensure_data(data_dir)
    samples = json.loads(data_path.read_text())

    rng = random.Random(seed)
    rng.shuffle(samples)
    sample = samples[0]

    conv = sample["conversation"]

    # Build train: all sessions
    session_keys = sorted(
        (k for k in conv if _SESSION_KEY_RE.match(k)),
        key=lambda k: int(_SESSION_KEY_RE.match(k).group(1)),
    )
    session_nums = set()
    train: list[DataItem] = []
    for key in session_keys:
        n = int(_SESSION_KEY_RE.match(key).group(1))
        session_nums.add(n)
        date_key = f"session_{n}_date_time"
        date_time = conv.get(date_key, "")
        text = _format_session(conv[key], date_time)
        train.append(DataItem(raw_text=text, question="", expected_answer=""))

    # Build val: only QAs whose evidence sessions are all in train
    eligible: list[DataItem] = []
    for qa in sample.get("qa", []):
        if qa.get("category") not in categories:
            continue
        evidence_sessions = set()
        for e in qa.get("evidence", []):
            m = _EVIDENCE_RE.match(e)
            if m:
                evidence_sessions.add(int(m.group(1)))
        if evidence_sessions and evidence_sessions.issubset(session_nums):
            eligible.append(DataItem(raw_text="", question=qa["question"], expected_answer=qa["answer"]))

    rng2 = random.Random(seed)
    rng2.shuffle(eligible)
    val = eligible[:num_val]

    return Dataset(train=train, val=val, test=[], scorer=TokenF1Scorer())
