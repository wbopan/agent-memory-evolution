"""PDDL planning environment wrapper — text-based state/action interface."""

from __future__ import annotations


def _state_to_text(state: object) -> str:
    """Convert a pddlgym State to text description."""
    literals = sorted(str(lit) for lit in state.literals)
    goals = sorted(str(g) for g in state.goal)
    lines = ["Current state:"]
    for lit in literals:
        lines.append(f"  {lit}")
    lines.append("")
    lines.append("Goal:")
    for g in goals:
        lines.append(f"  {g}")
    return "\n".join(lines)


def _compute_progress(state: object) -> float:
    """Compute goal completion rate: satisfied / total goal literals."""
    goal_lits = {str(g) for g in state.goal}
    current_lits = {str(lit) for lit in state.literals}
    if not goal_lits:
        return 1.0
    satisfied = goal_lits & current_lits
    return len(satisfied) / len(goal_lits)


class PDDLWrapper:
    """Text-based wrapper for PDDLGym environments."""

    def __init__(self, env_id: str, problem_idx: int) -> None:
        import pddlgym

        self._env = pddlgym.make(env_id)
        self._problem_idx = problem_idx
        self._state: object | None = None
        self._max_progress = 0.0

    def reset(self) -> str:
        self._max_progress = 0.0
        self._env.fix_problem_index(self._problem_idx)
        self._state, _info = self._env.reset()
        return _state_to_text(self._state)

    def step(self, action: str) -> tuple[str, float, bool]:
        # Find matching action from valid actions
        valid = self._env.action_space.all_ground_literals(self._state)
        matched = None
        for a in valid:
            if str(a) == action:
                matched = a
                break
        if matched is None and valid:
            # Fuzzy match: find closest
            action_lower = action.lower()
            for a in valid:
                if str(a).lower() == action_lower:
                    matched = a
                    break
            if matched is None:
                matched = next(iter(valid))  # fallback

        if matched is None:
            return "No valid actions available.", self._max_progress, True

        self._state, _reward, done, _info = self._env.step(matched)
        progress = _compute_progress(self._state)
        self._max_progress = max(self._max_progress, progress)
        obs = _state_to_text(self._state)
        return obs, self._max_progress, bool(done)

    def get_valid_actions(self) -> list[str]:
        if self._state is None:
            return []
        valid = self._env.action_space.all_ground_literals(self._state)
        return [str(a) for a in valid]

    def close(self) -> None:
        try:
            self._env.close()
        except Exception:
            pass
