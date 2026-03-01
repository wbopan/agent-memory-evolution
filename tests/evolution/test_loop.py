"""Tests for evolution/loop.py — evolution loop logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_MEMORY_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.types import DataItem, Dataset, EvalResult, FailedCase, MemoryProgram


def _make_dataset():
    return Dataset(
        train=[DataItem(raw_text="Fact 1", question="Q1?", expected_answer="A1")],
        val=[DataItem(raw_text="x", question="Q1?", expected_answer="A1")],
        test=[],
    )


class TestEvolutionLoop:
    def test_initial_evaluation_only(self):
        """With max_iterations=0, only initial program is evaluated."""
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, per_case_scores=[0.5])

        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert state.total_iterations == 0
        assert evaluator.evaluate.call_count == 1
        assert reflector.reflect_and_mutate.call_count == 0

    def test_child_accepted_when_better(self):
        """Child program replaces current when it scores higher."""
        dataset = _make_dataset()

        child_program = MemoryProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        # First call: initial eval (0.3), second call: child eval (0.8)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.8
        assert state.best_program == child_program
        assert state.history[-1].accepted is True

    def test_child_rejected_when_worse(self):
        """Child program is rejected when it scores lower."""
        dataset = _make_dataset()

        initial = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        child = MemoryProgram(source_code="worse", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.7, failed_cases=[]),
            EvalResult(score=0.3, per_case_scores=[0.3]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.7
        assert state.best_program == initial
        assert state.history[-1].accepted is False

    def test_reflection_failure_skips_iteration(self):
        """If reflector returns None, iteration is skipped."""
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = None

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=2,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1  # Only initial
        assert reflector.reflect_and_mutate.call_count == 2  # Tried both iterations
        assert state.total_iterations == 2

    def test_stop_condition_halts_loop(self):
        """Stop condition should terminate the loop early."""
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)

        # Stop after first check
        stop = MagicMock()
        stop.return_value = True

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=10,
            stop_condition=stop,
        )
        state = loop.run()

        assert state.total_iterations == 0
        assert reflector.reflect_and_mutate.call_count == 0

    def test_tracker_receives_metrics(self):
        """ExperimentTracker should receive log_metrics calls."""
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = MemoryProgram(source_code="child", generation=1)
        reflector.max_fix_attempts = 3

        tracker = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
            tracker=tracker,
        )
        loop.run()

        # Initial eval logged + iteration logged + summary
        assert tracker.log_metrics.call_count >= 2
        assert tracker.log_summary.call_count == 1


class TestEvolutionLoopRuntimeFix:
    """Tests for runtime violation fix loop in EvolutionLoop."""

    def test_runtime_violation_triggers_fix_and_reeval(self):
        """Runtime violation -> fix_runtime_violation called -> re-eval succeeds."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial eval
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),  # child
            EvalResult(score=0.8),  # re-eval after fix
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed code"
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        # fix_runtime_violation was called with the child's source and violation message
        reflector.fix_runtime_violation.assert_called_once_with(
            "child", "memory.read() returned 5000 chars (limit: 1000)"
        )
        # evaluator called 3 times: initial, child (violation), fixed child
        assert evaluator.evaluate.call_count == 3
        # Fixed child was accepted (0.8 > 0.5)
        assert state.best_score == 0.8

    def test_runtime_violation_fix_returns_none(self):
        """Runtime violation -> fix returns None -> iteration uses score=0."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial
            EvalResult(score=0.0, runtime_violation="memory.read() timed out after 5.0s"),  # child
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = None
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        # Fix failed, best stays at initial
        assert state.best_score == 0.5
        assert evaluator.evaluate.call_count == 2

    def test_runtime_violation_fix_loop_retries(self):
        """First fix still violates -> loop retries -> second fix succeeds."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),  # child
            EvalResult(
                score=0.0, runtime_violation="memory.read() returned 3000 chars (limit: 1000)"
            ),  # fix1 still violates
            EvalResult(score=0.7),  # fix2 works
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.side_effect = ["fix1", "fix2"]
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        assert reflector.fix_runtime_violation.call_count == 2
        assert evaluator.evaluate.call_count == 4
        assert state.best_score == 0.7
