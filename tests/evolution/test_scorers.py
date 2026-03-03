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


class TestConnectionsScorer:
    @pytest.fixture()
    def scorer(self):
        return ConnectionsScorer()

    def test_all_correct(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        assert scorer(output, expected) == 1.0

    def test_all_wrong(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, COIL, HONEYCOMB, BALL\nPLUCK, SPOOL, ORGANISM, MOVIE\nTHREAD, WIND, SOLAR PANEL, SCHOOL\nWAX, WRAP, SPREADSHEET, VITAMIN"
        assert scorer(output, expected) == 0.0

    def test_partial_credit(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        # First two groups correct, last two wrong
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, BALL, SOLAR PANEL, SPREADSHEET\nORGANISM, MOVIE, SCHOOL, VITAMIN"
        assert scorer(output, expected) == 0.5

    def test_case_insensitive(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "laser, pluck, thread, wax\ncoil, spool, wind, wrap\nhoneycomb, organism, solar panel, spreadsheet\nball, movie, school, vitamin"
        assert scorer(output, expected) == 1.0

    def test_order_independent(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        # Same groups but in different line order and word order within groups
        output = "BALL, VITAMIN, MOVIE, SCHOOL\nWRAP, WIND, COIL, SPOOL\nSPREADSHEET, HONEYCOMB, SOLAR PANEL, ORGANISM\nWAX, THREAD, PLUCK, LASER"
        assert scorer(output, expected) == 1.0

    def test_empty_output(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        assert scorer("", expected) == 0.0

    def test_empty_expected(self, scorer):
        assert scorer("LASER, PLUCK, THREAD, WAX", "") == 0.0

    def test_extra_whitespace(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "  LASER ,  PLUCK , THREAD ,  WAX  \n COIL, SPOOL, WIND, WRAP \n HONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET \n BALL, MOVIE, SCHOOL, VITAMIN "
        assert scorer(output, expected) == 1.0

    def test_one_group_correct(self, scorer):
        expected = "LASER, PLUCK, THREAD, WAX\nCOIL, SPOOL, WIND, WRAP\nHONEYCOMB, ORGANISM, SOLAR PANEL, SPREADSHEET\nBALL, MOVIE, SCHOOL, VITAMIN"
        output = "LASER, PLUCK, THREAD, WAX\nCOIL, HONEYCOMB, WIND, BALL\nSPOOL, ORGANISM, SOLAR PANEL, MOVIE\nWRAP, SPREADSHEET, SCHOOL, VITAMIN"
        assert scorer(output, expected) == 0.25
