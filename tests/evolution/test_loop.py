"""Tests for evolution/loop.py — evolution loop logic."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

import pytest

from programmaticmemory.evolution.batching import EvalBatch
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import ReflectionResult, Reflector
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child_program)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(
            program=KBProgram(source_code="child", generation=1)
        )
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
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
        mock_reflector.reflect_and_mutate.return_value = ReflectionResult(
            program=KBProgram(source_code=child_source, generation=1, parent_hash=parent.hash)
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


class TestExtraScorers:
    def test_extra_scorers_in_test_eval(self):
        """Extra scorers compute additional metrics from per_case_outputs."""
        mock_evaluator = Mock()
        seed_eval = EvalResult(score=0.5)
        test_eval = EvalResult(
            score=0.8,
            per_case_outputs=["alice", "wrong"],
            per_case_scores=[1.0, 0.6],
        )
        mock_evaluator.evaluate.side_effect = [seed_eval, test_eval]

        test_items = [
            DataItem(raw_text="", question="q1", expected_answer="alice"),
            DataItem(raw_text="", question="q2", expected_answer="bob"),
        ]
        dataset = Dataset(
            train=[],
            val=[],
            test=test_items,
            scorer=None,
            extra_scorers={"em": ExactMatchScorer()},
        )

        mock_strategy = Mock()
        mock_strategy.select.return_value = ([], [])
        mock_strategy.final_eval_data.return_value = None
        mock_strategy.test_eval_data.return_value = ([], test_items)
        mock_strategy.final_candidates.return_value = []

        mock_reflector = Mock()
        mock_reflector.max_fix_attempts = 3

        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=0,
            eval_strategy=mock_strategy,
        )
        state = loop.run()

        assert len(state.test_extra_metrics) == 1
        program_hash = list(state.test_extra_metrics.keys())[0]
        assert state.test_extra_metrics[program_hash]["em"] == 0.5


class TestPerCategoryScores:
    def test_final_category_scores_computed(self):
        """Per-category scores are computed in final eval when category_key is set."""
        val_items = [
            DataItem(raw_text="", question="q1", expected_answer="a1", metadata={"cat": "A"}),
            DataItem(raw_text="", question="q2", expected_answer="a2", metadata={"cat": "B"}),
            DataItem(raw_text="", question="q3", expected_answer="a3", metadata={"cat": "A"}),
        ]
        dataset = Dataset(
            train=[],
            val=val_items,
            test=[],
            scorer=None,
            category_key="cat",
        )

        mock_evaluator = Mock()
        # seed eval returns score=0.5; final eval returns per_case_scores=[1.0, 0.0, 0.5]
        seed_eval = EvalResult(score=0.5)
        final_eval = EvalResult(score=0.5, per_case_scores=[1.0, 0.0, 0.5])
        mock_evaluator.evaluate.side_effect = [seed_eval, final_eval]

        mock_strategy = Mock()
        mock_strategy.select.return_value = ([], val_items)
        mock_strategy.final_eval_data.return_value = ([], val_items)
        mock_strategy.final_candidates.side_effect = lambda pool: [pool.best]
        mock_strategy.test_eval_data.return_value = None

        mock_reflector = Mock()
        mock_reflector.max_fix_attempts = 3

        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=0,
            eval_strategy=mock_strategy,
        )
        state = loop.run()

        assert len(state.final_category_scores) == 1
        program_hash = list(state.final_category_scores.keys())[0]
        cat_scores = state.final_category_scores[program_hash]
        # Category A: items 0 and 2 → scores [1.0, 0.5] → avg 0.75
        assert cat_scores["A"] == pytest.approx(0.75)
        # Category B: item 1 → score [0.0] → avg 0.0
        assert cat_scores["B"] == pytest.approx(0.0)

    def test_test_category_scores_computed(self):
        """Per-category scores are computed in test eval when category_key is set."""
        test_items = [
            DataItem(raw_text="", question="q1", expected_answer="a1", metadata={"cat": "X"}),
            DataItem(raw_text="", question="q2", expected_answer="a2", metadata={"cat": "Y"}),
        ]
        dataset = Dataset(
            train=[],
            val=[],
            test=test_items,
            scorer=None,
            category_key="cat",
        )

        mock_evaluator = Mock()
        seed_eval = EvalResult(score=0.5)
        test_eval = EvalResult(score=0.6, per_case_scores=[0.8, 0.4])
        mock_evaluator.evaluate.side_effect = [seed_eval, test_eval]

        mock_strategy = Mock()
        mock_strategy.select.return_value = ([], [])
        mock_strategy.final_eval_data.return_value = None
        mock_strategy.test_eval_data.return_value = ([], test_items)
        mock_strategy.final_candidates.return_value = []

        mock_reflector = Mock()
        mock_reflector.max_fix_attempts = 3

        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=0,
            eval_strategy=mock_strategy,
        )
        state = loop.run()

        assert len(state.test_category_scores) == 1
        program_hash = list(state.test_category_scores.keys())[0]
        cat_scores = state.test_category_scores[program_hash]
        assert cat_scores["X"] == pytest.approx(0.8)
        assert cat_scores["Y"] == pytest.approx(0.4)


class _SplitStrategyStub:
    """Concrete stub strategy with select_reflection_val defined at class level."""

    def select(self, dataset, iteration):
        return (
            [DataItem(raw_text="train", question="", expected_answer="")],
            [DataItem(raw_text="", question="static_q?", expected_answer="static_a")],
        )

    def select_reflection_val(self, dataset, iteration):
        return [DataItem(raw_text="", question="rotate_q?", expected_answer="rotate_a")]

    def final_eval_data(self, dataset):
        return None

    def test_eval_data(self, dataset):
        return None

    def final_candidates(self, pool):
        return []


class TestSplitValidationLoop:
    """Tests for the dual-eval path when strategy has select_reflection_val."""

    def _make_split_strategy(self):
        """Return a strategy stub that has select_reflection_val defined at class level."""
        return _SplitStrategyStub()

    def test_dual_eval_used_when_strategy_has_reflection_val(self):
        """When strategy has select_reflection_val, evaluate_dual is called instead of evaluate."""
        dataset = _make_dataset()
        strategy = self._make_split_strategy()

        evaluator = MagicMock(spec=MemoryEvaluator)
        # Seeds: dual eval returns (score, reflect)
        evaluator.evaluate_dual.return_value = (
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.4, failed_cases=[FailedCase(question="rq", output="ro", expected="re", score=0.0)]),
        )
        reflector = MagicMock(spec=Reflector)
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
            eval_strategy=strategy,
        )
        state = loop.run()

        evaluator.evaluate_dual.assert_called_once()
        evaluator.evaluate.assert_not_called()
        assert state.best_score == 0.5

    def test_reflection_uses_stored_reflection_result(self):
        """Reflector receives the stored reflection_result, not the score eval_result."""
        dataset = _make_dataset()
        strategy = self._make_split_strategy()
        child = KBProgram(source_code="child", generation=1)

        seed_reflect = EvalResult(
            score=0.3,
            failed_cases=[FailedCase(question="rotate_q", output="wrong", expected="right", score=0.0)],
        )
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate_dual.side_effect = [
            (EvalResult(score=0.5), seed_reflect),  # seed
            (EvalResult(score=0.7), EvalResult(score=0.6, failed_cases=[])),  # child
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
            eval_strategy=strategy,
        )
        loop.run()

        # Reflector should have been called with the reflection_result (seed_reflect),
        # not the score result (score=0.5)
        reflect_call = reflector.reflect_and_mutate.call_args
        parent_eval_arg = reflect_call[0][1]  # second positional arg
        assert parent_eval_arg is seed_reflect

    def test_pool_stores_reflection_result(self):
        """PoolEntry should have reflection_result set when dual eval is used."""
        dataset = _make_dataset()
        strategy = self._make_split_strategy()

        reflect_result = EvalResult(score=0.3, failed_cases=[])
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate_dual.return_value = (EvalResult(score=0.5), reflect_result)
        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=0,
            eval_strategy=strategy,
        )
        state = loop.run()

        seed_entry = state.pool.entries[0]
        assert seed_entry.reflection_result is reflect_result
        assert seed_entry.score == 0.5  # score is from score_result

    def test_fallback_to_eval_result_when_no_reflection_result(self):
        """Without select_reflection_val, reflection uses eval_result as before."""
        dataset = _make_dataset()
        child = KBProgram(source_code="child", generation=1)

        seed_eval = EvalResult(
            score=0.5,
            failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)],
        )
        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            seed_eval,
            EvalResult(score=0.8),
        ]
        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = ReflectionResult(program=child)
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
        )
        loop.run()

        # Should use regular evaluate, and reflector gets eval_result
        reflect_call = reflector.reflect_and_mutate.call_args
        parent_eval_arg = reflect_call[0][1]
        assert parent_eval_arg is seed_eval
