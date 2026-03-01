"""ALFWorld benchmark — embodied task completion from ALFRED trajectories."""

from __future__ import annotations

import json
import random
from pathlib import Path

from programmaticmemory.benchmarks._download import download_and_extract_zip, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

_GH_RELEASE = "https://github.com/alfworld/alfworld/releases/download/0.2.2"
_JSON_URL = f"{_GH_RELEASE}/json_2.1.1_json.zip"
_TWPDDL_URL = f"{_GH_RELEASE}/json_2.1.1_tw-pddl.zip"

_KEY_ELEMENTS = {
    "heat": "microwave",
    "cool": "fridge",
    "clean": "sinkbasin",
    "look_at_obj_in_light": "desklamp",
}


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download and extract ALFRED json + tw-pddl data."""
    dest_dir = get_data_dir("alfworld", data_dir)
    json_dir = dest_dir / "json_2.1.1"
    has_valid = json_dir.exists() and (json_dir / "valid_unseen").exists()
    has_pddl = has_valid and any((json_dir / "valid_unseen").rglob("game.tw-pddl"))

    if not has_valid:
        download_and_extract_zip(
            _JSON_URL,
            dest_dir,
            members_filter=lambda m: "valid_unseen" in m,
            skip_if_exists=False,
        )
    if not has_pddl:
        download_and_extract_zip(
            _TWPDDL_URL,
            dest_dir,
            members_filter=lambda m: "valid_unseen" in m,
            skip_if_exists=False,
        )
    return dest_dir


def _derive_expected(task_type: str, traj_data: dict) -> str:
    """Derive expected answer from task type and trajectory data."""
    # Check key elements mapping for single-object tasks
    for key, element in _KEY_ELEMENTS.items():
        if key in task_type:
            return element

    # pick_and_place, pick_two → use pddl_params parent_target
    pddl = traj_data.get("pddl_params", {})
    parent_target = pddl.get("parent_target", "")
    if parent_target:
        return parent_target

    return ""


def _parse_trials(base_dir: Path) -> list[DataItem]:
    """Parse all valid trial directories under base_dir."""
    items = []
    if not base_dir.exists():
        return items

    for task_dir in sorted(base_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        for trial_dir in sorted(task_dir.iterdir()):
            if not trial_dir.is_dir():
                continue

            traj_path = trial_dir / "traj_data.json"
            pddl_path = trial_dir / "game.tw-pddl"

            # Solvable filter: only include tasks with game.tw-pddl
            if not traj_path.exists() or not pddl_path.exists():
                continue

            try:
                traj_data = json.loads(traj_path.read_text())
            except (json.JSONDecodeError, OSError):
                continue

            # Extract task description
            anns = traj_data.get("turk_annotations", {}).get("anns", [])
            if not anns:
                continue
            task_desc = anns[0].get("task_desc", "")
            if not task_desc:
                continue

            # Derive task type from directory name
            task_type = task_dir.name.split("-")[0] if "-" in task_dir.name else task_dir.name
            expected = _derive_expected(task_type, traj_data)

            items.append(DataItem(raw_text="", question=task_desc, expected_answer=expected))

    return items


@register_dataset("alfworld")
def load_alfworld(
    *,
    num_train: int = 50,
    num_val: int | None = None,
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load ALFWorld benchmark.

    Args:
        num_train: Number of training items.
        num_val: Number of validation items (None = all remaining).
        seed: Random seed for shuffling.
        data_dir: Override data directory.

    Returns:
        Dataset with ExactMatchScorer.
    """
    dest_dir = ensure_data(data_dir)

    # Look for valid_unseen under json_2.1.1
    json_dir = dest_dir / "json_2.1.1"
    valid_dir = json_dir / "valid_unseen"

    items = _parse_trials(valid_dir)

    rng = random.Random(seed)
    rng.shuffle(items)

    train = items[:num_train]
    remaining = items[num_train:]
    val = remaining[:num_val] if num_val is not None else remaining

    return Dataset(train=train, val=val, test=[], scorer=ExactMatchScorer())
