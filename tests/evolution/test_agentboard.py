"""Tests for AgentBoard benchmark (ScienceWorld, BabyAI, PDDL)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


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
