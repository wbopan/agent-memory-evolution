"""AgentBoard benchmark — ScienceWorld, BabyAI, PDDL interactive environments.

Unified benchmark for multi-turn goal-oriented tasks from AgentBoard.
Each environment is selected via the `category` parameter.
"""

from __future__ import annotations

import random
import re

import litellm

from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

try:
    from scienceworld import ScienceWorldEnv
except ImportError:
    ScienceWorldEnv = None  # type: ignore[assignment,misc]

AVAILABLE_CATEGORIES = ["scienceworld", "babyai", "pddl"]

# BabyAI levels used by AgentBoard (subset of 40)
BABYAI_LEVELS = [
    "BabyAI-GoToRedBallGrey-v0",
    "BabyAI-GoToRedBall-v0",
    "BabyAI-GoToObjS6-v0",
    "BabyAI-GoToLocalS8N7-v0",
    "BabyAI-GoToDoor-v0",
    "BabyAI-Open-v0",
    "BabyAI-OpenRedDoor-v0",
    "BabyAI-Pickup-v0",
    "BabyAI-UnblockPickup-v0",
    "BabyAI-Unlock-v0",
]

# PDDL domains from AgentBoard
PDDL_ENVS = {
    "blocks": ("PDDLEnvBlocks-v0", 20),
    "gripper": ("PDDLEnvGripper-v0", 20),
    "barman": ("PDDLEnvBarman-v0", 20),
    "tyreworld": ("PDDLEnvTyreworld-v0", 10),
}


# -- Per-environment data loaders --


def _load_scienceworld(num_train: int | None, num_val: int | None, seed: int) -> tuple[list[DataItem], list[DataItem]]:
    if ScienceWorldEnv is None:
        raise ImportError("scienceworld package required. Install with: pip install -e '.[agentboard]'")
    env = ScienceWorldEnv("", envStepLimit=100)
    try:
        task_names = env.get_task_names()
        train_items: list[DataItem] = []
        val_items: list[DataItem] = []
        for task_name in task_names:
            train_vars = env.get_variations_train()
            dev_vars = env.get_variations_dev()
            env.load(task_name, variationIdx=train_vars[0] if train_vars else 0)
            task_desc = env.get_task_description()
            for var in train_vars:
                train_items.append(
                    DataItem(
                        raw_text=f"Task: {task_name}\nDescription: {task_desc}\nVariation: {var}",
                        question="",
                        expected_answer="",
                        metadata={"env": "scienceworld", "task_name": task_name, "variation_idx": var},
                    )
                )
            for var in dev_vars:
                val_items.append(
                    DataItem(
                        raw_text="",
                        question=task_desc,
                        expected_answer="Task completed successfully",
                        metadata={"env": "scienceworld", "task_name": task_name, "variation_idx": var},
                    )
                )
    finally:
        env.close()
    rng = random.Random(seed)
    rng.shuffle(train_items)
    rng.shuffle(val_items)
    if num_train is not None:
        train_items = train_items[:num_train]
    if num_val is not None:
        val_items = val_items[:num_val]
    return train_items, val_items


def _load_babyai(num_train: int | None, num_val: int | None, seed: int) -> tuple[list[DataItem], list[DataItem]]:
    import gymnasium as gym
    import minigrid  # noqa: F401

    train_items: list[DataItem] = []
    val_items: list[DataItem] = []
    rng = random.Random(seed)
    seeds_per_level = 20
    for level_id in BABYAI_LEVELS:
        env = gym.make(level_id)
        for s in range(seeds_per_level):
            obs, _ = env.reset(seed=s)
            mission = obs["mission"]
            if s < seeds_per_level // 2:
                train_items.append(
                    DataItem(
                        raw_text=f"Level: {level_id}\nMission: {mission}\nSeed: {s}",
                        question="",
                        expected_answer="",
                        metadata={"env": "babyai", "env_id": level_id, "seed": s},
                    )
                )
            else:
                val_items.append(
                    DataItem(
                        raw_text="",
                        question=mission,
                        expected_answer="Task completed successfully",
                        metadata={"env": "babyai", "env_id": level_id, "seed": s},
                    )
                )
        env.close()
    rng.shuffle(train_items)
    rng.shuffle(val_items)
    if num_train is not None:
        train_items = train_items[:num_train]
    if num_val is not None:
        val_items = val_items[:num_val]
    return train_items, val_items


def _load_pddl(num_train: int | None, num_val: int | None, seed: int) -> tuple[list[DataItem], list[DataItem]]:
    train_items: list[DataItem] = []
    val_items: list[DataItem] = []
    for domain, (env_id, num_problems) in PDDL_ENVS.items():
        train_count = num_problems // 2
        for idx in range(num_problems):
            if idx < train_count:
                train_items.append(
                    DataItem(
                        raw_text=f"Domain: {domain}\nProblem: {idx}",
                        question="",
                        expected_answer="",
                        metadata={"env": "pddl", "domain": domain, "env_id": env_id, "problem_idx": idx},
                    )
                )
            else:
                val_items.append(
                    DataItem(
                        raw_text="",
                        question=f"Solve {domain} problem {idx}",
                        expected_answer="All goals satisfied",
                        metadata={"env": "pddl", "domain": domain, "env_id": env_id, "problem_idx": idx},
                    )
                )
    rng = random.Random(seed)
    rng.shuffle(train_items)
    rng.shuffle(val_items)
    if num_train is not None:
        train_items = train_items[:num_train]
    if num_val is not None:
        val_items = val_items[:num_val]
    return train_items, val_items


# -- Action selection (shared across envs) --


def _parse_action_response(response: str, valid_actions: list[str]) -> str:
    text = (response or "").strip()
    if not text:
        return valid_actions[0] if valid_actions else "look"
    text = re.sub(r"^\s*action\s*[:=\-]\s*", "", text, flags=re.IGNORECASE)
    line = text.splitlines()[0].strip().strip('"').strip("'").strip("`")
    lowered = text.lower()
    if valid_actions:
        for cmd in valid_actions:
            if line.lower() == str(cmd).lower():
                return cmd
        for cmd in valid_actions:
            if str(cmd).lower() in lowered:
                return cmd
        return valid_actions[0]
    return line if line else "look"


def _select_action(
    env_type: str,
    objective: str,
    tips: str,
    trajectory_text: str,
    valid_actions: list[str],
    task_model: str,
    always_on_knowledge: str = "",
) -> str:
    env_desc = {
        "scienceworld": "You are controlling a text-based ScienceWorld environment to perform science experiments.",
        "babyai": (
            "You are controlling a BabyAI grid-world environment."
            " Navigate and interact with objects to complete the mission."
        ),
        "pddl": "You are solving a PDDL planning problem. Choose actions to satisfy all goal conditions.",
    }.get(env_type, "You are controlling a text-based environment.")

    lines = [
        env_desc,
        "Choose the NEXT action as ONE text command.",
        "Output ONLY the command, no extra text.",
        "You MUST choose from the valid actions list and copy it EXACTLY.",
    ]
    if objective:
        lines += ["", "Goal:", objective.strip()]
    if always_on_knowledge and always_on_knowledge.strip():
        lines += ["", "Always-on knowledge:", always_on_knowledge.strip()]
    if tips and tips.strip():
        lines += ["", "Retrieved tips:", tips.strip()]
    lines += ["", "Interaction history:"]
    if trajectory_text and trajectory_text.strip():
        traj_lines = trajectory_text.strip().splitlines()
        if len(traj_lines) > 40:
            traj_lines = ["(earlier history truncated)", "..."] + traj_lines[-40:]
        lines.append("\n".join(traj_lines))
    else:
        lines.append("(empty)")
    if valid_actions:
        lines += ["", "Valid actions (choose exactly ONE):"]
        for cmd in valid_actions:
            lines.append(f"- {cmd}")
    prompt = "\n".join(lines)
    resp = litellm.completion(
        model=task_model, messages=[{"role": "user", "content": prompt}], max_tokens=64, caching=True
    )
    raw = resp.choices[0].message.content.strip()
    return _parse_action_response(raw, valid_actions)


# -- Episode runner (module-level for ProcessPoolExecutor pickling) --


def _run_episode(
    env_type: str,
    env_config: dict,
    objective: str,
    tips: str,
    task_model: str,
    max_steps: int,
    always_on_knowledge: str = "",
) -> tuple[str, float]:
    if env_type == "scienceworld":
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        wrapper = ScienceWorldWrapper(env_config["task_name"], env_config["variation_idx"], step_limit=max_steps)
    elif env_type == "babyai":
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        wrapper = BabyAIWrapper(env_config["env_id"], seed=env_config["seed"], max_steps=max_steps)
    elif env_type == "pddl":
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        wrapper = PDDLWrapper(env_config["env_id"], env_config["problem_idx"])
    else:
        msg = f"Unknown env_type: {env_type}"
        raise ValueError(msg)

    try:
        obs = wrapper.reset()
        trajectory_lines = [obs.strip()]
        progress = 0.0
        done = False
        for _step in range(max_steps):
            valid_actions = wrapper.get_valid_actions()
            if not valid_actions:
                break
            action = _select_action(
                env_type, objective, tips, "\n".join(trajectory_lines), valid_actions, task_model, always_on_knowledge
            )
            trajectory_lines.append(f"ACTION: {action}")
            obs, progress, done = wrapper.step(action)
            trajectory_lines.append(f"OBSERVATION: {obs.strip()}")
            if done:
                break
        return "\n".join(trajectory_lines), progress
    finally:
        wrapper.close()


# -- Val scorer --


class AgentBoardValScorer:
    def __init__(self, max_steps: int = 30, max_workers: int = 10, episode_timeout: float = 300.0) -> None:
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
    ) -> list[tuple[str, float]]:
        import concurrent.futures

        workers = min(self.max_workers, len(items)) if items else 1
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(
                    _run_episode,
                    item.metadata["env"],
                    item.metadata,
                    item.question,
                    tips,
                    task_model,
                    self.max_steps,
                    always_on_knowledge,
                )
                for item, tips in zip(items, retrieved, strict=True)
            ]
            results: list[tuple[str, float]] = []
            for f in futures:
                try:
                    results.append(f.result(timeout=self.episode_timeout))
                except Exception as exc:
                    results.append((f"Episode failed: {exc}", 0.0))
        return results


# -- Dataset registration --


@register_dataset("agentboard")
def load_agentboard(
    *,
    num_train: int | None = None,
    num_val: int | None = None,
    category: str | None = None,
    seed: int = 42,
) -> Dataset:
    if category is None or category not in AVAILABLE_CATEGORIES:
        msg = f"category must be one of {AVAILABLE_CATEGORIES}, got {category!r}"
        raise ValueError(msg)

    loaders = {
        "scienceworld": _load_scienceworld,
        "babyai": _load_babyai,
        "pddl": _load_pddl,
    }
    train, val = loaders[category](num_train, num_val, seed)

    val_scorer = AgentBoardValScorer()
    return Dataset(
        train=train,
        val=val,
        test=[],
        scorer=ExactMatchScorer(),
        val_scorer=val_scorer,
        available_categories=AVAILABLE_CATEGORIES,
    )
