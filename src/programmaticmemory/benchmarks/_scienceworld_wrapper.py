"""ScienceWorld environment wrapper — text-based science experiment interface."""

from __future__ import annotations

try:
    from scienceworld import ScienceWorldEnv
except ImportError:
    ScienceWorldEnv = None  # type: ignore[assignment,misc]


class ScienceWorldWrapper:
    """Text-based wrapper for ScienceWorld environments.

    ScienceWorld natively uses text for observations and actions,
    so this wrapper is thin — mainly normalizing the interface and
    tracking monotonic progress rate.
    """

    def __init__(self, task_name: str, variation_idx: int, step_limit: int = 100) -> None:
        self._env = ScienceWorldEnv("", envStepLimit=step_limit)
        self._task_name = task_name
        self._variation_idx = variation_idx
        self._max_score = 0.0

    def reset(self) -> str:
        self._max_score = 0.0
        self._env.load(self._task_name, variationIdx=self._variation_idx, simplificationStr="")
        obs, _info = self._env.reset()
        task_desc = self._env.get_task_description()
        return f"Task: {task_desc}\n\n{obs}"

    def step(self, action: str) -> tuple[str, float, bool]:
        obs, _reward, done, info = self._env.step(action)
        score = info.get("score", 0) / 100.0
        self._max_score = max(self._max_score, score)
        return str(obs), self._max_score, bool(done)

    def get_valid_actions(self) -> list[str]:
        return self._env.get_valid_action_object_combinations()

    def close(self) -> None:
        try:
            self._env.close()
        except Exception:
            pass
