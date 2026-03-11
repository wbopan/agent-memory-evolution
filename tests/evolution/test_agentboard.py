"""Tests for AgentBoard benchmark (ScienceWorld, BabyAI, PDDL)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from programmaticmemory.evolution.types import DataItem, Dataset


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


class TestBabyAIGridToText:
    """Test the grid-to-text observation conversion."""

    def test_empty_room_description(self):
        from programmaticmemory.benchmarks._babyai_wrapper import grid_to_text

        grid = np.ones((7, 7, 3), dtype=np.uint8)
        grid[:, :, 0] = 1  # empty
        grid[:, :, 1] = 0
        grid[:, :, 2] = 0
        text = grid_to_text(grid, direction=0, carrying=None)
        assert isinstance(text, str)
        assert len(text) > 0

    def test_object_in_view(self):
        from programmaticmemory.benchmarks._babyai_wrapper import grid_to_text

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

        mock_gym = MagicMock()
        mock_gym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym",
            mock_gym,
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

        mock_gym = MagicMock()
        mock_gym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym",
            mock_gym,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            wrapper.reset()
            obs, _progress, _done = wrapper.step("turn left")

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

        mock_gym = MagicMock()
        mock_gym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym",
            mock_gym,
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

        mock_gym = MagicMock()
        mock_gym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._babyai_wrapper.gym",
            mock_gym,
        ):
            wrapper = BabyAIWrapper("BabyAI-GoToRedBall-v0", seed=42)
            wrapper.reset()
            wrapper.close()

        mock_env.close.assert_called_once()


class TestPDDLWrapper:
    def test_reset_returns_text(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        mock_state = MagicMock()
        mock_state.literals = frozenset({"on(a,b)", "clear(a)", "ontable(b)"})
        mock_state.goal = frozenset({"on(b,a)", "ontable(a)"})
        mock_env.reset.return_value = (mock_state, {})
        mock_env.action_space.all_ground_literals.return_value = [
            "pick-up(a)",
            "put-down(a)",
            "stack(a,b)",
        ]

        mock_pddlgym = MagicMock()
        mock_pddlgym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym",
            mock_pddlgym,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            obs = wrapper.reset()

        assert isinstance(obs, str)
        assert len(obs) > 0

    def test_progress_rate_partial_goal(self):
        from programmaticmemory.benchmarks._pddl_wrapper import PDDLWrapper

        mock_env = MagicMock()
        # Initial state for reset
        init_state = MagicMock()
        init_state.literals = frozenset({"clear(a)", "ontable(b)"})
        init_state.goal = frozenset({"on(b,a)", "ontable(a)"})
        mock_env.reset.return_value = (init_state, {})

        # After-step state: 1 of 2 goals met
        step_state = MagicMock()
        step_state.literals = frozenset({"on(b,a)", "clear(b)"})
        step_state.goal = frozenset({"on(b,a)", "ontable(a)"})
        mock_env.step.return_value = (step_state, 0, False, {})

        # Provide a valid action that matches the step command
        mock_action = MagicMock()
        mock_action.__str__ = lambda s: "pick-up(a)"
        mock_env.action_space.all_ground_literals.return_value = [mock_action]

        mock_pddlgym = MagicMock()
        mock_pddlgym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym",
            mock_pddlgym,
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

        mock_pddlgym = MagicMock()
        mock_pddlgym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym",
            mock_pddlgym,
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

        mock_pddlgym = MagicMock()
        mock_pddlgym.make.return_value = mock_env
        with patch(
            "programmaticmemory.benchmarks._pddl_wrapper.pddlgym",
            mock_pddlgym,
        ):
            wrapper = PDDLWrapper("PDDLEnvBlocks-v0", 0)
            wrapper.reset()
            wrapper.close()

        mock_env.close.assert_called_once()


class TestLoadAgentboard:
    def test_category_required(self):
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

    def test_pddl_loads_without_external_deps(self):
        """PDDL loader doesn't need pddlgym for data generation (unlike scienceworld/babyai)."""
        from programmaticmemory.benchmarks.agentboard import load_agentboard

        ds = load_agentboard(category="pddl", num_train=3, num_val=3)
        assert isinstance(ds, Dataset)
        assert len(ds.train) == 3
        assert len(ds.val) == 3
        for item in ds.train:
            assert item.raw_text  # has domain+problem info
        for item in ds.val:
            assert item.metadata["env"] == "pddl"


class TestAgentBoardValScorer:
    def test_score_batch_dispatches_episodes(self):
        from programmaticmemory.benchmarks.agentboard import AgentBoardValScorer

        scorer = AgentBoardValScorer(max_steps=30, max_workers=2)
        items = [
            DataItem(
                raw_text="",
                question="Boil water",
                expected_answer="",
                metadata={"env": "scienceworld", "task_name": "boil", "variation_idx": 0},
            ),
            DataItem(
                raw_text="",
                question="Melt ice",
                expected_answer="",
                metadata={"env": "scienceworld", "task_name": "melt", "variation_idx": 0},
            ),
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
            DataItem(
                raw_text="",
                question="Boil water",
                expected_answer="",
                metadata={"env": "scienceworld", "task_name": "boil", "variation_idx": 0},
            ),
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
