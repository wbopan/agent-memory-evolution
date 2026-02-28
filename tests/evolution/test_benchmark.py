"""Tests for benchmarks/kv_memory.py — KV memory benchmark."""

from programmaticmemory.benchmarks.kv_memory import load_kv_memory
from programmaticmemory.evolution.types import DataItem


class TestKVMemoryBenchmark:
    def test_simple_loads(self):
        train, val, test = load_kv_memory(num_items=5, difficulty="simple")
        assert len(train) == 5
        assert len(val) == 5
        assert len(test) == 0

    def test_compound_loads(self):
        train, val, test = load_kv_memory(num_items=3, difficulty="compound")
        assert len(train) == 3
        assert len(val) == 3
        assert len(test) == 0

    def test_items_are_dataitems(self):
        train, _, _ = load_kv_memory(num_items=3)
        for item in train:
            assert isinstance(item, DataItem)
            assert item.raw_text
            assert item.question
            assert item.expected_answer

    def test_deterministic_with_same_seed(self):
        t1, _, _ = load_kv_memory(num_items=5, seed=42)
        t2, _, _ = load_kv_memory(num_items=5, seed=42)
        assert [i.question for i in t1] == [i.question for i in t2]

    def test_different_seed_gives_different_order(self):
        t1, _, _ = load_kv_memory(num_items=10, seed=42)
        t2, _, _ = load_kv_memory(num_items=10, seed=99)
        # Different order (extremely unlikely to be the same)
        q1 = [i.question for i in t1]
        q2 = [i.question for i in t2]
        assert q1 != q2

    def test_max_simple_items(self):
        train, _, _ = load_kv_memory(num_items=20, difficulty="simple")
        assert len(train) == 20

    def test_max_compound_items(self):
        train, _, _ = load_kv_memory(num_items=5, difficulty="compound")
        assert len(train) == 5

    def test_compound_raw_text_combines_facts(self):
        train, _, _ = load_kv_memory(num_items=1, difficulty="compound")
        # Compound items have multi-sentence raw_text
        assert len(train[0].raw_text.split(".")) >= 2

    def test_train_and_val_are_same_for_type_a(self):
        """For Type A, same items serve as both train (ingest) and val (query)."""
        train, val, _ = load_kv_memory(num_items=5)
        assert train == val

    def test_simple_answers_are_concise(self):
        train, _, _ = load_kv_memory(num_items=20, difficulty="simple")
        for item in train:
            # Answers should be concise factual responses
            assert len(item.expected_answer) < 100
