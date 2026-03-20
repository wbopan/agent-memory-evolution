"""Tests for PRBench benchmark integration."""

from __future__ import annotations

import json

import pytest

from programmaticmemory.benchmarks import prbench as _prbench

_PRBENCH_FIXTURE = [
    {
        "prompt_0": "Draft an investment memo for acquiring a competitor in a hostile market.",
        "response_0": "Consider valuation, regulatory exposure, and financing constraints.",
        "rubric": [
            {
                "criteria_category": "analysis",
                "criteria_description": "Evaluates synergy potential",
                "weight_class": "important",
            },
            {
                "criteria_category": "analysis",
                "criteria_description": "Discusses financing feasibility",
                "weight_class": "important",
            },
            {
                "criteria_category": "tone",
                "criteria_description": "Provides balanced risk framing",
                "weight_class": "nice_to_have",
            },
        ],
        "field": "finance",
        "topic": "M&A",
        "title": "Acquisition memo",
    },
    {
        "prompt_0": "Summarize the key legal risks in a SaaS terms-of-service update.",
        "response_0": "Highlight indemnity, liability caps, and arbitration clauses.",
        "rubric": [
            {
                "criteria_category": "analysis",
                "criteria_description": "Identifies liability exposure",
                "weight_class": "important",
            },
            {
                "criteria_category": "analysis",
                "criteria_description": "Mentions governing law changes",
                "weight_class": "nice_to_have",
            },
        ],
        "field": "legal",
        "topic": "Contract drafting",
        "title": "TOS legal memo",
    },
    {
        "prompt_0": "Model the financial impact of delaying a product launch by 6 months.",
        "response_0": "Estimate revenue loss, cash runway impact, and communication strategy.",
        "rubric": [
            {
                "criteria_category": "analysis",
                "criteria_description": "Quantifies revenue impact",
                "weight_class": "critical",
            },
            {
                "criteria_category": "analysis",
                "criteria_description": "Considers cash runway effects",
                "weight_class": "important",
            },
        ],
        "field": "finance",
        "topic": "Forecast",
        "title": "Delay model",
    },
    {
        "prompt_0": "Review this contract clause for enforceability in EU jurisdictions.",
        "response_0": "Assess governing law conflict and consumer rights obligations.",
        "rubric": [
            {
                "criteria_category": "analysis",
                "criteria_description": "Checks consumer rights compliance",
                "weight_class": "important",
            },
            {
                "criteria_category": "analysis",
                "criteria_description": "Notes enforcement caveats",
                "weight_class": "nice_to_have",
            },
        ],
        "field": "legal",
        "topic": "EU compliance",
        "title": "Clause review",
    },
]


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Write a minimal PRBench cache file and bypass network loading."""
    dest = tmp_path / "prbench"
    dest.mkdir()
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in _PRBENCH_FIXTURE)
    (dest / "prbench.jsonl").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(_prbench, "ensure_data", lambda data_dir_arg=None: dest)
    return tmp_path


class TestPRBench:
    def test_register(self):
        from programmaticmemory.datasets import list_datasets

        assert "prbench" in list_datasets()

    def test_load_returns_dataset(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("prbench", data_dir=data_dir)
        assert len(ds.train) > 0
        assert len(ds.val) > 0
        assert ds.val_scorer is not None

    def test_category_finance(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("prbench", data_dir=data_dir, category="finance")
        assert len(ds.val) > 0

    def test_category_legal(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("prbench", data_dir=data_dir, category="legal")
        assert len(ds.val) > 0

    def test_val_has_rubric_in_metadata(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("prbench", data_dir=data_dir, category="finance")
        for item in ds.val:
            assert item.expected_answer == ""
            assert "rubric_criteria" in item.metadata
            rubric = item.metadata["rubric_criteria"]
            assert isinstance(rubric, list)
