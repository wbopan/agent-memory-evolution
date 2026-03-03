"""ALFWorld benchmark — embodied task completion from ALFRED trajectories.

Train items: structured training text from trajectory metadata (task type, description, PDDL params, scene).
Val items: task objectives with game_file metadata for env interaction via ALFWorldValScorer.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

import litellm

from programmaticmemory.benchmarks._download import download_and_extract_zip, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.types import DataItem, Dataset

_GH_RELEASE = "https://github.com/alfworld/alfworld/releases/download/0.2.2"
_JSON_URL = f"{_GH_RELEASE}/json_2.1.1_json.zip"
_TWPDDL_URL = f"{_GH_RELEASE}/json_2.1.1_tw-pddl.zip"


def ensure_data(data_dir: str | Path | None = None) -> Path:
    """Download and extract ALFRED json + tw-pddl data (both train and valid_unseen splits)."""
    dest_dir = get_data_dir("alfworld", data_dir)
    json_dir = dest_dir / "json_2.1.1"

    # Check valid_unseen
    has_valid = json_dir.exists() and (json_dir / "valid_unseen").exists()
    has_valid_pddl = has_valid and any((json_dir / "valid_unseen").rglob("game.tw-pddl"))

    # Check train
    has_train = json_dir.exists() and (json_dir / "train").exists()
    has_train_pddl = has_train and any((json_dir / "train").rglob("game.tw-pddl"))

    # Download valid_unseen JSON + PDDL
    if not has_valid:
        download_and_extract_zip(
            _JSON_URL,
            dest_dir,
            members_filter=lambda m: "valid_unseen" in m,
            skip_if_exists=False,
        )
    if not has_valid_pddl:
        download_and_extract_zip(
            _TWPDDL_URL,
            dest_dir,
            members_filter=lambda m: "valid_unseen" in m,
            skip_if_exists=False,
        )

    # Download train JSON + PDDL
    if not has_train:
        download_and_extract_zip(
            _JSON_URL,
            dest_dir,
            members_filter=lambda m: "train" in m,
            skip_if_exists=False,
        )
    if not has_train_pddl:
        download_and_extract_zip(
            _TWPDDL_URL,
            dest_dir,
            members_filter=lambda m: "train" in m,
            skip_if_exists=False,
        )

    return dest_dir


def _format_training_text(task_desc: str, task_type: str, traj_data: dict) -> str:
    """Format trajectory metadata as training text for the KB."""
    parts = [f"Task type: {task_type}", f"Task description: {task_desc}"]

    pddl = traj_data.get("pddl_params", {})
    if pddl:
        parts.append("PDDL parameters:")
        for key, val in sorted(pddl.items()):
            parts.append(f"  {key}: {val}")

    scene = traj_data.get("scene", {})
    if scene:
        parts.append(f"Scene: {scene}")

    return "\n".join(parts)


def _parse_trials(base_dir: Path, *, for_train: bool) -> list[tuple[str, DataItem]]:
    """Parse all valid trial directories under base_dir.

    Returns (task_type, DataItem) pairs.
    - for_train=True: items have raw_text (training text), empty question/expected_answer
    - for_train=False: items have question (task objective), metadata with game_file/task_type
    """
    items: list[tuple[str, DataItem]] = []
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

            if for_train:
                raw_text = _format_training_text(task_desc, task_type, traj_data)
                item = DataItem(raw_text=raw_text, question="", expected_answer="")
            else:
                item = DataItem(
                    raw_text="",
                    question=task_desc,
                    expected_answer="",
                    metadata={"game_file": str(pddl_path), "task_type": task_type},
                )

            items.append((task_type, item))

    return items


class ALFWorldValScorer:
    """Pluggable val scorer that runs ALFWorld episodes via TextWorld environments.

    For each val item, creates a TextWorld env from the game_file, uses LLM to select
    actions based on KB-retrieved tips, and returns binary success (1.0/0.0).
    """

    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
    ) -> list[tuple[str, float]]:
        """Run one episode per item, return (transcript, score) pairs."""
        results: list[tuple[str, float]] = []
        for item, tips in zip(items, retrieved, strict=True):
            game_file = item.metadata["game_file"]
            objective = item.question
            transcript, score = self._run_episode(game_file, objective, tips, task_model)
            results.append((transcript, score))
        return results

    def _run_episode(self, game_file: str, objective: str, tips: str, task_model: str) -> tuple[str, float]:
        """Run a single ALFWorld episode. Returns (transcript, score)."""
        env = self._create_env(game_file)
        try:
            obs, info = env.reset()
            admissible = info.get("admissible_commands", [])
            history: list[str] = [f"OBSERVATION: {obs}"]
            score = 0.0

            for _step in range(self.max_steps):
                action = self._select_action(objective, tips, history, admissible, task_model)
                history.append(f"ACTION: {action}")

                obs, reward, done, info = env.step(action)
                history.append(f"OBSERVATION: {obs}")
                admissible = info.get("admissible_commands", [])

                if done:
                    score = float(reward)
                    break

            return "\n".join(history), score
        finally:
            env.close()

    def _create_env(self, game_file: str):
        """Create a TextWorld environment from a game file. Lazy-imports alfworld."""
        import alfworld.agents.environment  # noqa: F401 — side-effect import registers alfworld envs
        import textworld

        env = textworld.start(game_file)
        return env

    def _select_action(
        self,
        objective: str,
        tips: str,
        history: list[str],
        admissible: list[str],
        task_model: str,
    ) -> str:
        """Use LLM to select the next action from admissible commands."""
        # Build recent history (last 20 entries to avoid context overflow)
        recent = history[-20:]
        history_text = "\n".join(recent)
        admissible_text = "\n".join(f"- {cmd}" for cmd in admissible)

        prompt = (
            "You are controlling a text-based ALFWorld environment.\n"
            "Choose the NEXT action as ONE text command.\n"
            "You MUST choose from the admissible actions and copy it EXACTLY.\n\n"
            f"Goal: {objective}\n\n"
            f"Procedural tips from knowledge base:\n{tips}\n\n"
            f"Recent interaction history:\n{history_text}\n\n"
            f"Admissible actions (choose exactly ONE):\n{admissible_text}\n\n"
            "Output exactly one line: the chosen action."
        )

        resp = litellm.completion(
            model=task_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=64,
            temperature=0.0,
        )
        raw = resp.choices[0].message.content.strip()

        # Exact match
        if raw in admissible:
            return raw

        # Substring match: find admissible command contained in LLM output
        for cmd in admissible:
            if cmd in raw:
                return cmd

        # Fallback to first admissible
        if admissible:
            return admissible[0]

        return raw


@register_dataset("alfworld")
def load_alfworld(
    *,
    num_train: int = 50,
    num_val: int | None = None,
    category: str | None = None,
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load ALFWorld benchmark.

    Train items come from the train split (structured training text as raw_text).
    Val items come from valid_unseen split (task objectives with game_file metadata).

    Args:
        num_train: Number of training items.
        num_val: Number of validation items (None = all).
        category: Filter to a specific task type (e.g. "heat", "cool"). None = all.
        seed: Random seed for shuffling.
        data_dir: Override data directory.

    Returns:
        Dataset with ALFWorldValScorer.
    """
    dest_dir = ensure_data(data_dir)
    json_dir = dest_dir / "json_2.1.1"

    # Parse train split for training items (with raw_text)
    train_dir = json_dir / "train"
    train_typed = _parse_trials(train_dir, for_train=True)

    # Parse valid_unseen split for val items (with metadata)
    valid_dir = json_dir / "valid_unseen"
    val_typed = _parse_trials(valid_dir, for_train=False)

    # Available categories: union of task types from both splits
    all_categories = sorted({t for t, _ in train_typed} | {t for t, _ in val_typed})

    if category is not None:
        train_filtered = [(t, item) for t, item in train_typed if t == category]
        val_filtered = [(t, item) for t, item in val_typed if t == category]
        if not train_filtered and not val_filtered:
            raise ValueError(f"category {category!r} not found. Available: {all_categories}")
        train_typed = train_filtered
        val_typed = val_filtered

    train_items = [item for _, item in train_typed]
    val_items = [item for _, item in val_typed]

    rng = random.Random(seed)
    rng.shuffle(train_items)
    rng.shuffle(val_items)

    train = train_items[:num_train]
    val = val_items[:num_val] if num_val is not None else val_items

    return Dataset(
        train=train,
        val=val,
        test=[],
        val_scorer=ALFWorldValScorer(),
        available_categories=all_categories,
    )
