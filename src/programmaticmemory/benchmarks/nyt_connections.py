"""NYT Connections benchmark — group 16 words into 4 themed categories."""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

from programmaticmemory.benchmarks._download import download_file, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.types import DataItem, Dataset

_DATA_URL = "https://huggingface.co/datasets/tm21cy/NYT-Connections/resolve/main/ConnectionsFinalDataset.json"

_TASK_DESCRIPTION = (
    "You are solving an NYT Connections puzzle. "
    "Given 16 words, identify four groups of four words that share a common theme or connection.\n\n"
    "Output exactly four lines. Each line should contain four comma-separated words forming one group. "
    "No group names, explanations, or other text — just the four lines of grouped words.\n\n"
    "Think carefully about:\n"
    "- Literal categories (same type of thing)\n"
    "- Wordplay (homophones, palindromes, letter patterns)\n"
    "- Cultural references (phrases, titles, names)\n"
    "- Tricky connections that seem to fit multiple categories"
)


class ConnectionsScorer:
    """Scorer for NYT Connections puzzles.

    Parses output and expected into groups of words, counts exact group matches.
    Returns correct_count / 4 (partial credit: 0.0, 0.25, 0.5, 0.75, 1.0).
    """

    def __call__(self, output: str, expected: str) -> float:
        predicted = self._parse_groups(output)
        answer = self._parse_groups(expected)
        if not answer:
            return 0.0
        correct = self._count_correct_groups(predicted, answer)
        return correct / len(answer)

    @staticmethod
    def _parse_groups(text: str) -> list[set[str]]:
        """Parse text into groups of uppercase words."""
        groups = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            words = {w.strip().upper() for w in line.split(",") if w.strip()}
            if words:
                groups.append(words)
        return groups

    @staticmethod
    def _count_correct_groups(predicted: list[set[str]], answer: list[set[str]]) -> int:
        """Count exact group matches via bipartite matching."""
        correct = 0
        used: set[int] = set()
        for pred in predicted:
            for i, ans in enumerate(answer):
                if i in used:
                    continue
                if pred == ans:
                    correct += 1
                    used.add(i)
                    break
        return correct


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download NYT Connections dataset if not present."""
    dest_dir = get_data_dir("nyt_connections", data_dir)
    download_file(_DATA_URL, dest_dir / "ConnectionsFinalDataset.json")
    return dest_dir


def _puzzle_to_dataitem(puzzle: dict) -> DataItem:
    """Convert a raw puzzle dict to a DataItem with self-contained question."""
    words = list(puzzle["words"])
    date_str = str(puzzle.get("date") or "")

    # Deterministic shuffle using date hash (same as GEPA)
    date_hash = int(hashlib.md5(date_str.encode()).hexdigest(), 16) % (2**32)
    rng = random.Random(date_hash)
    rng.shuffle(words)

    # Build self-contained question with task description + words
    question = f"{_TASK_DESCRIPTION}\n\nWords: {', '.join(words)}"

    # Build expected answer: 4 lines of comma-separated group words
    answer_lines = []
    for group in puzzle["answers"]:
        answer_lines.append(", ".join(group["words"]))
    expected_answer = "\n".join(answer_lines)

    return DataItem(raw_text="", question=question, expected_answer=expected_answer)


@register_dataset("nyt_connections")
def load_nyt_connections(
    *,
    train_ratio: float = 0.5,
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load NYT Connections benchmark.

    Args:
        train_ratio: Fraction of puzzles for training (rest goes to val).
        seed: Random seed for shuffling.
        data_dir: Override data directory for testing.

    Returns:
        Dataset with ConnectionsScorer. QA-only pattern (raw_text="").
    """
    dest_dir = ensure_data(data_dir)
    data_path = dest_dir / "ConnectionsFinalDataset.json"
    puzzles = json.loads(data_path.read_text())

    items = [_puzzle_to_dataitem(p) for p in puzzles]

    rng = random.Random(seed)
    rng.shuffle(items)

    split = int(len(items) * train_ratio)
    train = items[:split]
    val = items[split:]

    return Dataset(train=train, val=val, test=[], scorer=ConnectionsScorer())
