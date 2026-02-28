"""Tests for TokenF1Scorer."""

from __future__ import annotations

import pytest

from programmaticmemory.evolution.evaluator import TokenF1Scorer


class TestTokenF1Scorer:
    @pytest.fixture()
    def scorer(self):
        return TokenF1Scorer()

    def test_exact_match(self, scorer):
        assert scorer("hello world", "hello world") == 1.0

    def test_case_insensitive(self, scorer):
        assert scorer("Hello World", "hello world") == 1.0

    def test_article_removal(self, scorer):
        assert scorer("the cat sat on a mat", "cat sat on mat") == 1.0

    def test_partial_overlap(self, scorer):
        # "big red" vs "big blue" → common={"big":1}, p=1/2, r=1/2, F1=0.5
        assert scorer("big red", "big blue") == pytest.approx(0.5)

    def test_no_overlap(self, scorer):
        assert scorer("hello", "world") == 0.0

    def test_both_empty(self, scorer):
        assert scorer("", "") == 1.0

    def test_one_empty(self, scorer):
        assert scorer("hello", "") == 0.0
        assert scorer("", "hello") == 0.0

    def test_punctuation_ignored(self, scorer):
        assert scorer("hello, world!", "hello world") == 1.0

    def test_superset_output(self, scorer):
        # output has extra tokens → precision drops
        # "paris france capital" vs "paris" → common=1, p=1/3, r=1/1, F1=0.5
        assert scorer("paris france capital", "paris") == pytest.approx(0.5)

    def test_subset_output(self, scorer):
        # output missing tokens → recall drops
        # "paris" vs "paris france" → common=1, p=1/1, r=1/2, F1=2/3
        assert scorer("paris", "paris france") == pytest.approx(2 / 3)
