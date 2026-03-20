"""Tests for scorers (TokenF1Scorer, ConnectionsScorer)."""

from __future__ import annotations

import pytest

from programmaticmemory.benchmarks.nyt_connections import ConnectionsScorer
from programmaticmemory.evolution.evaluator import TokenF1Scorer


class TestTokenF1Scorer:
    @pytest.fixture()
    def scorer(self):
        return TokenF1Scorer()

    def test_exact_match(self, scorer):
        score, rationale = scorer("hello world", "hello world")
        assert score == 1.0
        assert "F1=1.00" in rationale
        assert "Expected answer:" in rationale

    def test_case_insensitive(self, scorer):
        score, rationale = scorer("Hello World", "hello world")
        assert score == 1.0
        assert "F1=1.00" in rationale
        assert "Expected answer:" in rationale

    def test_article_removal(self, scorer):
        score, rationale = scorer("the cat sat on a mat", "cat sat on mat")
        assert score == 1.0
        assert "F1=1.00" in rationale
        assert "precision=" in rationale

    def test_partial_overlap(self, scorer):
        # "big red" vs "big blue" → common={"big":1}, p=1/2, r=1/2, F1=0.5
        score, rationale = scorer("big red", "big blue")
        assert score == pytest.approx(0.5)
        assert "F1=0.50" in rationale

    def test_no_overlap(self, scorer):
        score, rationale = scorer("hello", "world")
        assert score == 0.0
        assert "No word overlap" in rationale

    def test_both_empty(self, scorer):
        score, rationale = scorer("", "")
        assert score == 1.0
        assert "both empty" in rationale

    def test_one_empty(self, scorer):
        score, rationale = scorer("hello", "")
        assert score == 0.0
        assert "one side empty" in rationale
        score, rationale = scorer("", "hello")
        assert score == 0.0
        assert "one side empty" in rationale

    def test_punctuation_ignored(self, scorer):
        score, rationale = scorer("hello, world!", "hello world")
        assert score == 1.0
        assert "F1=1.00" in rationale

    def test_superset_output(self, scorer):
        # output has extra tokens → precision drops
        # "paris france capital" vs "paris" → common=1, p=1/3, r=1/1, F1=0.5
        score, rationale = scorer("paris france capital", "paris")
        assert score == pytest.approx(0.5)
        assert "precision=" in rationale

    def test_subset_output(self, scorer):
        # output missing tokens → recall drops
        # "paris" vs "paris france" → common=1, p=1/1, r=1/2, F1=2/3
        score, rationale = scorer("paris", "paris france")
        assert score == pytest.approx(2 / 3)
        assert "precision=" in rationale
        assert "recall=" in rationale


class TestConnectionsScorer:
    @pytest.fixture()
    def scorer(self):
        return ConnectionsScorer()

    def test_all_correct(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        score, rationale = scorer(output, expected)
        assert score == 1.0
        assert "Matched 4/4" in rationale

    def test_all_wrong(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, COIL, HONEYCOMB, BALL\nPLUCK, SPOOL, ORGANISM, MOVIE\nTHREAD, WIND, SOLAR PANEL, SCHOOL\nWAX, WRAP, SPREADSHEET, VITAMIN"
        score, rationale = scorer(output, expected)
        assert score == 0.0
        assert "Connections puzzle" in rationale
        assert "Matched 0/4" in rationale

    def test_partial_credit(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        # First two groups correct, last two wrong
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, BALL, SOLAR PANEL, SPREADSHEET\nORGANISM, MOVIE, SCHOOL, VITAMIN"
        score, rationale = scorer(output, expected)
        assert score == 0.5
        assert "Matched 2/4" in rationale

    def test_case_insensitive(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "laser, pluck, thread, wax\ncoil, spool, wind, wrap\nhoneycomb, organism, solar panel, spreadsheet\nball, movie, school, vitamin"
        score, rationale = scorer(output, expected)
        assert score == 1.0
        assert "Matched 4/4" in rationale

    def test_order_independent(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        # Same groups but in different line order and word order within groups
        output = "BALL, VITAMIN, MOVIE, SCHOOL\nWRAP, WIND, COIL, SPOOL\nSPREADSHEET, HONEYCOMB, SOLAR PANEL, ORGANISM\nWAX, THREAD, PLUCK, LASER"
        score, rationale = scorer(output, expected)
        assert score == 1.0
        assert "Matched 4/4" in rationale

    def test_empty_output(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        score, rationale = scorer("", expected)
        assert score == 0.0
        assert "Matched 0/4" in rationale

    def test_empty_expected(self, scorer):
        score, rationale = scorer("LASER, PLUCK, THREAD, WAX", "")
        assert score == 0.0
        assert "Connections puzzle" in rationale

    def test_extra_whitespace(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "  LASER ,  PLUCK , THREAD ,  WAX  \n COIL, SPOOL, WIND, WRAP \n HONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET \n BALL, MOVIE, SCHOOL, VITAMIN "
        score, rationale = scorer(output, expected)
        assert score == 1.0
        assert "Matched 4/4" in rationale

    def test_one_group_correct(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, HONEYCOMB, WIND, BALL\nSPOOL, ORGANISM, SOLAR PANEL, MOVIE\nWRAP, SPREADSHEET, SCHOOL, VITAMIN"
        score, rationale = scorer(output, expected)
        assert score == 0.25
        assert "Matched 1/4" in rationale
