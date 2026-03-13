"""Tests for evolution/loop.py — evolution loop logic."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

from programmaticmemory.evolution.batching import EvalBatch
from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.sandbox import compile_kb_program
from programmaticmemory.evolution.strategies import FullDataset, RotatingBatch
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[initial],
            max_iterations=1,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[seed1, seed2],
            max_iterations=0,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=2,
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
        reflector.reflect_and_mutate.return_value = KBProgram(source_code="child", generation=1)
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[initial],
            max_iterations=1,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[initial],
            max_iterations=1,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[initial],
            max_iterations=1,
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
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[initial],
            max_iterations=1,
        )
        state = loop.run()

        assert reflector.fix_runtime_violation.call_count == 2
        assert evaluator.evaluate.call_count == 4
        assert state.best_score == 0.7


class TestBatchRotation:
    def _make_batches_and_dataset(self):
        """Create 2 batches over a dataset of 4 train + 4 val items."""
        train = [DataItem(raw_text=f"train_{i}", question=f"tq{i}?", expected_answer=f"ta{i}") for i in range(4)]
        val = [DataItem(raw_text="", question=f"vq{i}?", expected_answer=f"va{i}") for i in range(4)]
        ds = Dataset(train=train, val=val, test=[])
        batches = [
            EvalBatch(val_indices=[0, 1], train_indices=[0, 1], coverage=0.9),
            EvalBatch(val_indices=[2, 3], train_indices=[2, 3], coverage=0.8),
        ]
        return ds, batches

    def test_seeds_evaluated_on_batch_0(self):
        """All seeds should be evaluated using batch 0 data."""
        ds, batches = self._make_batches_and_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=0,
            eval_strategy=RotatingBatch(batches),
        )
        loop.run()

        # First call is seed eval on batch 0; final eval follows on full dataset
        call_args = evaluator.evaluate.call_args_list[0]
        train_arg = call_args[0][1]
        val_arg = call_args[0][2]
        assert len(train_arg) == 2
        assert len(val_arg) == 2
        assert train_arg[0].raw_text == "train_0"
        assert train_arg[1].raw_text == "train_1"
        assert val_arg[0].question == "vq0?"
        assert val_arg[1].question == "vq1?"

    def test_iterations_rotate_through_batches(self):
        """Iteration 1 uses batch 1, iteration 2 wraps to batch 0."""
        ds, batches = self._make_batches_and_dataset()
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),  # seed on batch 0
            EvalResult(score=0.6),  # iter 1 on batch 1
            EvalResult(score=0.7),  # iter 2 on batch 0 (wrap)
            EvalResult(score=0.7),  # final eval candidate 1
            EvalResult(score=0.6),  # final eval candidate 2
            EvalResult(score=0.5),  # final eval candidate 3
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=2,
            eval_strategy=RotatingBatch(batches),
        )
        loop.run()

        # Check iteration 1 used batch 1 (train_indices=[2,3], val_indices=[2,3])
        iter1_call = evaluator.evaluate.call_args_list[1]
        assert iter1_call[0][1][0].raw_text == "train_2"
        assert iter1_call[0][2][0].question == "vq2?"

        # Check iteration 2 wrapped to batch 0 (train_indices=[0,1], val_indices=[0,1])
        iter2_call = evaluator.evaluate.call_args_list[2]
        assert iter2_call[0][1][0].raw_text == "train_0"
        assert iter2_call[0][2][0].question == "vq0?"

    def test_runtime_fix_uses_same_batch(self):
        """Runtime violation re-eval should use the same batch as initial child eval."""
        ds, batches = self._make_batches_and_dataset()
        child = KBProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # seed on batch 0
            EvalResult(score=0.0, runtime_violation="timeout"),  # iter 1 on batch 1
            EvalResult(score=0.8),  # re-eval on batch 1
            EvalResult(score=0.8),  # final eval candidate 1
            EvalResult(score=0.5),  # final eval candidate 2
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed"
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=1,
            eval_strategy=RotatingBatch(batches),
        )
        loop.run()

        # Both iter 1 evals (initial + fix) should use batch 1
        iter1_initial = evaluator.evaluate.call_args_list[1]
        iter1_fix = evaluator.evaluate.call_args_list[2]
        assert iter1_initial[0][1][0].raw_text == "train_2"
        assert iter1_fix[0][1][0].raw_text == "train_2"

    def test_no_batches_uses_full_dataset(self):
        """Without batches, full ds.train/ds.val are used (existing behavior)."""
        ds, _batches = self._make_batches_and_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=0,
        )
        loop.run()

        call_args = evaluator.evaluate.call_args
        assert len(call_args[0][1]) == 4  # full train
        assert len(call_args[0][2]) == 4  # full val


class TestFinalEvaluation:
    def test_final_eval_runs_when_strategy_provides_data(self):
        """FixedRepresentative-like strategy triggers final evaluation."""
        dataset = _make_dataset()

        class MockStrategy:
            def select(self, ds, iteration):
                return ds.train[:1], ds.val[:1]

            def final_candidates(self, pool):
                return [pool.best]

            def final_eval_data(self, ds):
                return ds.train, ds.val

            def test_eval_data(self, ds):
                return None

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # seed on subset
            EvalResult(score=0.8),  # final eval on full
        ]
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
            eval_strategy=MockStrategy(),
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 2
        assert len(state.final_scores) == 1
        assert next(iter(state.final_scores.values())) == 0.8

    def test_final_eval_skipped_when_strategy_returns_none(self):
        """FullDataset-like strategy skips final evaluation."""
        dataset = _make_dataset()
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
            eval_strategy=FullDataset(),
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1  # only seed
        assert state.final_scores == {}


class TestFreezeInstructions:
    def test_freeze_restores_parent_constants(self):
        """When freeze_instructions=True, child's instruction constants match parent's."""
        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)

        child_source = INITIAL_KB_PROGRAM.replace(
            'INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."',
            'INSTRUCTION_KNOWLEDGE_ITEM = "CHANGED BY REFLECTOR"',
        )
        mock_reflector = Mock()
        mock_reflector.reflect_and_mutate.return_value = KBProgram(
            source_code=child_source, generation=1, parent_hash=parent.hash
        )
        mock_reflector.max_fix_attempts = 3

        mock_evaluator = Mock()
        mock_evaluator.evaluate.return_value = EvalResult(score=0.5)

        dataset = Dataset(train=[], val=[], test=[], scorer=None)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=1,
            freeze_instructions=True,
        )
        state = loop.run()

        children = [e for e in state.pool.entries if e.name != "seed_0"]
        assert len(children) == 1
        child_compiled = compile_kb_program(children[0].program.source_code)
        parent_compiled = compile_kb_program(INITIAL_KB_PROGRAM)
        assert child_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
        assert "CHANGED BY REFLECTOR" not in children[0].program.source_code
