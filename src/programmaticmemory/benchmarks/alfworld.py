"""ALFWorld benchmark — embodied task completion from ALFRED trajectories.

Train items: expert episode transcripts (ACTION/OBSERVATION pairs) collected by replaying
episodes in TextWorld with AlfredExpert planner. Falls back to structured metadata if
pre-computed trajectories are not available.

Val items: task objectives with game_file metadata for env interaction via ALFWorldValScorer.
"""

from __future__ import annotations

import json
import multiprocessing
import random
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from programmaticmemory.evolution.toolkit import completion_with_retry

# Monkey-patch TextWorld PDDL parser to handle Fast Downward internal variables.
# Fast Downward's SAS translation creates dummy atoms (e.g. "Atom dummy(val1)")
# whose arguments aren't in the PDDL :objects section, causing KeyError in
# textworld.envs.pddl.logic.Atom.get_fact().
#
# Fix: return a sentinel with is_negation=True for atoms with unknown variables.
# The caller (PddlState.__init__ line 214) already filters `not fact.is_negation`,
# so these dummy atoms are silently dropped without corrupting the game state.
try:
    import textworld.envs.pddl.logic as _pddl_logic

    class _SkipFact:
        is_negation = True

    _orig_get_fact = _pddl_logic.Atom.get_fact

    def _safe_get_fact(self, name2type={}):  # noqa: B006
        _, rest = self.name.split(" ", 1)
        _, args_str = rest.split("(", 1)
        for arg in args_str[:-1].split(", "):
            if arg and arg not in name2type:
                return _SkipFact()
        return _orig_get_fact(self, name2type)

    _pddl_logic.Atom.get_fact = _safe_get_fact
except (ImportError, AttributeError):
    pass  # textworld not installed or API changed

from programmaticmemory.benchmarks._download import download_and_extract_zip, get_data_dir
from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

_GH_RELEASE = "https://github.com/alfworld/alfworld/releases/download/0.2.2"
_JSON_URL = f"{_GH_RELEASE}/json_2.1.1_json.zip"
_TWPDDL_URL = f"{_GH_RELEASE}/json_2.1.1_tw-pddl.zip"

# Cache of validated game files: game_file_path -> True (has admissible commands) / False
_VALID_GAME_CACHE: dict[str, bool] = {}
_CACHE_FILE_NAME = ".alfworld_valid_games.json"


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


def _load_trajectories(dest_dir: Path, split: str) -> dict[str, dict]:
    """Load pre-computed expert trajectories for a split.

    Trajectories are keyed by game file path (Docker-internal). We build a lookup
    keyed by the relative path from json_2.1.1/ for cross-platform matching.

    Returns {relative_game_path: trajectory_record} or empty dict if not available.
    """
    traj_file = dest_dir / "trajectories" / f"{split}.json"
    if not traj_file.exists():
        return {}
    try:
        raw = json.loads(traj_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    # Re-key by relative path from json_2.1.1/
    lookup: dict[str, dict] = {}
    for key, val in raw.items():
        # Docker keys look like /data/alfworld/json_2.1.1/train/...
        marker = "json_2.1.1/"
        idx = key.find(marker)
        if idx >= 0:
            rel = key[idx + len(marker) :]
        else:
            rel = key
        lookup[rel] = val
    return lookup


def _format_training_text(task_desc: str, task_type: str, traj_data: dict) -> str:
    """Fallback: format trajectory metadata as training text for the KB."""
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


def _parse_trials(
    base_dir: Path, *, for_train: bool, trajectories: dict[str, dict] | None = None
) -> list[tuple[str, DataItem]]:
    """Parse all valid trial directories under base_dir.

    Returns (task_type, DataItem) pairs.
    - for_train=True: items have raw_text (expert transcript or fallback metadata)
    - for_train=False: items have question (task objective), metadata with game_file/task_type

    When trajectories dict is provided, successful expert episode transcripts are used
    as raw_text instead of the metadata-based fallback.
    """
    items: list[tuple[str, DataItem]] = []
    if not base_dir.exists():
        return items

    # Determine split name from base_dir (e.g. "train", "valid_unseen")
    split_name = base_dir.name

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
                # Try pre-computed expert trajectory first
                raw_text = ""
                if trajectories:
                    rel_key = f"{split_name}/{task_dir.name}/{trial_dir.name}/game.tw-pddl"
                    traj_record = trajectories.get(rel_key, {})
                    traj_text = traj_record.get("trajectory", "")
                    # Only use successful trajectories
                    if traj_text and traj_record.get("total_reward", 0) >= 1.0:
                        raw_text = traj_text

                if not raw_text:
                    raw_text = _format_training_text(task_desc, task_type, traj_data)

                item = DataItem(raw_text=raw_text, question="", expected_answer="")
            else:
                item = DataItem(
                    raw_text="",
                    question=task_desc,
                    expected_answer="Task completed successfully (reward=1.0)",
                    metadata={"game_file": str(pddl_path), "task_type": task_type},
                )

            items.append((task_type, item))

    return items


def _unwrap_single(value: Any, default: Any) -> Any:
    """Unwrap a batch-size-1 result from textworld.gym (handles nested lists)."""
    if value is None:
        return default
    if isinstance(value, (list, tuple)):
        if not value:
            return default
        first = value[0]
        if first is None:
            return default
        if isinstance(first, (list, tuple)):
            if not first:
                return default
            nested = first[0]
            return default if nested is None else nested
        return first
    return value


def _extract_admissible(info: dict[str, Any]) -> list[str]:
    """Extract admissible commands from info dict, handling nested list formats."""
    commands = info.get("admissible_commands")
    if isinstance(commands, list) and commands:
        if isinstance(commands[0], list):
            commands = commands[0]
        return [str(c) for c in commands if c]
    return []


def _parse_action_response(response: str, admissible: list[str]) -> str:
    """Parse LLM response to extract an admissible action."""
    text = (response or "").strip()
    if not text:
        return admissible[0] if admissible else "look"

    # Strip common prefixes like "action:", "Action -", etc.
    text = re.sub(r"^\s*action\s*[:=\-]\s*", "", text, flags=re.IGNORECASE)
    line = text.splitlines()[0].strip().strip('"').strip("'").strip("`")
    lowered = text.lower()

    if admissible:
        # Case-insensitive exact match
        for cmd in admissible:
            if line.lower() == str(cmd).lower():
                return cmd
        # Substring containment
        for cmd in admissible:
            if str(cmd).lower() in lowered:
                return cmd
        # Fallback to first admissible
        return admissible[0]

    return line if line else "look"


def _reset_with_timeout(env: Any, timeout_s: float) -> tuple[Any, Any]:
    """Reset env with timeout — alfworld env.reset() can hang on certain games."""
    reset_result: dict[str, Any] = {}

    def _do_reset() -> None:
        try:
            reset_result["value"] = env.reset()
        except Exception as exc:
            reset_result["error"] = exc

    reset_thread = threading.Thread(target=_do_reset, daemon=True)
    reset_thread.start()
    reset_thread.join(timeout_s)
    if reset_thread.is_alive():
        raise TimeoutError(f"ALFWorld env.reset() timed out after {timeout_s:.1f}s")
    if "error" in reset_result:
        raise reset_result["error"]
    return reset_result.get("value", ("", {}))


def _probe_game(game_file: str) -> bool:
    """Check if a game file produces admissible commands after env.reset().

    Returns True if the game is valid (has admissible commands), False otherwise.
    Results are cached in _VALID_GAME_CACHE.
    """
    if game_file in _VALID_GAME_CACHE:
        return _VALID_GAME_CACHE[game_file]

    try:
        import textworld
        import textworld.gym
        from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

        request_infos = textworld.EnvInfos(
            feedback=True,
            description=True,
            inventory=True,
            admissible_commands=True,
            objective=True,
            extras=["gamefile"],
        )
        wrappers = [AlfredDemangler(), AlfredInfos]
        env_id = textworld.gym.register_games(
            [game_file],
            request_infos,
            batch_size=1,
            auto_reset=False,
            max_episode_steps=5,
            asynchronous=False,
            name=f"probe-{uuid.uuid4().hex}",
            wrappers=wrappers,
        )
        env = textworld.gym.make(env_id)
        try:
            _, info_batch = env.reset()
            raw_cmds = info_batch.get("admissible_commands", [])
            flat = raw_cmds[0] if raw_cmds and isinstance(raw_cmds[0], list) else raw_cmds
            valid = len(flat) > 0
        finally:
            try:
                env.close()
            except Exception:
                pass
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).debug("_probe_game(%s) failed: %s", game_file, e)
        valid = False

    _VALID_GAME_CACHE[game_file] = valid
    return valid


def _probe_game_isolated(game_file: str) -> bool:
    """Probe a game in a fully isolated subprocess.

    Fast Downward's C library can call exit()/abort() on certain game files,
    killing the host process. subprocess.run() gives true process isolation.
    """
    script = (
        "from programmaticmemory.benchmarks.alfworld import _probe_game; "
        "import sys; "
        f"sys.exit(0 if _probe_game({game_file!r}) else 1)"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            timeout=60,
        )
        return result.returncode == 0
    except Exception as e:
        import logging as _logging

        _logging.getLogger(__name__).debug("_probe_game_isolated(%s) failed: %s", game_file, e)
        return False


def _run_episode(
    game_file: str,
    objective: str,
    tips: str,
    task_model: str,
    max_steps: int,
    always_on_knowledge: str = "",
    reasoning_effort: str | None = None,
) -> tuple[str, float, str]:
    """Run a single ALFWorld episode in its own process.

    Module-level function (not a method) so it can be pickled for
    ``ProcessPoolExecutor``. TextWorld's tatsu-based parsers use global
    singletons that are not thread-safe, so each episode must run in a
    separate process.

    Returns:
        tuple[str, float, str]: (transcript, score, rationale)
    """
    import os

    os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
    os.environ.setdefault("MKL_NUM_THREADS", "1")

    import textworld
    import textworld.gym
    from alfworld.agents.environment.alfred_tw_env import AlfredDemangler, AlfredInfos

    request_infos = textworld.EnvInfos(
        feedback=True,
        description=True,
        inventory=True,
        admissible_commands=True,
        objective=True,
        extras=["gamefile"],
    )
    wrappers = [AlfredDemangler(), AlfredInfos]
    env_id = textworld.gym.register_games(
        [game_file],
        request_infos,
        batch_size=1,
        auto_reset=False,
        max_episode_steps=max_steps,
        asynchronous=False,
        name=f"alfworld-eval-{uuid.uuid4().hex}",
        wrappers=wrappers,
    )
    env = textworld.gym.make(env_id)
    try:
        obs_batch, info_batch = _reset_with_timeout(env, 60.0)
        obs = _unwrap_single(obs_batch, "")
        info = _unwrap_single(info_batch, {})

        admissible = _extract_admissible(info if isinstance(info, dict) else {})
        if not admissible:
            return (
                "Game has no admissible commands (corrupt PDDL state)",
                0.0,
                "ALFWorld episode. Episode failed before execution: no admissible actions.",
            )
        trajectory_lines: list[str] = [str(obs).strip()] if obs else []
        total_reward = 0.0
        done = False

        for _step in range(max_steps):
            inventory = ""
            if isinstance(info, dict):
                inv = info.get("inventory") or info.get("inv") or ""
                if isinstance(inv, list):
                    inv = inv[0] if inv else ""
                inventory = str(inv) if inv else ""

            action = _select_action(
                objective,
                tips,
                "\n".join(trajectory_lines),
                inventory,
                admissible,
                task_model,
                always_on_knowledge,
                reasoning_effort=reasoning_effort,
            )
            trajectory_lines.append(f"ACTION: {action}")

            obs_batch, scores, dones, infos = env.step([action])
            obs = _unwrap_single(obs_batch, "")
            info = _unwrap_single(infos, {})

            reward = float(scores[0]) if isinstance(scores, (list, tuple)) and scores else float(scores or 0)
            done = bool(dones[0]) if isinstance(dones, (list, tuple)) and dones else bool(dones)
            total_reward += reward

            trajectory_lines.append(f"OBSERVATION: {str(obs).strip()}")
            admissible = _extract_admissible(info if isinstance(info, dict) else {})

            if done:
                break

        transcript = "\n".join(trajectory_lines)
        if done and total_reward >= 1.0:
            rationale = (
                f"ALFWorld episode (binary success: complete the household task). "
                f"Result: SUCCESS in {_step + 1}/{max_steps} steps. "
                f'Task: "{objective}"'
            )
            score = 1.0
        else:
            last_lines = trajectory_lines[-6:] if len(trajectory_lines) >= 6 else trajectory_lines
            last_context = "\n  ".join(last_lines)
            score = 0.0
            rationale = (
                f"ALFWorld episode (binary success: complete the household task). "
                f"Result: FAILED after {_step + 1}/{max_steps} steps. "
                f'Task: "{objective}"\nLast steps:\n  {last_context}'
            )
        return transcript, score, rationale
    finally:
        try:
            env.close()
        except Exception:
            pass


def _select_action(
    objective: str,
    tips: str,
    trajectory_text: str,
    inventory: str,
    admissible: list[str],
    task_model: str,
    always_on_knowledge: str = "",
    *,
    reasoning_effort: str | None = None,
) -> str:
    """Use LLM to select the next action from admissible commands."""
    lines = [
        "You are controlling a text-based ALFWorld environment.",
        "Your job: choose the NEXT action as ONE text command.",
        "Output ONLY the command string, with no extra text.",
        "You MUST choose an action from the admissible actions list and copy it EXACTLY.",
    ]

    if objective:
        lines += ["", "Goal:", objective.strip()]

    aok = always_on_knowledge.strip() if always_on_knowledge else ""
    if aok:
        lines += ["", "Always-on knowledge:", aok]

    if tips and tips.strip():
        lines += ["", "Retrieved procedural tips (optional, short & actionable):", tips.strip()]

    lines += ["", "Interaction history so far (most recent info matters most):"]
    if trajectory_text and trajectory_text.strip():
        traj_lines = trajectory_text.strip().splitlines()
        if len(traj_lines) > 40:
            traj_lines = ["(earlier history truncated)", "..."] + traj_lines[-40:]
        lines.append("\n".join(traj_lines))
    else:
        lines.append("(empty)")

    if inventory and inventory.strip() and inventory.strip().lower() not in {"none", "null", "(empty)"}:
        lines += ["", "Inventory (if available):", inventory.strip()]

    if admissible:
        lines += ["", "Admissible actions (choose exactly ONE and copy it verbatim):"]
        for cmd in admissible:
            lines.append(f"- {cmd}")
        lines += ["", "Now output exactly one line: the chosen action (must match one item above)."]

    prompt = "\n".join(lines)

    extra: dict = {}
    if reasoning_effort is not None:
        extra["reasoning_effort"] = reasoning_effort
    resp = completion_with_retry(
        model=task_model,
        messages=[{"role": "user", "content": prompt}],
        caching=True,
        **extra,
    )
    raw = resp.choices[0].message.content.strip()
    return _parse_action_response(raw, admissible)


class ALFWorldValScorer:
    """Pluggable val scorer that runs ALFWorld episodes via TextWorld environments.

    Uses textworld.gym with AlfredDemangler + AlfredInfos wrappers for human-readable
    object names and proper Gym-style API. Episodes run in parallel via ProcessPoolExecutor
    (TextWorld's tatsu-based parsers use global singletons that are not thread-safe).
    """

    def __init__(self, max_steps: int = 50, max_workers: int = 20, episode_timeout: float = 300.0) -> None:
        self.max_steps = max_steps
        self.max_workers = max_workers
        self.episode_timeout = episode_timeout

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
        always_on_knowledge: str = "",
        *,
        reasoning_effort: str | None = None,
    ) -> list[tuple[str, float, str]]:
        """Run one episode per item in parallel, return (transcript, score, rationale) triples."""
        import concurrent.futures

        workers = min(self.max_workers, len(items)) if items else 1
        # Use 'spawn' context: TextWorld's C library crashes (exit/abort) in
        # forked workers can break the pool. 'spawn' fully isolates each episode.
        ctx = multiprocessing.get_context("spawn")
        results: list[tuple[str, float, str]] = []
        try:
            with concurrent.futures.ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as pool:
                futures = [
                    pool.submit(
                        _run_episode,
                        item.metadata["game_file"],
                        item.question,
                        tips,
                        task_model,
                        self.max_steps,
                        always_on_knowledge,
                        reasoning_effort,
                    )
                    for item, tips in zip(items, retrieved, strict=True)
                ]
                for f in futures:
                    try:
                        results.append(f.result(timeout=self.episode_timeout))
                    except Exception as exc:
                        results.append((f"Episode failed: {exc}", 0.0, f"ALFWorld episode. Episode crashed: {exc}"))
        except Exception as e:
            # Pool broke — fill remaining items with 0.0
            from programmaticmemory.logging.logger import get_logger

            get_logger().log(
                f"ALFWorld process pool broke: {e} — filling {len(items) - len(results)} remaining with 0.0",
                header="ALFWORLD",
            )
            while len(results) < len(items):
                results.append(("Episode failed: broken process pool", 0.0, "ALFWorld episode. Process pool broke"))
        return results


@register_dataset("alfworld")
def load_alfworld(
    *,
    num_train: int | None = None,
    num_val: int | None = None,
    category: str | None = None,
    eval_split: str = "unseen",
    seed: int = 42,
    data_dir: str | Path | None = None,
) -> Dataset:
    """Load ALFWorld benchmark.

    Train items come from the train split (structured training text as raw_text).
    Val items come from valid_seen or valid_unseen split (task objectives with game_file metadata).

    Args:
        num_train: Number of training items.
        num_val: Number of validation items (None = all).
        category: Filter to a specific task type (e.g. "heat", "cool"). None = all.
        eval_split: Which val split to use: "unseen" (default) or "seen".
        seed: Random seed for shuffling.
        data_dir: Override data directory.

    Returns:
        Dataset with ALFWorldValScorer.
    """
    if eval_split not in ("seen", "unseen"):
        raise ValueError(f"eval_split must be 'seen' or 'unseen', got {eval_split!r}")

    dest_dir = ensure_data(data_dir)
    json_dir = dest_dir / "json_2.1.1"

    # Load pre-computed expert trajectories (if available)
    train_trajectories = _load_trajectories(dest_dir, "train")

    # Parse train split for training items (with raw_text)
    train_dir = json_dir / "train"
    train_typed = _parse_trials(train_dir, for_train=True, trajectories=train_trajectories)

    # Parse val split
    # ALFWorld directory naming: valid_unseen and valid_train (not valid_seen)
    val_split_name = "valid_unseen" if eval_split == "unseen" else "valid_train"
    val_trajectories = _load_trajectories(dest_dir, val_split_name)
    valid_dir = json_dir / val_split_name
    val_typed = _parse_trials(valid_dir, for_train=False, trajectories=val_trajectories)

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

    train = train_items[:num_train] if num_train is not None else train_items
    val = val_items[:num_val] if num_val is not None else val_items

    # Only use env scorer if alfworld is available
    val_scorer = None
    try:
        import alfworld  # noqa: F401

        # Filter val items with broken PDDL state (0 admissible commands after env.reset).
        # Some game files trigger Fast Downward C-library crashes, so each probe runs
        # in a separate process. Results are cached to disk for subsequent runs.
        pre_filter = len(val)
        import concurrent.futures

        # Load disk cache
        cache_path = dest_dir / _CACHE_FILE_NAME
        if not _VALID_GAME_CACHE and cache_path.exists():
            try:
                _VALID_GAME_CACHE.update(json.loads(cache_path.read_text()))
            except (json.JSONDecodeError, OSError):
                pass

        game_files = [item.metadata["game_file"] for item in val]
        uncached = [gf for gf in game_files if gf not in _VALID_GAME_CACHE]
        if uncached:
            from programmaticmemory.logging.logger import get_logger

            get_logger().log(f"Validating {len(uncached)} game files (first run only)...", header="ALFWORLD")
            # Use ThreadPoolExecutor + subprocess.run for each probe.
            # Fast Downward's C library can call exit()/abort() on corrupt
            # games; subprocess.run gives true process isolation.
            # Low worker count (4) avoids TextWorld temp-file contention.
            workers = min(4, len(uncached))
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(_probe_game_isolated, gf): gf for gf in uncached}
                for future in concurrent.futures.as_completed(futures):
                    gf = futures[future]
                    try:
                        _VALID_GAME_CACHE[gf] = future.result(timeout=90)
                    except Exception:
                        _VALID_GAME_CACHE[gf] = False
            # Persist to disk
            try:
                cache_path.write_text(json.dumps(_VALID_GAME_CACHE))
            except OSError:
                from programmaticmemory.logging.logger import get_logger

                get_logger().log("Failed to persist ALFWorld valid game cache", header="ALFWORLD")

        val = [item for item in val if _VALID_GAME_CACHE.get(item.metadata["game_file"], False)]
        if len(val) < pre_filter:
            from programmaticmemory.logging.logger import get_logger

            get_logger().log(
                f"Filtered {pre_filter - len(val)}/{pre_filter} broken val games (no admissible commands)",
                header="ALFWORLD",
            )

        val_scorer = ALFWorldValScorer(max_steps=50)
    except ImportError:
        import logging as _logging

        _logging.getLogger(__name__).debug("alfworld not installed, using LLM answer path")

        pass  # Fall back to default LLM answer path if alfworld not installed

    return Dataset(
        train=train,
        val=val,
        test=[],
        compare_fn=ExactMatchScorer() if val_scorer is None else None,
        val_scorer=val_scorer,
        available_categories=all_categories,
        category_key="task_type",
    )
