"""BabyAI environment wrapper — grid-to-text conversion for LLM agents.

Observation conversion and text action space ported from
AgentBoard (hkust-nlp/AgentBoard) babyai_env.py.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

try:
    import gymnasium as gym
    import minigrid
except ImportError:
    gym = None  # type: ignore[assignment]
    minigrid = None  # type: ignore[assignment]

# Minigrid object type indices
OBJECT_NAMES = {
    0: "unseen",
    1: "empty",
    2: "wall",
    3: "floor",
    4: "door",
    5: "key",
    6: "ball",
    7: "box",
    8: "goal",
    9: "lava",
    10: "agent",
}

# Minigrid color indices
COLOR_NAMES = {0: "red", 1: "green", 2: "blue", 3: "purple", 4: "yellow", 5: "grey"}

# Door state indices
DOOR_STATES = {0: "open", 1: "closed", 2: "locked"}

# Direction names (agent facing)
DIR_NAMES = {0: "right", 1: "down", 2: "left", 3: "up"}

# Text action to gymnasium int action mapping
ACTION_MAP = {
    "turn left": 0,
    "turn right": 1,
    "move forward": 2,
    "pickup": 3,
    "drop": 4,
    "toggle": 5,
    "done": 6,
}


def grid_to_text(grid: np.ndarray, direction: int, carrying: tuple[str, str] | None) -> str:
    """Convert a 7x7x3 partial observation grid to natural language.

    The agent is at position (3, 6) in the grid (bottom-center),
    facing upward in grid coordinates. Objects are described
    relative to the agent's position and facing direction.

    Args:
        grid: Shape (7, 7, 3) with (object_idx, color_idx, state) per cell.
        direction: Agent facing direction (0=right, 1=down, 2=left, 3=up).
        carrying: (object_type, color) if carrying something, else None.
    """
    objects: list[str] = []
    # Agent is at grid position (3, 6), facing "up" in grid coords (toward row 0)
    agent_col, agent_row = 3, 6

    # Track objects by type for numbering
    obj_counts: dict[str, int] = defaultdict(int)

    for col in range(7):
        for row in range(7):
            obj_type = int(grid[col, row, 0])
            if obj_type in (0, 1, 2, 3, 10):  # unseen, empty, wall, floor, agent
                continue

            color_idx = int(grid[col, row, 1])
            state = int(grid[col, row, 2])
            obj_name = OBJECT_NAMES.get(obj_type, f"object_{obj_type}")
            color = COLOR_NAMES.get(color_idx, "unknown")

            # Relative position in grid coords (agent faces toward row 0)
            dc = col - agent_col  # positive = right of agent
            dr = agent_row - row  # positive = in front of agent

            obj_counts[f"{color}_{obj_name}"] += 1
            obj_id = obj_counts[f"{color}_{obj_name}"]

            # Build description
            parts = []
            if obj_type == 4:  # door
                door_state = DOOR_STATES.get(state, "unknown")
                parts.append(f"a {color} {door_state} door {obj_id}")
            else:
                parts.append(f"a {color} {obj_name} {obj_id}")

            # Position description
            pos_parts = []
            if dr > 0:
                pos_parts.append(f"{dr} step{'s' if dr > 1 else ''} in front of you")
            elif dr < 0:
                pos_parts.append(f"{-dr} step{'s' if -dr > 1 else ''} behind you")

            if dc > 0:
                pos_parts.append(f"{dc} step{'s' if dc > 1 else ''} to your right")
            elif dc < 0:
                pos_parts.append(f"{-dc} step{'s' if -dc > 1 else ''} to your left")

            if dr == 0 and dc == 0:
                pos_parts.append("at your position")

            parts.append(" and ".join(pos_parts) if pos_parts else "nearby")
            objects.append(f"There is {parts[0]} {parts[1]}.")

    lines = []
    if objects:
        lines.append("You can see: " + " ".join(objects))
    else:
        lines.append("You see nothing notable around you.")

    lines.append(f"You are facing {DIR_NAMES.get(direction, 'unknown')}.")

    if carrying:
        lines.append(f"You are carrying a {carrying[1]} {carrying[0]}.")
    else:
        lines.append("You are not carrying anything.")

    return " ".join(lines)


class BabyAIWrapper:
    """Text-based wrapper for BabyAI gymnasium environments."""

    def __init__(self, env_id: str, seed: int = 42, max_steps: int = 64) -> None:
        self._env = gym.make(env_id, max_steps=max_steps)
        self._seed = seed
        self._obs: dict | None = None
        self._mission = ""
        self._carrying: tuple[str, str] | None = None
        self._done = False

    def reset(self) -> str:
        self._obs, _info = self._env.reset(seed=self._seed)
        self._mission = self._obs["mission"]
        self._carrying = None
        self._done = False
        text = grid_to_text(self._obs["image"], self._obs["direction"], self._carrying)
        return f"Mission: {self._mission}\n\n{text}"

    def step(self, action: str) -> tuple[str, float, bool]:
        action_lower = action.strip().lower()

        if action_lower in ACTION_MAP:
            int_action = ACTION_MAP[action_lower]
            self._obs, reward, terminated, truncated, _info = self._env.step(int_action)
            self._done = terminated or truncated

            # Track carrying state from observation
            if action_lower == "drop":
                self._carrying = None

            progress = 1.0 if (terminated and reward > 0) else 0.0
            text = grid_to_text(self._obs["image"], self._obs["direction"], self._carrying)
            return text, progress, self._done
        else:
            return (
                f"Invalid action: {action}. Valid actions: {', '.join(self.get_valid_actions())}",
                0.0,
                self._done,
            )

    def get_valid_actions(self) -> list[str]:
        actions = ["turn left", "turn right"]
        if self._obs is not None:
            # Check if forward cell is passable
            front_cell = self._obs["image"][3, 5]  # cell directly in front
            obj_type = int(front_cell[0])
            if obj_type in (1, 3, 8):  # empty, floor, goal
                actions.append("move forward")
            if obj_type == 4:  # door
                actions.append("toggle")
                if int(front_cell[2]) == 0:  # open door
                    actions.append("move forward")

            # Check if there's a pickupable object in front
            if obj_type in (5, 6, 7):  # key, ball, box
                actions.append("pickup")

            if self._carrying is not None:
                actions.append("drop")
        return actions

    def close(self) -> None:
        try:
            self._env.close()
        except Exception:
            pass
