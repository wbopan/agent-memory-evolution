"""Tests for AWM baseline — trajectory capture, workflow induction, retrieval."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from programmaticmemory.baselines.awm import (
    Trajectory,
    TrajectoryStep,
    Workflow,
    WorkflowStep,
    _cosine_similarity,
    _format_existing_workflows,
    _format_trajectory_for_induction,
    _parse_reasoning_and_action,
    _parse_workflow_json,
    format_workflows_as_tips,
    induce_workflows,
    induce_workflows_from_trajectory,
    retrieve_workflows,
)

# -- Fixtures --


def _make_trajectory(env_type="babyai", objective="go to the red ball", progress=1.0, n_steps=3) -> Trajectory:
    steps = [
        TrajectoryStep(observation=f"obs_{i}", reasoning=f"reason_{i}", action=f"action_{i}") for i in range(n_steps)
    ]
    return Trajectory(
        env_type=env_type,
        objective=objective,
        env_config={"env_id": "BabyAI-GoToRedBall-v0", "seed": 0},
        steps=steps,
        progress=progress,
    )


def _make_workflow(description="Navigate to object", n_steps=2, source_env="babyai") -> Workflow:
    steps = [
        WorkflowStep(
            state_description=f"state_{i}",
            reasoning=f"reasoning_{i}",
            action=f"action_{i}",
        )
        for i in range(n_steps)
    ]
    return Workflow(description=description, steps=steps, source_env=source_env)


# -- Reasoning+action parsing --


class TestParseReasoningAndAction:
    def test_standard_format(self):
        response = "REASONING: I need to go forward to reach the ball.\nACTION: go forward"
        reasoning, action = _parse_reasoning_and_action(response, ["go forward", "turn left"])
        assert reasoning == "I need to go forward to reach the ball."
        assert action == "go forward"

    def test_action_only(self):
        response = "go forward"
        reasoning, action = _parse_reasoning_and_action(response, ["go forward", "turn left"])
        assert reasoning == ""
        assert action == "go forward"

    def test_reasoning_without_action_label(self):
        response = "REASONING: The ball is ahead.\ngo forward"
        reasoning, action = _parse_reasoning_and_action(response, ["go forward", "turn left"])
        assert reasoning == "The ball is ahead.\ngo forward"
        # Falls back to first valid action since ACTION: label missing
        assert action == "go forward"


# -- JSON parsing --


class TestParseWorkflowJson:
    def test_plain_json_array(self):
        raw = '[{"description": "nav", "steps": []}]'
        result = _parse_workflow_json(raw)
        assert len(result) == 1
        assert result[0]["description"] == "nav"

    def test_json_in_code_block(self):
        raw = '```json\n[{"description": "heat", "steps": [{"state_description": "s", "reasoning": "r", "action": "a"}]}]\n```'
        result = _parse_workflow_json(raw)
        assert len(result) == 1
        assert len(result[0]["steps"]) == 1

    def test_invalid_json_returns_empty(self):
        assert _parse_workflow_json("not json at all") == []

    def test_single_object_wrapped_in_list(self):
        raw = '{"description": "single", "steps": []}'
        result = _parse_workflow_json(raw)
        assert len(result) == 1


# -- Trajectory formatting (includes reasoning) --


class TestFormatTrajectory:
    def test_format_includes_reasoning(self):
        traj = _make_trajectory(objective="pick up the key", n_steps=2)
        text = _format_trajectory_for_induction(traj)
        assert "pick up the key" not in text  # objective is not in the formatted steps
        assert "reason_0" in text
        assert "reason_1" in text
        assert "action_0" in text
        assert "obs_0" in text

    def test_three_fields_per_step(self):
        traj = _make_trajectory(n_steps=1)
        text = _format_trajectory_for_induction(traj)
        assert "Observation:" in text
        assert "Reasoning:" in text
        assert "Action:" in text


# -- Existing workflow formatting for composition --


class TestFormatExistingWorkflows:
    def test_empty_returns_empty(self):
        assert _format_existing_workflows([]) == ""

    def test_includes_description_and_actions(self):
        w = _make_workflow(description="Navigate to door", n_steps=2)
        text = _format_existing_workflows([w])
        assert "Navigate to door" in text
        assert "action_0" in text


# -- Workflow tips formatting --


class TestFormatWorkflows:
    def test_empty_returns_empty_string(self):
        assert format_workflows_as_tips([]) == ""

    def test_single_workflow_has_state_and_action(self):
        w = _make_workflow(description="Navigate to door", n_steps=1)
        tips = format_workflows_as_tips([w])
        assert "Workflow 1: Navigate to door" in tips
        assert "State: state_0" in tips
        assert "Action: action_0" in tips

    def test_multiple_workflows(self):
        ws = [_make_workflow(description=f"wf_{i}") for i in range(3)]
        tips = format_workflows_as_tips(ws)
        assert "Workflow 1: wf_0" in tips
        assert "Workflow 3: wf_2" in tips


# -- Cosine similarity --


class TestCosineSimilarity:
    def test_identical_vectors(self):
        assert _cosine_similarity([1, 0, 0], [1, 0, 0]) == pytest.approx(1.0)

    def test_orthogonal_vectors(self):
        assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)

    def test_zero_vector(self):
        assert _cosine_similarity([0, 0], [1, 1]) == pytest.approx(0.0)


# -- Per-trajectory workflow induction (mocked LLM) --


class TestInduceWorkflowsFromTrajectory:
    @patch("programmaticmemory.baselines.awm.litellm")
    def test_induction_with_no_existing_workflows(self, mock_litellm):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = """```json
[
  {
    "description": "Navigate to target object",
    "steps": [
      {"state_description": "Agent sees object ahead", "reasoning": "Move toward it", "action": "go forward"}
    ]
  }
]
```"""
        mock_litellm.completion.return_value = mock_resp

        traj = _make_trajectory(progress=1.0)
        workflows = induce_workflows_from_trajectory(traj, existing_workflows=[], model="test/model")
        assert len(workflows) == 1
        assert workflows[0].description == "Navigate to target object"
        assert workflows[0].source_env == "babyai"

        # Should NOT include composition guideline about existing workflows
        call_args = mock_litellm.completion.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Previously induced workflows" not in prompt

    @patch("programmaticmemory.baselines.awm.litellm")
    def test_induction_with_existing_workflows_enables_composition(self, mock_litellm):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[
            0
        ].message.content = (
            '[{"description": "complex wf", "steps": [{"state_description": "s", "reasoning": "r", "action": "a"}]}]'
        )
        mock_litellm.completion.return_value = mock_resp

        existing = [_make_workflow(description="simple nav")]
        traj = _make_trajectory(progress=1.0)
        workflows = induce_workflows_from_trajectory(traj, existing_workflows=existing, model="test/model")

        # Prompt should include existing workflows for composition
        call_args = mock_litellm.completion.call_args
        prompt = call_args[1]["messages"][0]["content"]
        assert "Previously induced workflows" in prompt
        assert "simple nav" in prompt
        assert "COMPOSE" in prompt
        assert len(workflows) == 1

    @patch("programmaticmemory.baselines.awm.litellm")
    def test_empty_description_or_steps_filtered(self, mock_litellm):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[
            0
        ].message.content = '[{"description": "", "steps": []}, {"description": "valid", "steps": [{"state_description": "s", "reasoning": "r", "action": "a"}]}]'
        mock_litellm.completion.return_value = mock_resp

        traj = _make_trajectory(progress=1.0)
        workflows = induce_workflows_from_trajectory(traj, [], model="test/model")
        assert len(workflows) == 1
        assert workflows[0].description == "valid"


# -- Iterative induction with snowball effect --


class TestInduceWorkflows:
    def test_no_successful_trajectories(self):
        trajs = [_make_trajectory(progress=0.0)]
        result = induce_workflows(trajs, model="test/model", min_progress=0.5)
        assert result == []

    @patch("programmaticmemory.baselines.awm.litellm")
    def test_iterative_induction_passes_existing_as_context(self, mock_litellm):
        """Second trajectory's induction should see workflows from first."""
        call_count = [0]

        def fake_completion(**kwargs):
            call_count[0] += 1
            resp = MagicMock()
            resp.choices = [MagicMock()]
            if call_count[0] == 1:
                resp.choices[
                    0
                ].message.content = '[{"description": "basic nav", "steps": [{"state_description": "s", "reasoning": "r", "action": "a"}]}]'
            else:
                resp.choices[
                    0
                ].message.content = '[{"description": "complex pickup", "steps": [{"state_description": "s2", "reasoning": "r2", "action": "a2"}]}]'
            return resp

        mock_litellm.completion.side_effect = fake_completion

        trajs = [
            _make_trajectory(env_type="babyai", progress=1.0, objective="task1"),
            _make_trajectory(env_type="babyai", progress=1.0, objective="task2"),
        ]
        workflows = induce_workflows(trajs, model="test/model", min_progress=0.0)

        # Should have called completion twice (once per trajectory)
        assert mock_litellm.completion.call_count == 2
        assert len(workflows) == 2

        # Second call should include "basic nav" from first induction
        second_call_prompt = mock_litellm.completion.call_args_list[1][1]["messages"][0]["content"]
        assert "basic nav" in second_call_prompt

    @patch("programmaticmemory.baselines.awm.litellm")
    def test_deduplicates_by_description(self, mock_litellm):
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[
            0
        ].message.content = '[{"description": "Navigate to object", "steps": [{"state_description": "s", "reasoning": "r", "action": "a"}]}]'
        mock_litellm.completion.return_value = mock_resp

        trajs = [
            _make_trajectory(progress=1.0, objective="task1"),
            _make_trajectory(progress=1.0, objective="task2"),
        ]
        workflows = induce_workflows(trajs, model="test/model", min_progress=0.0)
        # Both return same description → should deduplicate
        assert len(workflows) == 1


# -- Workflow retrieval (mocked embeddings) --


class TestRetrieveWorkflows:
    @patch("programmaticmemory.baselines.awm.embed_texts")
    def test_retrieves_most_similar(self, mock_embed):
        w1 = _make_workflow(description="navigate to object")
        w1.embedding = [1.0, 0.0, 0.0]
        w2 = _make_workflow(description="heat substance")
        w2.embedding = [0.0, 1.0, 0.0]
        w3 = _make_workflow(description="move to target")
        w3.embedding = [0.9, 0.1, 0.0]

        # Query embedding similar to w1 and w3
        mock_embed.return_value = [[0.95, 0.05, 0.0]]

        result = retrieve_workflows("go to the ball", [w1, w2, w3], top_k=2)
        descriptions = [w.description for w in result]
        assert "navigate to object" in descriptions
        assert "move to target" in descriptions
        assert len(result) == 2

    def test_empty_workflows_returns_empty(self):
        result = retrieve_workflows("query", [], top_k=3)
        assert result == []
