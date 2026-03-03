"""Tests for benchmark datasets."""

import json
from pathlib import Path

import pytest

from programmaticmemory.benchmarks.kv_memory import load_kv_memory
from programmaticmemory.evolution.types import DataItem, Dataset


class TestKVMemoryBenchmark:
    def test_simple_loads(self):
        ds = load_kv_memory(num_items=5, difficulty="simple")
        assert isinstance(ds, Dataset)
        assert len(ds.train) == 5
        assert len(ds.val) == 5
        assert len(ds.test) == 0

    def test_compound_loads(self):
        ds = load_kv_memory(num_items=3, difficulty="compound")
        assert len(ds.train) == 3
        assert len(ds.val) == 3
        assert len(ds.test) == 0

    def test_items_are_dataitems(self):
        ds = load_kv_memory(num_items=3)
        for item in ds.train:
            assert isinstance(item, DataItem)
            assert item.raw_text
            assert item.question
            assert item.expected_answer

    def test_deterministic_with_same_seed(self):
        d1 = load_kv_memory(num_items=5, seed=42)
        d2 = load_kv_memory(num_items=5, seed=42)
        assert [i.question for i in d1.train] == [i.question for i in d2.train]

    def test_different_seed_gives_different_order(self):
        d1 = load_kv_memory(num_items=10, seed=42)
        d2 = load_kv_memory(num_items=10, seed=99)
        q1 = [i.question for i in d1.train]
        q2 = [i.question for i in d2.train]
        assert q1 != q2

    def test_max_simple_items(self):
        ds = load_kv_memory(num_items=20, difficulty="simple")
        assert len(ds.train) == 20

    def test_max_compound_items(self):
        ds = load_kv_memory(num_items=5, difficulty="compound")
        assert len(ds.train) == 5

    def test_compound_raw_text_combines_facts(self):
        ds = load_kv_memory(num_items=1, difficulty="compound")
        assert len(ds.train[0].raw_text.split(".")) >= 2

    def test_train_and_val_are_same_for_offline(self):
        """For offline eval, same items serve as both train (ingest) and val (query)."""
        ds = load_kv_memory(num_items=5)
        assert ds.train == ds.val

    def test_simple_answers_are_concise(self):
        ds = load_kv_memory(num_items=20, difficulty="simple")
        for item in ds.train:
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

        ds = load_locomo(data_dir=locomo_data_dir)
        assert isinstance(ds, Dataset)
        assert len(ds.train) == 2
        assert all(isinstance(i, DataItem) for i in ds.train)
        assert all(i.raw_text for i in ds.train)
        assert "[2023-01-15 14:30]" in ds.train[0].raw_text
        assert "Alice: Hey Bob!" in ds.train[0].raw_text

    def test_val_has_qa_pairs(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        assert len(ds.val) == 2
        assert ds.val[0].question == "Where did Bob go hiking?"
        assert ds.val[0].expected_answer == "Mt. Rainier"

    def test_category_5_excluded(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        questions = [v.question for v in ds.val]
        assert "Obscure meta question" not in questions

    def test_category_filter(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir, categories=(1,))
        assert len(ds.val) == 1
        assert ds.val[0].question == "Where did Bob go hiking?"

    def test_deterministic_with_seed(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        d1 = load_locomo(data_dir=locomo_data_dir, seed=42)
        d2 = load_locomo(data_dir=locomo_data_dir, seed=42)
        assert [i.raw_text for i in d1.train] == [i.raw_text for i in d2.train]
        assert [i.question for i in d1.val] == [i.question for i in d2.val]

    def test_test_set_empty(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        assert ds.test == []


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

        ds = load_tau_bench(data_dir=tau_data_dir, train_ratio=0.7)
        assert isinstance(ds, Dataset)
        assert len(ds.train) + len(ds.val) == 3
        assert len(ds.test) == 0

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

        ds = load_tau_bench(data_dir=tau_data_dir)
        for item in ds.train + ds.val:
            assert item.raw_text == ""

    def test_train_val_non_overlapping(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        ds = load_tau_bench(data_dir=tau_data_dir)
        train_q = {i.question for i in ds.train}
        val_q = {i.question for i in ds.val}
        assert train_q.isdisjoint(val_q)

    def test_deterministic_with_seed(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        d1 = load_tau_bench(data_dir=tau_data_dir, seed=42)
        d2 = load_tau_bench(data_dir=tau_data_dir, seed=42)
        assert [i.question for i in d1.train] == [i.question for i in d2.train]
        assert [i.question for i in d1.val] == [i.question for i in d2.val]


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
        assert len(items) == 4
        questions = [i.question for i in items]
        assert "Clean the cup." not in questions

    def test_loads_solvable_tasks(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert isinstance(ds, Dataset)
        assert len(ds.train) == 2
        assert len(ds.val) == 2
        assert len(ds.test) == 0

    def test_raw_text_empty(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        for item in ds.train + ds.val:
            assert item.raw_text == ""

    def test_deterministic_with_seed(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        d1 = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        d2 = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        assert [i.question for i in d1.train] == [i.question for i in d2.train]


# ── NYT Connections ──────────────────────────────────────────────────────────

_NYT_CONNECTIONS_FIXTURE = [
    {
        "date": "2024/06/03",
        "contest": "NYT Connections 358",
        "words": [
            "LASER",
            "PLUCK",
            "THREAD",
            "WAX",
            "COIL",
            "SPOOL",
            "WIND",
            "WRAP",
            "HONEYCOMB",
            "ORGANISM",
            "SOLAR PANEL",
            "SPREADSHEET",
            "BALL",
            "MOVIE",
            "SCHOOL",
            "VITAMIN",
        ],
        "answers": [
            {"answerDescription": "REMOVE, AS BODY HAIR", "words": ["LASER", "PLUCK", "THREAD", "WAX"]},
            {"answerDescription": "TWIST AROUND", "words": ["COIL", "SPOOL", "WIND", "WRAP"]},
            {
                "answerDescription": "THINGS MADE OF CELLS",
                "words": ["HONEYCOMB", "ORGANISM", "SOLAR PANEL", "SPREADSHEET"],
            },
            {"answerDescription": "B-___", "words": ["BALL", "MOVIE", "SCHOOL", "VITAMIN"]},
        ],
        "difficulty": 3.3,
    },
    {
        "date": "2024/06/02",
        "contest": "NYT Connections 357",
        "words": [
            "FOLLOWERS",
            "LEMMINGS",
            "PUPPETS",
            "SHEEP",
            "BEES",
            "BIRDS",
            "FLOWERS",
            "STARS",
            "BOARD",
            "CARD",
            "VIDEO",
            "WAR",
            "BRIDGE",
            "POKER",
            "RUMMY",
            "SOLITAIRE",
        ],
        "answers": [
            {"answerDescription": "CONFORMISTS", "words": ["FOLLOWERS", "LEMMINGS", "PUPPETS", "SHEEP"]},
            {"answerDescription": "___ AND THE ___", "words": ["BEES", "BIRDS", "FLOWERS", "STARS"]},
            {"answerDescription": "___ GAME", "words": ["BOARD", "CARD", "VIDEO", "WAR"]},
            {"answerDescription": "CARD GAMES", "words": ["BRIDGE", "POKER", "RUMMY", "SOLITAIRE"]},
        ],
        "difficulty": 2.8,
    },
    {
        "date": "2024/06/01",
        "contest": "NYT Connections 356",
        "words": [
            "ANCHOR",
            "HOST",
            "LEAD",
            "STAR",
            "BUTTER",
            "CROW",
            "PEANUT",
            "SCOTCH",
            "BAND",
            "BELT",
            "RING",
            "STRAP",
            "ALMOND",
            "CASHEW",
            "PECAN",
            "WALNUT",
        ],
        "answers": [
            {"answerDescription": "MAIN PERFORMER", "words": ["ANCHOR", "HOST", "LEAD", "STAR"]},
            {"answerDescription": "BAR ___", "words": ["BUTTER", "CROW", "PEANUT", "SCOTCH"]},
            {"answerDescription": "THINGS THAT WRAP AROUND", "words": ["BAND", "BELT", "RING", "STRAP"]},
            {"answerDescription": "TREE NUTS", "words": ["ALMOND", "CASHEW", "PECAN", "WALNUT"]},
        ],
        "difficulty": 2.5,
    },
]


class TestNYTConnectionsBenchmark:
    @pytest.fixture()
    def nyt_data_dir(self, tmp_path):
        dest = tmp_path / "nyt_connections"
        dest.mkdir()
        (dest / "ConnectionsFinalDataset.json").write_text(json.dumps(_NYT_CONNECTIONS_FIXTURE))
        return tmp_path

    def test_loads_correct_count(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir, train_ratio=0.5)
        assert isinstance(ds, Dataset)
        assert len(ds.train) + len(ds.val) == 3
        assert len(ds.test) == 0

    def test_raw_text_empty(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        for item in ds.train + ds.val:
            assert item.raw_text == ""

    def test_question_contains_task_description(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        for item in ds.train + ds.val:
            assert "NYT Connections puzzle" in item.question
            assert "Words:" in item.question
            assert "four groups" in item.question

    def test_question_contains_16_words(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        for item in ds.train + ds.val:
            words_line = item.question.split("Words: ")[1]
            words = [w.strip() for w in words_line.split(",")]
            assert len(words) == 16

    def test_expected_answer_has_4_groups(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        for item in ds.train + ds.val:
            lines = [l for l in item.expected_answer.strip().split("\n") if l.strip()]
            assert len(lines) == 4
            for line in lines:
                words = [w.strip() for w in line.split(",") if w.strip()]
                assert len(words) == 4

    def test_words_are_shuffled_deterministically(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        d1 = load_nyt_connections(data_dir=nyt_data_dir, seed=42)
        d2 = load_nyt_connections(data_dir=nyt_data_dir, seed=42)
        for a, b in zip(d1.train + d1.val, d2.train + d2.val, strict=False):
            assert a.question == b.question

    def test_different_seed_gives_different_order(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        d1 = load_nyt_connections(data_dir=nyt_data_dir, seed=42)
        d2 = load_nyt_connections(data_dir=nyt_data_dir, seed=99)
        q1 = [i.question for i in d1.train]
        q2 = [i.question for i in d2.train]
        assert q1 != q2

    def test_train_val_non_overlapping(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        train_q = {i.question for i in ds.train}
        val_q = {i.question for i in ds.val}
        assert train_q.isdisjoint(val_q)

    def test_scorer_is_connections_scorer(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import ConnectionsScorer, load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        assert isinstance(ds.scorer, ConnectionsScorer)
