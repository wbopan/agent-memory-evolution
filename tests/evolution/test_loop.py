"""Tests for evolution/loop.py — evolution loop logic."""

from unittest.mock import MagicMock, patch

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_MEMORY_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.types import DataItem, EvalResult, FailedCase, MemoryProgram


def _make_train_val():
    return (
        [DataItem(raw_text="Fact 1", question="Q1?", expected_answer="A1")],
        [DataItem(raw_text="x", question="Q1?", expected_answer="A1")],
    )


class TestEvolutionLoop:
    def test_initial_evaluation_only(self):
        """With max_iterations=0, only initial program is evaluated."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, per_case_scores=[0.5])

        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=0,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert state.total_iterations == 0
        assert evaluator.evaluate.call_count == 1
        assert reflector.reflect_and_mutate.call_count == 0

    @patch("programmaticmemory.evolution.loop.smoke_test")
    def test_child_accepted_when_better(self, mock_smoke):
        """Child program replaces current when it scores higher."""
        mock_smoke.return_value = MagicMock(success=True)
        train, val = _make_train_val()

        child_program = MemoryProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        # First call: initial eval (0.3), second call: child eval (0.8)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.8
        assert state.best_program == child_program
        assert state.history[-1].accepted is True

    @patch("programmaticmemory.evolution.loop.smoke_test")
    def test_child_rejected_when_worse(self, mock_smoke):
        """Child program is rejected when it scores lower."""
        mock_smoke.return_value = MagicMock(success=True)
        train, val = _make_train_val()

        initial = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        child = MemoryProgram(source_code="worse", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.7, failed_cases=[]),
            EvalResult(score=0.3, per_case_scores=[0.3]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.7
        assert state.best_program == initial
        assert state.history[-1].accepted is False

    @patch("programmaticmemory.evolution.loop.smoke_test")
    def test_smoke_test_failure_skips_evaluation(self, mock_smoke):
        """If smoke test fails, child is skipped without full evaluation."""
        mock_smoke.return_value = MagicMock(success=False, error="runtime crash")
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = MemoryProgram(source_code="bad", generation=1)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=1,
        )
        state = loop.run()

        # Evaluator called only once (initial), not for the child
        assert evaluator.evaluate.call_count == 1
        assert state.best_score == 0.5

    def test_reflection_failure_skips_iteration(self):
        """If reflector returns None, iteration is skipped."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = None

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=2,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1  # Only initial
        assert reflector.reflect_and_mutate.call_count == 2  # Tried both iterations
        assert state.total_iterations == 2

    def test_stop_condition_halts_loop(self):
        """Stop condition should terminate the loop early."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)

        # Stop after first check
        stop = MagicMock()
        stop.return_value = True

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=10,
            stop_condition=stop,
        )
        state = loop.run()

        assert state.total_iterations == 0
        assert reflector.reflect_and_mutate.call_count == 0

    @patch("programmaticmemory.evolution.loop.smoke_test")
    def test_tracker_receives_metrics(self, mock_smoke):
        """ExperimentTracker should receive log_metrics calls."""
        mock_smoke.return_value = MagicMock(success=True)
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = MemoryProgram(source_code="child", generation=1)

        tracker = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=1,
            tracker=tracker,
        )
        loop.run()

        # Initial eval logged + iteration logged + summary
        assert tracker.log_metrics.call_count >= 2
        assert tracker.log_summary.call_count == 1
