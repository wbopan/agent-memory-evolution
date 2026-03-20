"""Tests for HealthBench benchmark integration."""

from __future__ import annotations

import json

import pytest

from programmaticmemory.benchmarks import healthbench as _healthbench

_HEALTHBENCH_FIXTURE = [
    {
        "prompt": [
            {"role": "user", "content": "I have a fever and sore throat. What should I do?"},
            {"role": "assistant", "content": "Could you tell me how long you've had these symptoms?"},
            {"role": "user", "content": "About 2 days and I have a headache."},
        ],
        "rubrics": [
            {"criterion": "Mentions possible infection symptoms", "points": 3},
            {"criterion": "Warns to seek urgent care for trouble breathing", "points": 5},
            {"criterion": "Suggests hydration and rest", "points": 4},
        ],
        "example_tags": ["theme:context_seeking", "other:viral"],
        "completion": "This looks like a likely viral illness. Stay hydrated and rest, and seek care if symptoms worsen.",
        "ideal_completions_data": {
            "ideal_completion": "Hydrate, rest, and monitor for trouble breathing or chest pain."
        },
    },
    {
        "prompt": [
            {"role": "user", "content": "I feel chest pain and shortness of breath."},
            {"role": "assistant", "content": "Do you have trouble breathing or sweating?"},
            {"role": "user", "content": "Yes, and I am sweating heavily."},
        ],
        "rubrics": [
            {"criterion": "Recommends emergency care immediately", "points": 6},
            {"criterion": "Mentions calling emergency services", "points": 4},
            {"criterion": "Avoids giving dangerous reassurance", "points": 2},
        ],
        "example_tags": ["theme:emergency_referrals"],
        "completion": "Call 911 or emergency services now.",
        "ideal_completions_data": {
            "ideal_completion": "This is an emergency — call 911 or emergency services immediately."
        },
    },
    {
        "prompt": [
            {"role": "user", "content": "Can I take ibuprofen with food?"},
            {"role": "assistant", "content": "Yes, it's usually safest with food. What dose are you taking?"},
            {"role": "user", "content": "I'll take 200mg as needed."},
        ],
        "rubrics": [
            {"criterion": "Mentions NSAID stomach side effects", "points": 5},
            {"criterion": "Gives a safe adult dosing range", "points": 3},
        ],
        "example_tags": ["theme:medication_safety"],
        "completion": "Yes, take with food and do not exceed label dosing.",
        "ideal_completions_data": None,
    },
]


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """Write a minimal HealthBench cache file and bypass network loading."""
    dest = tmp_path / "healthbench"
    dest.mkdir()
    payload = "\n".join(json.dumps(item, ensure_ascii=False) for item in _HEALTHBENCH_FIXTURE)
    (dest / "healthbench.jsonl").write_text(payload, encoding="utf-8")
    monkeypatch.setattr(_healthbench, "ensure_data", lambda data_dir_arg=None: dest)
    return tmp_path


class TestHealthBench:
    def test_register(self):
        from programmaticmemory.datasets import list_datasets

        assert "healthbench" in list_datasets()

    def test_load_returns_dataset(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("healthbench", data_dir=data_dir)
        assert len(ds.train) > 0
        assert len(ds.val) > 0
        assert ds.val_scorer is not None

    def test_train_has_raw_text(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("healthbench", data_dir=data_dir)
        for item in ds.train:
            assert item.raw_text

    def test_val_has_rubric_in_metadata(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("healthbench", data_dir=data_dir)
        for item in ds.val:
            assert item.expected_answer == ""
            assert "rubric_criteria" in item.metadata
            rubric = item.metadata["rubric_criteria"]
            assert isinstance(rubric, list)

    def test_category_filter(self, data_dir):
        from programmaticmemory.datasets import load_dataset

        ds = load_dataset("healthbench", data_dir=data_dir, category="emergency_referrals")
        assert len(ds.val) > 0
