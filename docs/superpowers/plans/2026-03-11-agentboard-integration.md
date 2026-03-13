# AgentBoard Benchmark Integration Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate ScienceWorld, BabyAI, and PDDL environments as a unified `agentboard` benchmark dataset with interactive episode evaluation.

**Architecture:** A single `@register_dataset("agentboard")` entry point dispatches to per-environment wrappers via `--category`. Each wrapper (ScienceWorld, BabyAI, PDDL) implements a common text-based interface (`reset`, `step`, `get_valid_actions`, `close`). An `AgentBoardValScorer` runs LLM-driven episodes through these wrappers in parallel via `ProcessPoolExecutor`, following the existing `ALFWorldValScorer` pattern.

**Tech Stack:** scienceworld (Java/py4j), minigrid (gymnasium), pddlgym, litellm, ProcessPoolExecutor

**Spec:** `docs/superpowers/specs/2026-03-11-agentboard-integration-design.md`

---

## Chunk 1: ScienceWorld Wrapper + Tests

### Task 1: ScienceWorld wrapper

**Files:**
- Create: `src/programmaticmemory/benchmarks/_scienceworld_wrapper.py`
- Test: `tests/evolution/test_agentboard.py`

The ScienceWorld wrapper is the simplest — the underlying API already uses text for observations and actions.

- [ ] **Step 1: Write failing tests for ScienceWorldWrapper**

In `tests/evolution/test_agentboard.py`:

```python
"""Tests for AgentBoard benchmark (ScienceWorld, BabyAI, PDDL)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestScienceWorldWrapper:
    def test_reset_returns_text_observation(self):
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        mock_env = MagicMock()
        mock_env.reset.return_value = ("You are in the kitchen.", {})
        mock_env.get_task_description.return_value = "Boil water."

        with patch(
            "programmaticmemory.benchmarks._scienceworld_wrapper.ScienceWorldEnv",
            return_value=mock_env,
        ):
            wrapper = ScienceWorldWrapper("boil", 0)
            obs = wrapper.reset()

        assert isinstance(obs, str)
        assert "kitchen" in obs.lower() or "Boil" in obs

    def test_step_returns_obs_progress_done(self):
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        mock_env = MagicMock()
        mock_env.reset.return_value = ("Kitchen.", {})
        mock_env.get_task_description.return_value = "Boil water."
        mock_env.step.return_value = ("The water is now hot.", 1, False, {"score": 50})

        with patch(
            "programmaticmemory.benchmarks._scienceworld_wrapper.ScienceWorldEnv",
            return_value=mock_env,
        ):
            wrapper = ScienceWorldWrapper("boil", 0)
            wrapper.reset()
            obs, progress, done = wrapper.step("pick up the thermometer")

        assert isinstance(obs, str)
        assert progress == 0.5  # score 50 / 100
        assert done is False

    def test_get_valid_actions_returns_list(self):
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        mock_env = MagicMock()
        mock_env.reset.return_value = ("Kitchen.", {})
        mock_env.get_task_description.return_value = "Boil water."
        mock_env.get_valid_action_object_combinations.return_value = [
            "pick up thermometer",
            "open door",
            "look around",
        ]

        with patch(
            "programmaticmemory.benchmarks._scienceworld_wrapper.ScienceWorldEnv",
            return_value=mock_env,
        ):
            wrapper = ScienceWorldWrapper("boil", 0)
            wrapper.reset()
            actions = wrapper.get_valid_actions()

        assert isinstance(actions, list)
        assert len(actions) == 3
        assert "pick up thermometer" in actions

    def test_close_cleans_up(self):
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        mock_env = MagicMock()
        mock_env.reset.return_value = ("Kitchen.", {})
        mock_env.get_task_description.return_value = "Boil water."

        with patch(
            "programmaticmemory.benchmarks._scienceworld_wrapper.ScienceWorldEnv",
            return_value=mock_env,
        ):
            wrapper = ScienceWorldWrapper("boil", 0)
            wrapper.reset()
            wrapper.close()

        mock_env.close.assert_called_once()

    def test_progress_rate_monotonic(self):
        """Progress rate should track max score seen (monotonic)."""
        from programmaticmemory.benchmarks._scienceworld_wrapper import ScienceWorldWrapper

        mock_env = MagicMock()
        mock_env.reset.return_value = ("Kitchen.", {})
        mock_env.get_task_description.return_value = "Boil water."
        mock_env.step.side_effect = [
            ("Step 1.", 0, False, {"score": 30}),
            ("Step 2.", 0, False, {"score": 10}),  # score drops
            ("Step 3.", 0, False, {"score": 50}),
        ]

        with patch(
            "programmaticmemory.benchmarks._scienceworld_wrapper.ScienceWorldEnv",
            return_value=mock_env,
        ):
            wrapper = ScienceWorldWrapper("boil", 0)
            wrapper.reset()
            _, p1, _ = wrapper.step("a1")
            _, p2, _ = wrapper.step("a2")
            _, p3, _ = wrapper.step("a3")

        assert p1 == 0.3
        assert p2 == 0.3  # stays at max
        assert p3 == 0.5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestScienceWorldWrapper -v`
Expected: FAIL with `ModuleNotFoundError` or `ImportError`

- [ ] **Step 3: Implement ScienceWorldWrapper**

Create `src/programmaticmemory/benchmarks/_scienceworld_wrapper.py`:

```python
"""ScienceWorld environment wrapper — text-based science experiment interface."""

from __future__ import annotations


class ScienceWorldWrapper:
    """Text-based wrapper for ScienceWorld environments.

    ScienceWorld natively uses text for observations and actions,
    so this wrapper is thin — mainly normalizing the interface and
    tracking monotonic progress rate.
    """

    def __init__(self, task_name: str, variation_idx: int, step_limit: int = 100) -> None:
        from scienceworld import ScienceWorldEnv

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestScienceWorldWrapper -v`
Expected: All 5 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/benchmarks/_scienceworld_wrapper.py tests/evolution/test_agentboard.py
git commit -m "feat: add ScienceWorld environment wrapper for agentboard benchmark"
```

---

## Chunk 2: BabyAI Wrapper + Tests

### Task 2: BabyAI grid-to-text wrapper

**Files:**
- Create: `src/programmaticmemory/benchmarks/_babyai_wrapper.py`
- Modify: `tests/evolution/test_agentboard.py`

This is the most complex wrapper — it ports AgentBoard's grid-to-text observation conversion and maps text actions to gymnasium integer actions. Key components:
1. Grid-to-text: parse 7x7x3 partial view, compute object positions relative to agent, generate natural language
2. Text-to-action: map high-level text commands ("go to red ball 1", "turn left") to sequences of int actions
3. BFS pathfinding for "go to" commands

- [ ] **Step 1: Write failing tests for BabyAIWrapper**

Append to `tests/evolution/test_agentboard.py`:

```python
import numpy as np


class TestBabyAIGridToText:
    """Test the grid-to-text observation conversion."""

    def test_empty_room_description(self):
        from programmaticmemory.benchmarks._babyai_wrapper import grid_to_text

        # 7x7x3 grid: all empty (1,0,0) except walls (2,5,0) on edges
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1  # empty
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        text = grid_to_text(grid, direction=0, carrying=None)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_object_in_view(self):
        from programmaticmemory.benchmarks._babyai_wrapper import grid_to_text

        # Place a red ball (6, 0, 0) at position (3, 5) — 2 steps ahead of agent
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1  # empty
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        grid[3, 5, 0] = 6  # ball
        grid[3, 5, 1] = 0  # red
        grid[3, 5, 2] = 0
        text = grid_to_text(grid, direction=0, carrying=None)
        assert "ball" in text.lower()
        assert "red" in text.lower()

    def test_carrying_object_mentioned(self):
        from programmaticmemory.benchmarks._babyai_wrapper import grid_to_text

        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        text = grid_to_text(grid, direction=0, carrying=("key", "blue"))
        assert "carrying" in text.lower()
        assert "blue" in text.lower()
        assert "key" in text.lower()


class TestBabyAIWrapper:
    def test_reset_returns_text(self):
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        mock_env = MagicMock()
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        mock_env.reset.return_value = (
            {"image": grid, "direction": 0, "mission": "go to the red ball"},
            {},
        )

        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym.make",
            return_value=mock_env,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            obs = wrapper.reset()

        assert isinstance(obs, str)
        assert "red ball" in obs.lower()

    def test_step_turn_left(self):
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        mock_env = MagicMock()
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        mock_env.reset.return_value = (
            {"image": grid, "direction": 0, "mission": "go to the red ball"},
            {},
        )
        mock_env.step.return_value = (
            {"image": grid, "direction": 3, "mission": "go to the red ball"},
            0.0,
            False,
            False,
            {},
        )

        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym.make",
            return_value=mock_env,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            wrapper.reset()
            obs, progress, done = wrapper.step("turn left")

        assert isinstance(obs, str)
        mock_env.step.assert_called_with(0)  # 0 = turn_left

    def test_get_valid_actions_always_has_turn(self):
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        mock_env = MagicMock()
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        mock_env.reset.return_value = (
            {"image": grid, "direction": 0, "mission": "go to the red ball"},
            {},
        )

        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym.make",
            return_value=mock_env,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            wrapper.reset()
            actions = wrapper.get_valid_actions()

        assert "turn left" in actions
        assert "turn right" in actions

    def test_close(self):
        from programmaticmemory.benchmarks._babyai_wrapper import BabyAIWrapper

        mock_env = MagicMock()
        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        mock_env.reset.return_value = (
            {"image": grid, "direction": 0, "mission": "go to the red ball"},
            {},
        )

        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym.make",
            return_value=mock_env,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            wrapper.reset()
            wrapper.close()

        mock_env.close.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestBabyAIGridToText tests/evolution/test_agentboard.py::TestBabyAIWrapper -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement BabyAIWrapper**

Create `src/programmaticmemory/benchmarks/_babyai_wrapper.py`. This is the largest file — it ports AgentBoard's grid-to-text conversion. Key sections:

1. **Object/color/state constants** — mapping indices to names (from minigrid)
2. **`grid_to_text(grid, direction, carrying)`** — converts 7x7x3 numpy grid + direction to natural language description
3. **`BabyAIWrapper`** — gymnasium env wrapper with text action mapping

```python
"""BabyAI environment wrapper — grid-to-text conversion for LLM agents.

Observation conversion and text action space ported from
AgentBoard (hkust-nlp/AgentBoard) babyai_env.py.
"""

from __future__ import annotations

from collections import defaultdict

import numpy as np

# Minigrid object type indices
OBJECT_NAMES = {0: "unseen", 1: "empty", 2: "wall", 3: "floor", 4: "door", 5: "key", 6: "ball", 7: "box", 8: "goal", 9: "lava", 10: "agent"}

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
        import gymnasium as gym
        import minigrid  # noqa: F401 — registers environments

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
            # (simplified: if pickup succeeded, we don't get explicit carry info from obs)
            if action_lower == "pickup":
                # Check if agent picked something up by looking at the cell ahead
                pass  # carrying state tracked implicitly
            elif action_lower == "drop":
                self._carrying = None

            progress = 1.0 if (terminated and reward > 0) else 0.0
            text = grid_to_text(self._obs["image"], self._obs["direction"], self._carrying)
            return text, progress, self._done
        else:
            return f"Invalid action: {action}. Valid actions: {', '.join(self.get_valid_actions())}", 0.0, self._done

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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestBabyAIGridToText tests/evolution/test_agentboard.py::TestBabyAIWrapper -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/benchmarks/_babyai_wrapper.py tests/evolution/test_agentboard.py
git commit -m "feat: add BabyAI grid-to-text wrapper for agentboard benchmark"
```

---

## Chunk 3: PDDL Wrapper + Tests

### Task 3: PDDL planning wrapper

**Files:**
- Create: `src/programmaticmemory/benchmarks/_pddl_wrapper.py`
- Modify: `tests/evolution/test_agentboard.py`

PDDL wrapper renders structured PDDL states as text and maps text actions back to grounded operators.

- [ ] **Step 1: Write failing tests for PDDLWrapper**

Append to `tests/evolution/test_agentboard.py`:

```python
class TestPDDLWrapper:
    def test_reset_returns_text(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        # pddlgym returns a State object with predicates and goal
        mock_state = MagicMock()
        mock_state.literals = frozenset({"on(a,b)", "clear(a)", "ontable(b)"})
        mock_state.goal = frozenset({"on(b,a)", "ontable(a)"})
        mock_env.reset.return_value = (mock_state, {})
        mock_env.action_space.all_ground_literals.return_value = [
            "pick-up(a)", "put-down(a)", "stack(a,b)",
        ]

        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym.make",
            return_value=mock_env,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            obs = wrapper.reset()

        assert isinstance(obs, str)
        assert len(obs) > 0

    def test_progress_rate_partial_goal(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        mock_state = MagicMock()
        mock_state.literals = frozenset({"on(b,a)", "clear(b)"})  # 1 of 2 goals met
        mock_state.goal = frozenset({"on(b,a)", "ontable(a)"})
        mock_env.reset.return_value = (mock_state, {})
        mock_env.step.return_value = (mock_state, 0, False, {})
        mock_env.action_space.all_ground_literals.return_value = []

        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym.make",
            return_value=mock_env,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            wrapper.reset()
            _, progress, _ = wrapper.step("pick-up(a)")

        assert progress == 0.5  # 1 of 2 goals satisfied

    def test_get_valid_actions(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        mock_state = MagicMock()
        mock_state.literals = frozenset()
        mock_state.goal = frozenset()
        mock_env.reset.return_value = (mock_state, {})
        mock_actions = [MagicMock(__str__=lambda s: "pick-up(a)"), MagicMock(__str__=lambda s: "stack(a,b)")]
        mock_env.action_space.all_ground_literals.return_value = mock_actions

        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym.make",
            return_value=mock_env,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            wrapper.reset()
            actions = wrapper.get_valid_actions()

        assert isinstance(actions, list)
        assert len(actions) == 2

    def test_close(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        mock_state = MagicMock()
        mock_state.literals = frozenset()
        mock_state.goal = frozenset()
        mock_env.reset.return_value = (mock_state, {})

        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym.make",
            return_value=mock_env,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            wrapper.reset()
            wrapper.close()

        mock_env.close.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestPDDLWrapper -v`
Expected: FAIL

- [ ] **Step 3: Implement PDDLWrapper**

Create `src/programmaticmemory/benchmarks/_pddl_wrapper.py`:

```python
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
    goal_lits = set(str(g) for g in state.goal)
    current_lits = set(str(lit) for lit in state.literals)
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
                matched = list(valid)[0]  # fallback

        if matched is None:
            return "No valid actions available.", self._max_progress, True

        self._state, reward, done, _info = self._env.step(matched)
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestPDDLWrapper -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/benchmarks/_pddl_wrapper.py tests/evolution/test_agentboard.py
git commit -m "feat: add PDDL planning wrapper for agentboard benchmark"
```

---

## Chunk 4: Main Benchmark + ValScorer + Integration

### Task 4: AgentBoard benchmark registration and data loading

**Files:**
- Create: `src/programmaticmemory/benchmarks/agentboard.py`
- Modify: `tests/evolution/test_agentboard.py`

- [ ] **Step 1: Write failing tests for load_agentboard**

Append to `tests/evolution/test_agentboard.py`:

```python
from programmaticmemory.evolution.types import DataItem, Dataset


class TestLoadAgentboard:
    def test_category_required(self):
        """Must specify a category — no default all-environments mode."""
        from programmaticmemory.benchmarks.agentboard import load_agentboard

        with pytest.raises(ValueError, match="category"):
            load_agentboard(category=None)

    def test_invalid_category_raises(self):
        from programmaticmemory.benchmarks.agentboard import load_agentboard

        with pytest.raises(ValueError, match="category"):
            load_agentboard(category="nonexistent")

    def test_scienceworld_loads_with_mock(self):
        from programmaticmemory.benchmarks.agentboard import load_agentboard

        mock_env = MagicMock()
        mock_env.get_task_names.return_value = ["boil", "melt"]
        mock_env.get_variations_train.return_value = [0, 1, 2]
        mock_env.get_variations_dev.return_value = [3, 4]
        mock_env.get_task_description.return_value = "Boil water in a pot."

        with patch(
            "programmaticmemory.benchmarks.agentboard.ScienceWorldEnv",
            return_value=mock_env,
        ):
            ds = load_agentboard(category="scienceworld", num_train=2, num_val=2)

        assert isinstance(ds, Dataset)
        assert len(ds.train) <= 2
        assert len(ds.val) <= 2
        assert ds.val_scorer is not None
        for item in ds.train:
            assert item.raw_text  # non-empty
        for item in ds.val:
            assert "env" in item.metadata
            assert item.metadata["env"] == "scienceworld"

    def test_available_categories(self):
        from programmaticmemory.benchmarks.agentboard import AVAILABLE_CATEGORIES

        assert "scienceworld" in AVAILABLE_CATEGORIES
        assert "babyai" in AVAILABLE_CATEGORIES
        assert "pddl" in AVAILABLE_CATEGORIES
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestLoadAgentboard -v`
Expected: FAIL

- [ ] **Step 3: Implement agentboard.py**

Create `src/programmaticmemory/benchmarks/agentboard.py`. Key components:
1. `AVAILABLE_CATEGORIES` constant
2. `load_agentboard()` function with `@register_dataset("agentboard")`
3. Per-environment data loading helpers
4. `AgentBoardValScorer` class
5. `_run_episode()` module-level function (picklable)
6. `_select_action()` function (reuses ALFWorld pattern)

```python
"""AgentBoard benchmark — ScienceWorld, BabyAI, PDDL interactive environments.

Unified benchmark for multi-turn goal-oriented tasks from AgentBoard.
Each environment is selected via the `category` parameter.
"""

from __future__ import annotations

import random
import re
from typing import Any

import litellm

from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

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
    from scienceworld import ScienceWorldEnv
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
                train_items.append(DataItem(
                    raw_text=f"Task: {task_name}\nDescription: {task_desc}\nVariation: {var}",
                    question="",
                    expected_answer="",
                    metadata={"env": "scienceworld", "task_name": task_name, "variation_idx": var},
                ))
            for var in dev_vars:
                val_items.append(DataItem(
                    raw_text="",
                    question=task_desc,
                    expected_answer="Task completed successfully",
                    metadata={"env": "scienceworld", "task_name": task_name, "variation_idx": var},
                ))
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
                train_items.append(DataItem(
                    raw_text=f"Level: {level_id}\nMission: {mission}\nSeed: {s}",
                    question="",
                    expected_answer="",
                    metadata={"env": "babyai", "env_id": level_id, "seed": s},
                ))
            else:
                val_items.append(DataItem(
                    raw_text="",
                    question=mission,
                    expected_answer="Task completed successfully",
                    metadata={"env": "babyai", "env_id": level_id, "seed": s},
                ))
        env.close()
    rng.shuffle(train_items)
    rng.shuffle(val_items)
    if num_train is not None:
        train_items = train_items[:num_train]
    if num_val is not None:
        val_items = val_items[:num_val]
    return train_items, val_items


def _load_pddl(num_train: int | None, num_val: int | None, seed: int) -> tuple[list[DataItem], list[DataItem]]:
    import pddlgym
    train_items: list[DataItem] = []
    val_items: list[DataItem] = []
    for domain, (env_id, num_problems) in PDDL_ENVS.items():
        train_count = num_problems // 2
        for idx in range(num_problems):
            if idx < train_count:
                train_items.append(DataItem(
                    raw_text=f"Domain: {domain}\nProblem: {idx}",
                    question="",
                    expected_answer="",
                    metadata={"env": "pddl", "domain": domain, "env_id": env_id, "problem_idx": idx},
                ))
            else:
                train_items.append(DataItem(  # NOTE: this should be val_items
                    raw_text="",
                    question=f"Solve {domain} problem {idx}",
                    expected_answer="All goals satisfied",
                    metadata={"env": "pddl", "domain": domain, "env_id": env_id, "problem_idx": idx},
                ))
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
    env_type: str, objective: str, tips: str, trajectory_text: str,
    valid_actions: list[str], task_model: str, always_on_knowledge: str = "",
) -> str:
    env_desc = {
        "scienceworld": "You are controlling a text-based ScienceWorld environment to perform science experiments.",
        "babyai": "You are controlling a BabyAI grid-world environment. Navigate and interact with objects to complete the mission.",
        "pddl": "You are solving a PDDL planning problem. Choose actions to satisfy all goal conditions.",
    }.get(env_type, "You are controlling a text-based environment.")

    lines = [env_desc, "Choose the NEXT action as ONE text command.", "Output ONLY the command, no extra text.",
             "You MUST choose from the valid actions list and copy it EXACTLY."]
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
    resp = litellm.completion(model=task_model, messages=[{"role": "user", "content": prompt}], max_tokens=64, caching=True)
    raw = resp.choices[0].message.content.strip()
    return _parse_action_response(raw, valid_actions)


# -- Episode runner (module-level for ProcessPoolExecutor pickling) --

def _run_episode(
    env_type: str, env_config: dict, objective: str, tips: str,
    task_model: str, max_steps: int, always_on_knowledge: str = "",
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
        raise ValueError(f"Unknown env_type: {env_type}")

    try:
        obs = wrapper.reset()
        trajectory_lines = [obs.strip()]
        progress = 0.0
        done = False
        for _step in range(max_steps):
            valid_actions = wrapper.get_valid_actions()
            if not valid_actions:
                break
            action = _select_action(env_type, objective, tips, "\n".join(trajectory_lines), valid_actions, task_model, always_on_knowledge)
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
        self, items: list[DataItem], retrieved: list[str], task_model: str,
        instruction_response: str, always_on_knowledge: str = "",
    ) -> list[tuple[str, float]]:
        import concurrent.futures
        workers = min(self.max_workers, len(items)) if items else 1
        with concurrent.futures.ProcessPoolExecutor(max_workers=workers) as pool:
            futures = [
                pool.submit(_run_episode, item.metadata["env"], item.metadata, item.question, tips, task_model, self.max_steps, always_on_knowledge)
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
    *, num_train: int | None = None, num_val: int | None = None,
    category: str | None = None, seed: int = 42,
) -> Dataset:
    if category is None or category not in AVAILABLE_CATEGORIES:
        raise ValueError(f"category must be one of {AVAILABLE_CATEGORIES}, got {category!r}")

    loaders = {
        "scienceworld": _load_scienceworld,
        "babyai": _load_babyai,
        "pddl": _load_pddl,
    }
    train, val = loaders[category](num_train, num_val, seed)

    val_scorer = AgentBoardValScorer()
    return Dataset(
        train=train, val=val, test=[],
        scorer=ExactMatchScorer(),
        val_scorer=val_scorer,
        available_categories=AVAILABLE_CATEGORIES,
    )
```

**Important:** The code above has a known bug in `_load_pddl` (appends val items to `train_items`). The tests will catch this. Fix during implementation by ensuring val items go to `val_items`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestLoadAgentboard -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/benchmarks/agentboard.py tests/evolution/test_agentboard.py
git commit -m "feat: add agentboard benchmark with ScienceWorld, BabyAI, PDDL support"
```

### Task 5: AgentBoardValScorer tests

**Files:**
- Modify: `tests/evolution/test_agentboard.py`

- [ ] **Step 1: Write tests for ValScorer and episode runner**

Append to `tests/evolution/test_agentboard.py`:

```python
class TestAgentBoardValScorer:
    def test_score_batch_dispatches_episodes(self):
        from programmaticmemory.benchmarks.agentboard import AgentBoardValScorer

        scorer = AgentBoardValScorer(max_steps=30, max_workers=2)
        items = [
            DataItem(raw_text="", question="Boil water", expected_answer="",
                     metadata={"env": "scienceworld", "task_name": "boil", "variation_idx": 0}),
            DataItem(raw_text="", question="Melt ice", expected_answer="",
                     metadata={"env": "scienceworld", "task_name": "melt", "variation_idx": 0}),
        ]
        retrieved = ["tips1", "tips2"]

        import concurrent.futures
        with (
            patch("concurrent.futures.ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor),
            patch("programmaticmemory.benchmarks.agentboard._run_episode", return_value=("transcript", 0.75)),
        ):
            results = scorer.score_batch(items, retrieved, "mock/model", "instruction", "")

        assert len(results) == 2
        assert all(score == 0.75 for _, score in results)

    def test_score_batch_handles_failure(self):
        from programmaticmemory.benchmarks.agentboard import AgentBoardValScorer

        scorer = AgentBoardValScorer(max_steps=30)
        items = [
            DataItem(raw_text="", question="Boil water", expected_answer="",
                     metadata={"env": "scienceworld", "task_name": "boil", "variation_idx": 0}),
        ]

        import concurrent.futures
        with (
            patch("concurrent.futures.ProcessPoolExecutor", concurrent.futures.ThreadPoolExecutor),
            patch("programmaticmemory.benchmarks.agentboard._run_episode", side_effect=RuntimeError("crashed")),
        ):
            results = scorer.score_batch(items, ["tips"], "mock/model", "instruction", "")

        assert len(results) == 1
        assert results[0][1] == 0.0
        assert "Episode failed" in results[0][0]


class TestAgentBoardActionSelection:
    def test_parse_action_exact_match(self):
        from programmaticmemory.benchmarks.agentboard import _parse_action_response

        assert _parse_action_response("pick up thermometer", ["pick up thermometer", "look"]) == "pick up thermometer"

    def test_parse_action_case_insensitive(self):
        from programmaticmemory.benchmarks.agentboard import _parse_action_response

        assert _parse_action_response("PICK UP THERMOMETER", ["pick up thermometer", "look"]) == "pick up thermometer"

    def test_parse_action_strips_prefix(self):
        from programmaticmemory.benchmarks.agentboard import _parse_action_response

        assert _parse_action_response("Action: look", ["pick up thermometer", "look"]) == "look"

    def test_parse_action_fallback(self):
        from programmaticmemory.benchmarks.agentboard import _parse_action_response

        assert _parse_action_response("random nonsense", ["pick up thermometer", "look"]) == "pick up thermometer"
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py::TestAgentBoardValScorer tests/evolution/test_agentboard.py::TestAgentBoardActionSelection -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/evolution/test_agentboard.py
git commit -m "test: add AgentBoardValScorer and action selection tests"
```

### Task 6: Integration — pyproject.toml, __init__.py, conftest, CLAUDE.md

**Files:**
- Modify: `src/programmaticmemory/benchmarks/__init__.py`
- Modify: `pyproject.toml`
- Modify: `tests/evolution/conftest.py` (add marker)
- Modify: `CLAUDE.md`

- [ ] **Step 1: Update benchmarks/__init__.py**

Add import:
```python
import programmaticmemory.benchmarks.agentboard as agentboard
```

- [ ] **Step 2: Update pyproject.toml**

Add optional dependency group:
```toml
agentboard = [
    "scienceworld",
    "minigrid>=2.3.0",
    "pddlgym",
]
```

Add pytest marker:
```toml
"agentboard: tests that require agentboard environment packages (scienceworld, minigrid, pddlgym)",
```

- [ ] **Step 3: Update CLAUDE.md**

Add to the benchmark descriptions section:
```
- **benchmarks/agentboard.py** — AgentBoard interactive environments (ScienceWorld, BabyAI, PDDL). Unified benchmark with `--category` selection. Train has `raw_text` (task descriptions). Val uses `AgentBoardValScorer` for real env interaction (progress rate). Requires `pip install -e ".[agentboard]"` for env interaction. Per-env wrappers: `_scienceworld_wrapper.py`, `_babyai_wrapper.py`, `_pddl_wrapper.py`.
```

- [ ] **Step 4: Run full test suite**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/ -m "not llm and not alfworld and not agentboard" -v`
Expected: All existing tests still PASS, no regressions

- [ ] **Step 5: Run agentboard tests specifically**

Run: `cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos && uv run pytest tests/evolution/test_agentboard.py -v`
Expected: All agentboard tests PASS (they mock the underlying envs)

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/benchmarks/__init__.py pyproject.toml CLAUDE.md
git commit -m "feat: integrate agentboard benchmark into project infrastructure"
```
