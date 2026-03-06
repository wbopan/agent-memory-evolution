"""Tests for evolution/loop.py — evolution loop logic."""

from __future__ import annotations

from unittest.mock import MagicMock

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.types import DataItem, Dataset, EvalResult, FailedCase, KBProgram


def _make_dataset():
    return Dataset(
        train=[DataItem(raw_text="Fact 1", question="Q1?", expected_answer="A1")],
        val=[DataItem(raw_text="x", question="Q1?", expected_answer="A1")],
        test=[],
    )


class TestEvolutionLoop:
    def test_initial_evaluation_only(self):
        """With max_iterations=0, only seed programs are evaluated."""
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
        assert len(state.pool) == 1

    def test_child_improves_best(self):
        """Child with higher score becomes new best."""
        dataset = _make_dataset()
        child_program = KBProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset, max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.8
        assert state.best_program == child_program
        assert len(state.pool) == 2  # seed + child both in pool

    def test_child_worse_still_added_to_pool(self):
        """Child with lower score is still added to pool."""
        dataset = _make_dataset()
        initial = KBProgram(source_code=INITIAL_KB_PROGRAM)
        child = KBProgram(source_code="worse", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.7, failed_cases=[]),
            EvalResult(score=0.3, per_case_scores=[0.3]),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.7
        assert state.best_program == initial
        assert len(state.pool) == 2  # both in pool

    def test_multiple_seeds(self):
        """Multiple seed programs are all evaluated and added to pool."""
        dataset = _make_dataset()
        seed1 = KBProgram(source_code="seed1")
        seed2 = KBProgram(source_code="seed2")

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3),
            EvalResult(score=0.7),
        ]
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[seed1, seed2], max_iterations=0,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 2
        assert len(state.pool) == 2
        assert state.best_score == 0.7
        assert state.best_program == seed2

    def test_reflection_failure_skips_iteration(self):
        """If reflector returns None, iteration is skipped."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = None

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset, max_iterations=2,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1  # Only seed
        assert reflector.reflect_and_mutate.call_count == 2
        assert state.total_iterations == 2
        assert len(state.pool) == 1  # No children added

    def test_stop_condition_halts_loop(self):
        """Stop condition should terminate the loop early."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])
        reflector = MagicMock(spec=Reflector)
        stop = MagicMock()
        stop.return_value = True

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            max_iterations=10, stop_condition=stop,
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
        reflector.reflect_and_mutate.return_value = KBProgram(source_code="child", generation=1)
        reflector.max_fix_attempts = 3
        tracker = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            max_iterations=1, tracker=tracker,
        )
        loop.run()

        assert tracker.log_metrics.call_count >= 2
        assert tracker.log_summary.call_count == 1

    def test_parent_hash_tracked_in_history(self):
        """EvolutionRecord should track which parent was selected."""
        dataset = _make_dataset()
        initial = KBProgram(source_code=INITIAL_KB_PROGRAM)
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        # Last record should have parent_hash pointing to initial
        last_record = state.history[-1]
        assert last_record.parent_hash == initial.hash


class TestEvolutionLoopRuntimeFix:
    """Tests for runtime violation fix loop in EvolutionLoop."""

    def test_runtime_violation_triggers_fix_and_reeval(self):
        """Runtime violation -> fix_runtime_violation called -> re-eval succeeds."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),
            EvalResult(score=0.8),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed code"
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        reflector.fix_runtime_violation.assert_called_once_with(
            "child", "memory.read() returned 5000 chars (limit: 1000)"
        )
        assert evaluator.evaluate.call_count == 3
        assert state.best_score == 0.8

    def test_runtime_violation_fix_returns_none(self):
        """Runtime violation -> fix returns None -> child added with score=0."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() timed out after 5.0s"),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = None
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert evaluator.evaluate.call_count == 2
        assert len(state.pool) == 2  # both added

    def test_runtime_violation_fix_loop_retries(self):
        """First fix still violates -> loop retries -> second fix succeeds."""
        initial = KBProgram(source_code="initial")
        child = KBProgram(source_code="child", generation=1)
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),
            EvalResult(score=0.0, runtime_violation="memory.read() returned 3000 chars (limit: 1000)"),
            EvalResult(score=0.7),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.side_effect = ["fix1", "fix2"]
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator, reflector=reflector, dataset=dataset,
            initial_programs=[initial], max_iterations=1,
        )
        state = loop.run()

        assert reflector.fix_runtime_violation.call_count == 2
        assert evaluator.evaluate.call_count == 4
        assert state.best_score == 0.7
