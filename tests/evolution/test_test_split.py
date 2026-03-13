"""Tests for split_val_test helper and strategy test-eval behaviour."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from programmaticmemory.evolution.__main__ import split_val_test
from programmaticmemory.evolution.strategies import FixedRepresentative, FullDataset, RotatingBatch
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


class TestStrategyFinalEvalTest:
    """Strategy final_eval_data and test_eval_data use dataset.test correctly."""

    def test_full_dataset_final_eval_uses_test(self) -> None:
        """FullDataset.final_eval_data returns (train, test) when test is non-empty, None when empty."""
        strategy = FullDataset()
        ds_no_test = Dataset(train=_make_items(5), val=_make_items(10), test=[])
        assert strategy.final_eval_data(ds_no_test) is None

        test_items = _make_items(3)
        ds_with_test = Dataset(train=_make_items(5), val=_make_items(10), test=test_items)
        result = strategy.final_eval_data(ds_with_test)
        assert result is not None
        assert result == (ds_with_test.train, test_items)

    @patch("programmaticmemory.evolution.strategies.select_representative_subset")
    def test_fixed_representative_final_eval_uses_test(self, mock_select: object) -> None:
        """FixedRepresentative.final_eval_data returns (train, test) when test is non-empty, None when empty."""
        n_train, n_val = 5, 10
        mock_select.return_value = (list(range(n_train)), list(range(n_val)))  # type: ignore[union-attr]

        ds_no_test = Dataset(train=_make_items(n_train), val=_make_items(n_val), test=[])
        strategy = FixedRepresentative(ds_no_test, val_size=n_val)
        assert strategy.final_eval_data(ds_no_test) is None

        test_items = _make_items(4)
        ds_with_test = Dataset(train=_make_items(n_train), val=_make_items(n_val), test=test_items)
        result = strategy.final_eval_data(ds_with_test)
        assert result is not None
        assert result == (ds_with_test.train, test_items)

    def test_rotating_batch_final_eval_unchanged(self) -> None:
        """RotatingBatch.final_eval_data still returns (train, val) for revalidation ranking."""
        from programmaticmemory.evolution.batching import EvalBatch

        batches = [EvalBatch(val_indices=[0, 1], train_indices=[0], coverage=1.0)]
        strategy = RotatingBatch(batches)
        ds = Dataset(train=_make_items(5), val=_make_items(10), test=_make_items(3))
        result = strategy.final_eval_data(ds)
        assert result == (ds.train, ds.val)

    def test_rotating_batch_test_eval_data(self) -> None:
        """RotatingBatch.test_eval_data returns (train, test) when non-empty, None when empty."""
        from programmaticmemory.evolution.batching import EvalBatch

        batches = [EvalBatch(val_indices=[0, 1], train_indices=[0], coverage=1.0)]
        strategy = RotatingBatch(batches)

        ds_no_test = Dataset(train=_make_items(5), val=_make_items(10), test=[])
        assert strategy.test_eval_data(ds_no_test) is None

        test_items = _make_items(3)
        ds_with_test = Dataset(train=_make_items(5), val=_make_items(10), test=test_items)
        result = strategy.test_eval_data(ds_with_test)
        assert result is not None
        assert result == (ds_with_test.train, test_items)

    def test_full_dataset_test_eval_data_none(self) -> None:
        """FullDataset.test_eval_data always returns None."""
        strategy = FullDataset()
        ds = Dataset(train=_make_items(5), val=_make_items(10), test=_make_items(3))
        assert strategy.test_eval_data(ds) is None

    @patch("programmaticmemory.evolution.strategies.select_representative_subset")
    def test_fixed_representative_test_eval_data_none(self, mock_select: object) -> None:
        """FixedRepresentative.test_eval_data always returns None."""
        n_train, n_val = 5, 10
        mock_select.return_value = (list(range(n_train)), list(range(n_val)))  # type: ignore[union-attr]
        ds = Dataset(train=_make_items(n_train), val=_make_items(n_val), test=_make_items(3))
        strategy = FixedRepresentative(ds, val_size=n_val)
        assert strategy.test_eval_data(ds) is None
