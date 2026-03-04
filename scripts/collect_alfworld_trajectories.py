"""Collect ALFWorld expert trajectories by replaying episodes in TextWorld.

Faithfully replicates MemSkill's trajectory collection approach:
runs AlfredExpert planner in TextWorld environments, records full
(ACTION, OBSERVATION) transcripts.

Requires alfworld+textworld (run in Docker on macOS ARM):

    docker build --platform linux/amd64 -t alfworld-replay -f Dockerfile.alfworld .
    docker run --platform linux/amd64 --rm \
      -v $(pwd)/data/alfworld:/data/alfworld \
      -v $(pwd)/scripts:/app/scripts \
      alfworld-replay python /app/scripts/collect_alfworld_trajectories.py \
        --data-dir /data/alfworld \
        --output /data/alfworld/trajectories \
        --split train --workers 4

Output: one JSON file per split in --output dir, keyed by game file path.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import threading
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import textworld
import textworld.gym
from alfworld.agents.environment.alfred_tw_env import (
    AlfredDemangler,
    AlfredExpert,
    AlfredExpertType,
    AlfredInfos,
)
from tqdm import tqdm

# Task types to collect (same as MemSkill — excludes pick_and_place_with_movable_recep)
TASK_TYPES = {
    "pick_and_place_simple",
    "look_at_obj_in_light",
    "pick_clean_then_place_in_recep",
    "pick_heat_then_place_in_recep",
    "pick_cool_then_place_in_recep",
    "pick_two_obj_and_place",
}


def _ensure_scalar(v: Any) -> Any:
    """Unwrap batched/nested values to scalar."""
    while isinstance(v, (list, tuple)):
        if len(v) == 0:
            return None
        v = v[0]
    return v


def _extract_plan(info: dict) -> list[str]:
    """Extract expert plan from info dict, handling nested list/string wrapping."""
    raw = info.get("extra.expert_plan") or info.get("expert_plan", [])
    # Unwrap outer list wrapping (TextWorld returns [[cmd1, cmd2, ...]])
    while isinstance(raw, (list, tuple)) and len(raw) == 1:
        inner = raw[0]
        if isinstance(inner, str):
            try:
                parsed = ast.literal_eval(inner)
                if isinstance(parsed, (list, tuple)):
                    raw = parsed
                    continue
            except Exception:
                pass
            break
        elif isinstance(inner, (list, tuple)):
            raw = inner
        else:
            break
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        return []
    return [str(x) for x in raw]


def _extract_objective(first_obs: str) -> str:
    """Extract task objective from initial observation text."""
    match = re.search(r"Your task is to:\s*(.+)", first_obs, re.IGNORECASE | re.DOTALL)
    if not match:
        return ""
    objective = match.group(1).strip().splitlines()[0].strip()
    return objective.rstrip(".")


def _build_trajectory_text(steps: list[dict]) -> str:
    """Build MemSkill-style trajectory text from step records."""
    if not steps:
        return ""
    parts: list[str] = []
    first_obs = steps[0].get("observation", "")
    if first_obs:
        parts.append(str(first_obs).strip())
        parts.append("")
        parts.append("")
    for step in steps[1:]:
        action = step.get("action")
        if action:
            parts.append(f"ACTION: {action}")
        obs = step.get("observation", "")
        if obs:
            parts.append(f"OBSERVATION: {str(obs).strip()}")
    last_step = steps[-1] if steps else {}
    last_done = bool(last_step.get("done"))
    last_reward = float(last_step.get("reward") or 0.0)
    status = "SUCCESS" if (last_done and last_reward >= 1.0) else "FAILED"
    parts.append(f"\n\nTRAJECTORY_STATUS: {status}")
    return "\n".join(parts).strip()


def _collect_game_files(data_dir: str, split: str) -> dict[str, list[str]]:
    """Walk split directory to find solvable game files, grouped by task type."""
    split_map = {"train": "train", "valid_unseen": "valid_unseen", "valid_seen": "valid_seen"}
    split_dir = os.path.join(data_dir, "json_2.1.1", split_map[split])

    game_files_by_type: dict[str, list[str]] = {}

    for root, _, files in os.walk(split_dir):
        if "traj_data.json" not in files:
            continue
        # Filter like MemSkill: skip movable and Sliced
        if "movable" in root or "Sliced" in root:
            continue

        traj_path = os.path.join(root, "traj_data.json")
        game_path = os.path.join(root, "game.tw-pddl")

        if not os.path.exists(game_path):
            continue

        try:
            with open(traj_path) as f:
                traj_data = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue

        task_type = traj_data.get("task_type", "")
        if task_type not in TASK_TYPES:
            continue

        # Check solvability
        try:
            with open(game_path) as f:
                gamedata = json.load(f)
            if not gamedata.get("solvable", False):
                continue
        except (OSError, json.JSONDecodeError):
            continue

        game_files_by_type.setdefault(task_type, []).append(game_path)

    return game_files_by_type


def _run_single_game(task_type: str, gamefile: str, max_steps: int = 150) -> dict:
    """Replay one ALFWorld episode using expert planner. Returns trajectory record."""
    request_infos = textworld.EnvInfos(
        feedback=True,
        description=True,
        inventory=True,
        command_templates=True,
        intermediate_reward=True,
        location=True,
        objective=True,
        admissible_commands=True,
        extras=["gamefile", "expert_plan"],
    )
    wrappers = [
        AlfredDemangler(),
        AlfredInfos,
        AlfredExpert(expert_type=AlfredExpertType.PLANNER),
    ]
    env_id = textworld.gym.register_games(
        [gamefile],
        request_infos,
        batch_size=1,
        auto_reset=False,
        max_episode_steps=max_steps,
        asynchronous=False,
        name=f"alfworld-replay-{uuid.uuid4().hex}",
        wrappers=wrappers,
    )
    env = textworld.gym.make(env_id)

    try:
        # Reset with timeout
        reset_result: dict = {}

        def _do_reset() -> None:
            try:
                reset_result["value"] = env.reset()
            except Exception as exc:
                reset_result["error"] = exc

        reset_thread = threading.Thread(target=_do_reset, daemon=True)
        reset_thread.start()
        reset_thread.join(120.0)
        if reset_thread.is_alive():
            return {"error": "reset timeout", "gamefile": gamefile, "task_type": task_type}
        if "error" in reset_result:
            return {"error": str(reset_result["error"]), "gamefile": gamefile, "task_type": task_type}

        obs_batch, info_batch = reset_result["value"]
        obs = str(_ensure_scalar(obs_batch) or "")
        info = _ensure_scalar(info_batch) or {}
        if not isinstance(info, dict):
            info = {}

        plan = _extract_plan(info)
        if not plan:
            return {"error": "no expert plan", "gamefile": gamefile, "task_type": task_type}

        steps: list[dict] = [{"step": 0, "action": None, "observation": obs, "reward": 0.0, "done": False}]
        total_reward = 0.0

        for i, action in enumerate(plan):
            action = str(action)
            result = env.step([action])
            obs_raw, score_raw, done_raw, info_raw = result
            step_obs = str(_ensure_scalar(obs_raw) or "").strip()
            score = _ensure_scalar(score_raw)
            score = float(score) if isinstance(score, (int, float)) else 0.0
            done = bool(_ensure_scalar(done_raw))
            total_reward += score

            steps.append(
                {
                    "step": i + 1,
                    "action": action,
                    "observation": step_obs,
                    "reward": score,
                    "done": done,
                }
            )

            if done or (i + 1) >= max_steps:
                break

        first_obs = str(steps[0].get("observation", ""))
        objective = _extract_objective(first_obs)

        return {
            "gamefile": gamefile,
            "task_type": task_type,
            "objective": objective,
            "first_observation": first_obs,
            "total_reward": total_reward,
            "num_steps": len(steps) - 1,
            "trajectory": _build_trajectory_text(steps),
        }
    except Exception as exc:
        return {"error": str(exc), "gamefile": gamefile, "task_type": task_type}
    finally:
        try:
            env.close()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Collect ALFWorld expert trajectories")
    parser.add_argument("--data-dir", type=str, required=True, help="Path to alfworld data dir (contains json_2.1.1/)")
    parser.add_argument("--output", type=str, required=True, help="Output directory for trajectory JSONs")
    parser.add_argument("--split", type=str, default="train", choices=["train", "valid_unseen", "valid_seen"])
    parser.add_argument("--workers", type=int, default=4, help="Number of parallel workers")
    parser.add_argument("--max-steps", type=int, default=150, help="Max steps per episode")
    parser.add_argument("--save-every", type=int, default=20, help="Save progress every N games")
    args = parser.parse_args()

    os.makedirs(args.output, exist_ok=True)
    output_file = os.path.join(args.output, f"{args.split}.json")

    game_files_by_type = _collect_game_files(args.data_dir, args.split)
    total_games = sum(len(files) for files in game_files_by_type.values())
    print(f"Found {total_games} games across {len(game_files_by_type)} task types")
    for task_type, files in sorted(game_files_by_type.items()):
        print(f"  {task_type}: {len(files)} games")

    # Flatten to list of (task_type, gamefile) pairs
    all_games = [(tt, gf) for tt, games in game_files_by_type.items() for gf in games]

    results: dict[str, dict] = {}
    completed = 0
    success = 0
    failed = 0

    def _save():
        tmp = f"{output_file}.tmp"
        with open(tmp, "w") as f:
            json.dump(results, f, indent=2)
        os.replace(tmp, output_file)

    progress = tqdm(total=total_games, desc=f"Collecting {args.split}")
    try:
        with ProcessPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(_run_single_game, tt, gf, args.max_steps): (tt, gf) for tt, gf in all_games}
            for future in as_completed(futures):
                tt, gf = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {"error": str(exc), "gamefile": gf, "task_type": tt}

                results[gf] = result
                completed += 1
                if "error" in result:
                    failed += 1
                else:
                    reward = result.get("total_reward", 0)
                    if reward >= 1.0:
                        success += 1
                    else:
                        failed += 1

                progress.set_postfix(ok=success, fail=failed)
                progress.update(1)

                if args.save_every > 0 and completed % args.save_every == 0:
                    _save()
    finally:
        _save()
        progress.close()

    print(f"\nDone: {success} success, {failed} failed out of {total_games}")
    print(f"Saved to {output_file}")


if __name__ == "__main__":
    main()
