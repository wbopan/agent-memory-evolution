"""Tests for split_val_test helper and strategy test-eval behaviour."""

from __future__ import annotations

import pytest

from programmaticmemory.evolution.__main__ import split_val_test
from programmaticmemory.evolution.types import DataItem, Dataset


def _make_items(n: int) -> list[DataItem]:
    return [DataItem(raw_text=f"text_{i}", question=f"q_{i}", expected_answer=f"a_{i}") for i in range(n)]


def _make_dataset(n_train: int = 5, n_val: int = 10) -> Dataset:
    return Dataset(train=_make_items(n_train), val=_make_items(n_val), test=[])


class TestSplitValTest:
    def test_split_default_minus1(self) -> None:
        """test_size=-1: test == copy of val, val unchanged."""
        ds = _make_dataset(n_val=10)
        original_val = list(ds.val)
        split_val_test(ds, test_size=-1, seed=42)
        assert ds.val == original_val
        assert ds.test == original_val
        # Must be a copy, not the same object
        assert ds.test is not ds.val

    def test_split_zero(self) -> None:
        """test_size=0: test == [], val unchanged."""
        ds = _make_dataset(n_val=10)
        original_val = list(ds.val)
        split_val_test(ds, test_size=0, seed=42)
        assert ds.val == original_val
        assert ds.test == []

    def test_split_positive(self) -> None:
        """test_size=N: test has N items, val has len(val)-N, no overlap."""
        ds = _make_dataset(n_val=10)
        original_val = {item.question for item in ds.val}
        split_val_test(ds, test_size=3, seed=42)
        assert len(ds.test) == 3
        assert len(ds.val) == 7
        # No overlap
        val_qs = {item.question for item in ds.val}
        test_qs = {item.question for item in ds.test}
        assert val_qs & test_qs == set()
        # Union equals original
        assert val_qs | test_qs == original_val

    def test_split_deterministic(self) -> None:
        """Same seed produces same split."""
        ds1 = _make_dataset(n_val=20)
        ds2 = _make_dataset(n_val=20)
        split_val_test(ds1, test_size=5, seed=123)
        split_val_test(ds2, test_size=5, seed=123)
        assert ds1.val == ds2.val
        assert ds1.test == ds2.test

    def test_split_copies_list(self) -> None:
        """When train and val are the same list object, split doesn't corrupt train."""
        shared = _make_items(10)
        ds = Dataset(train=shared, val=shared, test=[])
        split_val_test(ds, test_size=3, seed=42)
        # train must still have all 10 items (untouched)
        assert len(ds.train) == 10
        # val + test should partition the original val
        assert len(ds.val) == 7
        assert len(ds.test) == 3

    def test_split_rejects_too_large(self) -> None:
        """test_size >= len(val) should error (would leave val empty)."""
        ds = _make_dataset(n_val=5)
        with pytest.raises(SystemExit):
            split_val_test(ds, test_size=5, seed=42)
        ds2 = _make_dataset(n_val=5)
        with pytest.raises(SystemExit):
            split_val_test(ds2, test_size=10, seed=42)

    def test_split_rejects_invalid_negative(self) -> None:
        """test_size=-2 should error."""
        ds = _make_dataset(n_val=10)
        with pytest.raises(SystemExit):
            split_val_test(ds, test_size=-2, seed=42)



class TestLoopTestEval:
    """Tests for two-stage test evaluation in the evolution loop."""

    def test_loop_runs_test_eval_when_test_eval_data_returns_data(self) -> None:
        """When test_eval_data returns data, the best program is evaluated on it."""
        from unittest.mock import MagicMock

        from programmaticmemory.evolution.loop import EvolutionLoop
        from programmaticmemory.evolution.types import EvalResult

        train = _make_items(3)
        val = _make_items(5)
        test = _make_items(4)
        ds = Dataset(train=train, val=val, test=test)

        class TestStrategy:
            def select(self, dataset: Dataset, iteration: int) -> tuple:
                return dataset.train, dataset.val

            def final_candidates(self, pool: object) -> list:
                return []

            def final_eval_data(self, dataset: Dataset) -> None:
                return None

            def test_eval_data(self, dataset: Dataset) -> tuple | None:
                if dataset.test:
                    return dataset.train, dataset.test
                return None

        evaluator = MagicMock()
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.6),  # seed eval
            EvalResult(score=0.9),  # test eval
        ]
        reflector = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=0,
            eval_strategy=TestStrategy(),
        )
        state = loop.run()

        # Should have called evaluate twice: seed + test
        assert evaluator.evaluate.call_count == 2
        # Test eval call should use train + test data
        test_call = evaluator.evaluate.call_args_list[1]
        assert test_call[0][1] == train
        assert test_call[0][2] == test
        # test_scores should be populated
        assert len(state.test_scores) == 1
        assert next(iter(state.test_scores.values())) == 0.9

    def test_loop_skips_test_eval_when_test_eval_data_returns_none(self) -> None:
        """When test_eval_data returns None, no test evaluation happens."""
        from unittest.mock import MagicMock

        from programmaticmemory.evolution.loop import EvolutionLoop
        from programmaticmemory.evolution.types import EvalResult

        ds = Dataset(train=_make_items(3), val=_make_items(5), test=[])

        class _NoTestStrategy:
            def select(self, dataset, iteration):
                return dataset.train, dataset.val

            def final_candidates(self, pool):
                return [pool.best]

            def final_eval_data(self, dataset):
                return None

            def test_eval_data(self, dataset):
                return None

        evaluator = MagicMock()
        evaluator.evaluate.return_value = EvalResult(score=0.5)
        reflector = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            max_iterations=0,
            eval_strategy=_NoTestStrategy(),
        )
        state = loop.run()

        # Only seed eval, no test eval
        assert evaluator.evaluate.call_count == 1
        assert state.test_scores == {}

    def test_loop_test_eval_uses_final_scores_winner(self) -> None:
        """When final_scores exist, test eval picks the best from final_scores."""
        from unittest.mock import MagicMock

        from programmaticmemory.evolution.loop import EvolutionLoop
        from programmaticmemory.evolution.types import EvalResult, KBProgram

        train = _make_items(3)
        val = _make_items(5)
        test = _make_items(4)
        ds = Dataset(train=train, val=val, test=test)

        seed1 = KBProgram(source_code="seed1_code")
        seed2 = KBProgram(source_code="seed2_code")

        class TestStrategy:
            def select(self, dataset: Dataset, iteration: int) -> tuple:
                return dataset.train, dataset.val

            def final_candidates(self, pool: object) -> list:
                return pool.entries  # type: ignore[attr-defined]

            def final_eval_data(self, dataset: Dataset) -> tuple | None:
                return dataset.train, dataset.val

            def test_eval_data(self, dataset: Dataset) -> tuple | None:
                if dataset.test:
                    return dataset.train, dataset.test
                return None

        evaluator = MagicMock()
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.4),  # seed1 eval
            EvalResult(score=0.6),  # seed2 eval
            EvalResult(score=0.3),  # final eval seed1
            EvalResult(score=0.9),  # final eval seed2 (winner)
            EvalResult(score=0.85),  # test eval on seed2 (final_scores winner)
        ]
        reflector = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=ds,
            initial_programs=[seed1, seed2],
            max_iterations=0,
            eval_strategy=TestStrategy(),
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 5
        # Test scores should contain seed2's hash (best final_scores)
        assert seed2.hash in state.test_scores
        assert state.test_scores[seed2.hash] == 0.85
