"""Tests for benchmark datasets."""

import json
from pathlib import Path

import pytest

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


# ── LoCoMo ────────────────────────────────────────────────────────────────────


_LOCOMO_FIXTURE = [
    {
        "sample_id": "locomo_test_1",
        "conversation": {
            "speaker_a": "Alice",
            "speaker_b": "Bob",
            "session_1": [
                {"speaker": "Alice", "dia_id": "1_1", "text": "Hey Bob!"},
                {"speaker": "Bob", "dia_id": "1_2", "text": "Hi Alice!"},
            ],
            "session_1_date_time": "2023-01-15 14:30",
            "session_2": [
                {"speaker": "Alice", "dia_id": "2_1", "text": "How was your weekend?"},
                {"speaker": "Bob", "dia_id": "2_2", "text": "I went hiking at Mt. Rainier."},
            ],
            "session_2_date_time": "2023-01-22 10:00",
        },
        "qa": [
            {"question": "Where did Bob go hiking?", "answer": "Mt. Rainier", "category": 1, "evidence": ["2_2"]},
            {"question": "What greeting did Alice use?", "answer": "Hey Bob!", "category": 3, "evidence": ["1_1"]},
            {"question": "Obscure meta question", "answer": "N/A", "category": 5, "evidence": []},
        ],
    }
]


class TestLoComoBenchmark:
    @pytest.fixture()
    def locomo_data_dir(self, tmp_path):
        dest = tmp_path / "locomo"
        dest.mkdir()
        (dest / "locomo10.json").write_text(json.dumps(_LOCOMO_FIXTURE))
        return tmp_path

    def test_train_has_sessions(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        train, val, test = load_locomo(data_dir=locomo_data_dir)
        # 2 sessions → 2 train items
        assert len(train) == 2
        assert all(isinstance(i, DataItem) for i in train)
        assert all(i.raw_text for i in train)
        assert "[2023-01-15 14:30]" in train[0].raw_text
        assert "Alice: Hey Bob!" in train[0].raw_text

    def test_val_has_qa_pairs(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        _, val, _ = load_locomo(data_dir=locomo_data_dir)
        # 2 QAs with category 1,3 (category 5 excluded)
        assert len(val) == 2
        assert val[0].question == "Where did Bob go hiking?"
        assert val[0].expected_answer == "Mt. Rainier"

    def test_category_5_excluded(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        _, val, _ = load_locomo(data_dir=locomo_data_dir)
        questions = [v.question for v in val]
        assert "Obscure meta question" not in questions

    def test_category_filter(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        _, val, _ = load_locomo(data_dir=locomo_data_dir, categories=(1,))
        assert len(val) == 1
        assert val[0].question == "Where did Bob go hiking?"

    def test_deterministic_with_seed(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        t1, v1, _ = load_locomo(data_dir=locomo_data_dir, seed=42)
        t2, v2, _ = load_locomo(data_dir=locomo_data_dir, seed=42)
        assert [i.raw_text for i in t1] == [i.raw_text for i in t2]
        assert [i.question for i in v1] == [i.question for i in v2]

    def test_test_set_empty(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        _, _, test = load_locomo(data_dir=locomo_data_dir)
        assert test == []


# ── tau-bench ─────────────────────────────────────────────────────────────────

_TAU_BENCH_TASKS_PY = """
tasks = [
    {
        "instruction": "Find the order status for order 12345",
        "actions": [{"name": "get_order_details", "kwargs": {"order_id": "12345"}}],
        "outputs": ["Order 12345 is currently being shipped"],
    },
    {
        "instruction": "Cancel order 67890",
        "actions": [{"name": "cancel_order", "kwargs": {"order_id": "67890"}}],
        "outputs": [],
    },
    {
        "instruction": "Update shipping address for order 11111",
        "actions": [
            {"name": "get_order_details", "kwargs": {"order_id": "11111"}},
            {"name": "update_shipping", "kwargs": {"address": "123 Main St"}},
        ],
        "outputs": ["Address updated successfully"],
    },
]
"""


class TestTauBenchBenchmark:
    @pytest.fixture()
    def tau_data_dir(self, tmp_path):
        dest = tmp_path / "tau_bench" / "retail"
        dest.mkdir(parents=True)
        (dest / "tasks.py").write_text(_TAU_BENCH_TASKS_PY)
        return tmp_path

    def test_loads_correct_count(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        train, val, test = load_tau_bench(data_dir=tau_data_dir, train_ratio=0.7)
        assert len(train) + len(val) == 3
        assert len(test) == 0

    def test_expected_from_outputs(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import _derive_expected

        task = {"outputs": ["Order 12345 is currently being shipped"], "actions": []}
        assert _derive_expected(task) == "Order 12345 is currently being shipped"

    def test_expected_from_last_action(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import _derive_expected

        task = {"outputs": [], "actions": [{"name": "cancel_order"}]}
        assert _derive_expected(task) == "cancel_order"

    def test_raw_text_empty(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        train, val, _ = load_tau_bench(data_dir=tau_data_dir)
        for item in train + val:
            assert item.raw_text == ""

    def test_train_val_non_overlapping(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        train, val, _ = load_tau_bench(data_dir=tau_data_dir)
        train_q = {i.question for i in train}
        val_q = {i.question for i in val}
        assert train_q.isdisjoint(val_q)

    def test_deterministic_with_seed(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        t1, v1, _ = load_tau_bench(data_dir=tau_data_dir, seed=42)
        t2, v2, _ = load_tau_bench(data_dir=tau_data_dir, seed=42)
        assert [i.question for i in t1] == [i.question for i in t2]
        assert [i.question for i in v1] == [i.question for i in v2]


# ── ALFWorld ──────────────────────────────────────────────────────────────────


def _make_traj(task_desc: str, pddl_params: dict | None = None) -> str:
    data = {
        "turk_annotations": {"anns": [{"task_desc": task_desc}]},
    }
    if pddl_params:
        data["pddl_params"] = pddl_params
    return json.dumps(data)


def _make_alfworld_fixture(tmp_path: Path) -> Path:
    """Create minimal ALFWorld directory structure."""
    base = tmp_path / "alfworld" / "json_2.1.1" / "valid_unseen"

    # heat task → microwave
    heat_dir = base / "heat-Egg-None-Microwave-1" / "trial_T0"
    heat_dir.mkdir(parents=True)
    (heat_dir / "traj_data.json").write_text(_make_traj("Heat the egg."))
    (heat_dir / "game.tw-pddl").write_text("(define ...)")

    # cool task → fridge
    cool_dir = base / "cool-Apple-None-Fridge-2" / "trial_T0"
    cool_dir.mkdir(parents=True)
    (cool_dir / "traj_data.json").write_text(_make_traj("Cool the apple."))
    (cool_dir / "game.tw-pddl").write_text("(define ...)")

    # pick_and_place → parent_target
    pick_dir = base / "pick_and_place-Book-None-Shelf-3" / "trial_T0"
    pick_dir.mkdir(parents=True)
    (pick_dir / "traj_data.json").write_text(
        _make_traj("Put the book on the shelf.", pddl_params={"parent_target": "shelf"})
    )
    (pick_dir / "game.tw-pddl").write_text("(define ...)")

    # look_at_obj_in_light → desklamp
    look_dir = base / "look_at_obj_in_light-Book-None-DeskLamp-4" / "trial_T0"
    look_dir.mkdir(parents=True)
    (look_dir / "traj_data.json").write_text(_make_traj("Look at book under light."))
    (look_dir / "game.tw-pddl").write_text("(define ...)")

    # Unsolvable task (no game.tw-pddl) → should be filtered out
    unsolvable_dir = base / "pick_clean_then_place_in_recep-Cup-None-SinkBasin-5" / "trial_T0"
    unsolvable_dir.mkdir(parents=True)
    (unsolvable_dir / "traj_data.json").write_text(_make_traj("Clean the cup."))
    # No game.tw-pddl intentionally

    return tmp_path


class TestALFWorldBenchmark:
    @pytest.fixture()
    def alfworld_data_dir(self, tmp_path):
        return _make_alfworld_fixture(tmp_path)

    def test_heat_maps_to_microwave(self):
        from programmaticmemory.benchmarks.alfworld import _derive_expected

        assert _derive_expected("heat", {}) == "microwave"

    def test_cool_maps_to_fridge(self):
        from programmaticmemory.benchmarks.alfworld import _derive_expected

        assert _derive_expected("cool", {}) == "fridge"

    def test_pick_and_place_maps_to_parent_target(self):
        from programmaticmemory.benchmarks.alfworld import _derive_expected

        assert _derive_expected("pick_and_place", {"pddl_params": {"parent_target": "shelf"}}) == "shelf"

    def test_look_at_maps_to_desklamp(self):
        from programmaticmemory.benchmarks.alfworld import _derive_expected

        assert _derive_expected("look_at_obj_in_light", {}) == "desklamp"

    def test_unsolvable_filtered_out(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import _parse_trials

        base = alfworld_data_dir / "alfworld" / "json_2.1.1" / "valid_unseen"
        items = _parse_trials(base)
        # 4 solvable, 1 unsolvable filtered
        assert len(items) == 4
        questions = [i.question for i in items]
        assert "Clean the cup." not in questions

    def test_loads_solvable_tasks(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        train, val, test = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert len(train) == 2
        assert len(val) == 2
        assert len(test) == 0

    def test_raw_text_empty(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        train, val, _ = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        for item in train + val:
            assert item.raw_text == ""

    def test_deterministic_with_seed(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        t1, _, _ = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        t2, _, _ = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        assert [i.question for i in t1] == [i.question for i in t2]
