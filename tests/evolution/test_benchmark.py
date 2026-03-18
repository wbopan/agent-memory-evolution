"""Tests for benchmark datasets."""

import importlib.util
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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

    def test_category_rejects_non_none(self):
        with pytest.raises(ValueError, match="category"):
            load_kv_memory(num_items=3, category="something")

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
    },
    {
        "sample_id": "locomo_test_2",
        "conversation": {
            "speaker_a": "Charlie",
            "speaker_b": "Diana",
            "session_1": [
                {"speaker": "Charlie", "dia_id": "3_1", "text": "Diana, did you see the game?"},
                {"speaker": "Diana", "dia_id": "3_2", "text": "Yes, Lakers won!"},
            ],
            "session_1_date_time": "2023-02-10 20:00",
        },
        "qa": [
            {"question": "Who won the game?", "answer": "Lakers", "category": 1, "evidence": ["3_2"]},
        ],
    },
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
        assert len(ds.train) == 3  # 2 from conv1 + 1 from conv2
        assert all(isinstance(i, DataItem) for i in ds.train)
        assert all(i.raw_text for i in ds.train)
        # Check that all expected sessions are present (order depends on seed shuffle)
        all_text = " ".join(i.raw_text for i in ds.train)
        assert "[2023-01-15 14:30]" in all_text
        assert "Alice: Hey Bob!" in all_text
        assert "[2023-02-10 20:00]" in all_text
        assert "Charlie: Diana, did you see the game?" in all_text

    def test_val_has_qa_pairs(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        assert len(ds.val) == 3  # 2 from conv1 (cat 1,3) + 1 from conv2 (cat 1)
        questions = {v.question for v in ds.val}
        assert "Where did Bob go hiking?" in questions
        assert "Who won the game?" in questions

    def test_category_5_excluded(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        questions = [v.question for v in ds.val]
        assert "Obscure meta question" not in questions

    def test_category_filter(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir, categories=(1,))
        assert len(ds.val) == 2  # 1 from conv1 (cat 1) + 1 from conv2 (cat 1)
        questions = {v.question for v in ds.val}
        assert "Where did Bob go hiking?" in questions
        assert "Who won the game?" in questions

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

    def test_category_filters_to_single_conversation(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds_all = load_locomo(data_dir=locomo_data_dir, category=None)
        ds_cat0 = load_locomo(data_dir=locomo_data_dir, category="0")
        # category="0" should give subset of full dataset
        assert len(ds_cat0.train) < len(ds_all.train) or len(ds_cat0.val) < len(ds_all.val)
        assert len(ds_cat0.train) > 0
        assert len(ds_cat0.val) > 0

    def test_category_out_of_range_raises(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        with pytest.raises(ValueError, match="category"):
            load_locomo(data_dir=locomo_data_dir, category="99")

    def test_category_non_integer_raises(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        with pytest.raises(ValueError, match="conversation index"):
            load_locomo(data_dir=locomo_data_dir, category="abc")

    def test_category_none_returns_all(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir, category=None)
        assert len(ds.train) == 3  # 2 sessions from conv1 + 1 from conv2
        assert len(ds.val) == 3  # 2 QAs from conv1 (cat 1,3) + 1 from conv2 (cat 1)


# ── Mini LoCoMo ──────────────────────────────────────────────────────────────


class TestMiniLoComoBenchmark:
    @pytest.fixture()
    def locomo_data_dir(self, tmp_path):
        dest = tmp_path / "locomo"
        dest.mkdir()
        (dest / "locomo10.json").write_text(json.dumps(_LOCOMO_FIXTURE))
        return tmp_path

    def test_category_rejects_non_none(self, locomo_data_dir):
        from programmaticmemory.benchmarks.mini_locomo import load_mini_locomo

        with pytest.raises(ValueError, match="category"):
            load_mini_locomo(data_dir=locomo_data_dir, category="something")


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

    def test_category_rejects_non_none(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        with pytest.raises(ValueError, match="category"):
            load_tau_bench(data_dir=tau_data_dir, category="something")

    def test_deterministic_with_seed(self, tau_data_dir):
        from programmaticmemory.benchmarks.tau_bench import load_tau_bench

        d1 = load_tau_bench(data_dir=tau_data_dir, seed=42)
        d2 = load_tau_bench(data_dir=tau_data_dir, seed=42)
        assert [i.question for i in d1.train] == [i.question for i in d2.train]
        assert [i.question for i in d1.val] == [i.question for i in d2.val]


# ── ALFWorld ──────────────────────────────────────────────────────────────────


def _make_traj(task_desc: str, pddl_params: dict | None = None, scene: dict | None = None) -> str:
    data = {
        "turk_annotations": {"anns": [{"task_desc": task_desc}]},
    }
    if pddl_params:
        data["pddl_params"] = pddl_params
    if scene:
        data["scene"] = scene
    return json.dumps(data)


def _make_alfworld_fixture(tmp_path: Path) -> Path:
    """Create minimal ALFWorld directory structure with train and valid_unseen splits."""
    json_base = tmp_path / "alfworld" / "json_2.1.1"

    # ── valid_unseen split ──
    val_base = json_base / "valid_unseen"

    # heat task
    heat_dir = val_base / "heat-Egg-None-Microwave-1" / "trial_T0"
    heat_dir.mkdir(parents=True)
    (heat_dir / "traj_data.json").write_text(
        _make_traj("Heat the egg.", pddl_params={"object_target": "egg", "parent_target": "microwave"})
    )
    (heat_dir / "game.tw-pddl").write_text("(define ...)")

    # cool task
    cool_dir = val_base / "cool-Apple-None-Fridge-2" / "trial_T0"
    cool_dir.mkdir(parents=True)
    (cool_dir / "traj_data.json").write_text(
        _make_traj("Cool the apple.", pddl_params={"object_target": "apple", "parent_target": "fridge"})
    )
    (cool_dir / "game.tw-pddl").write_text("(define ...)")

    # pick_and_place
    pick_dir = val_base / "pick_and_place-Book-None-Shelf-3" / "trial_T0"
    pick_dir.mkdir(parents=True)
    (pick_dir / "traj_data.json").write_text(
        _make_traj("Put the book on the shelf.", pddl_params={"object_target": "book", "parent_target": "shelf"})
    )
    (pick_dir / "game.tw-pddl").write_text("(define ...)")

    # look_at_obj_in_light
    look_dir = val_base / "look_at_obj_in_light-Book-None-DeskLamp-4" / "trial_T0"
    look_dir.mkdir(parents=True)
    (look_dir / "traj_data.json").write_text(_make_traj("Look at book under light."))
    (look_dir / "game.tw-pddl").write_text("(define ...)")

    # Unsolvable task (no game.tw-pddl) → should be filtered out
    unsolvable_dir = val_base / "pick_clean_then_place_in_recep-Cup-None-SinkBasin-5" / "trial_T0"
    unsolvable_dir.mkdir(parents=True)
    (unsolvable_dir / "traj_data.json").write_text(_make_traj("Clean the cup."))
    # No game.tw-pddl intentionally

    # ── train split ──
    train_base = json_base / "train"

    train_heat = train_base / "heat-Potato-None-Microwave-10" / "trial_T0"
    train_heat.mkdir(parents=True)
    (train_heat / "traj_data.json").write_text(
        _make_traj(
            "Heat the potato.",
            pddl_params={"object_target": "potato", "parent_target": "microwave"},
            scene={"floor_plan": "FloorPlan1"},
        )
    )
    (train_heat / "game.tw-pddl").write_text("(define ...)")

    train_cool = train_base / "cool-Lettuce-None-Fridge-11" / "trial_T0"
    train_cool.mkdir(parents=True)
    (train_cool / "traj_data.json").write_text(
        _make_traj("Cool the lettuce.", pddl_params={"object_target": "lettuce", "parent_target": "fridge"})
    )
    (train_cool / "game.tw-pddl").write_text("(define ...)")

    return tmp_path


class TestALFWorldTrainingText:
    def test_format_training_text_includes_task_info(self):
        from programmaticmemory.benchmarks.alfworld import _format_training_text

        text = _format_training_text(
            "Put a hot mug in the cabinet.",
            "pick_heat_then_place_in_recep",
            {"pddl_params": {"object_target": "mug", "parent_target": "cabinet"}},
        )
        assert "hot mug" in text
        assert "pick_heat" in text

    def test_format_training_text_includes_pddl_params(self):
        from programmaticmemory.benchmarks.alfworld import _format_training_text

        text = _format_training_text(
            "Heat the egg.",
            "heat",
            {"pddl_params": {"object_target": "egg", "parent_target": "microwave"}},
        )
        assert "object_target" in text
        assert "egg" in text

    def test_format_training_text_includes_scene(self):
        from programmaticmemory.benchmarks.alfworld import _format_training_text

        text = _format_training_text(
            "Cool the apple.",
            "cool",
            {"pddl_params": {}, "scene": {"floor_plan": "FloorPlan7"}},
        )
        assert "FloorPlan7" in text

    def test_format_training_text_no_pddl(self):
        from programmaticmemory.benchmarks.alfworld import _format_training_text

        text = _format_training_text("Look at book under light.", "look_at_obj_in_light", {})
        assert "look_at_obj_in_light" in text
        assert "Look at book under light." in text


class TestALFWorldBenchmark:
    @pytest.fixture(autouse=True)
    def _skip_game_probe(self):
        """Fixture game files are stubs — skip real TextWorld probing."""
        import programmaticmemory.benchmarks.alfworld as _aw

        _aw._VALID_GAME_CACHE.clear()
        with patch("programmaticmemory.benchmarks.alfworld._probe_game_isolated", return_value=True):
            yield
        _aw._VALID_GAME_CACHE.clear()

    @pytest.fixture()
    def alfworld_data_dir(self, tmp_path):
        return _make_alfworld_fixture(tmp_path)

    def test_unsolvable_filtered_out(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import _parse_trials

        base = alfworld_data_dir / "alfworld" / "json_2.1.1" / "valid_unseen"
        typed_items = _parse_trials(base, for_train=False)
        assert len(typed_items) == 4
        # Unsolvable "Clean the cup." should not appear
        questions = [item.question for _, item in typed_items]
        assert "Clean the cup." not in questions

    def test_loads_solvable_tasks(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert isinstance(ds, Dataset)
        assert len(ds.train) == 2
        assert len(ds.val) > 0
        assert len(ds.test) == 0

    def test_train_items_have_raw_text(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        for item in ds.train:
            assert item.raw_text  # non-empty training text
            assert item.question == ""
            assert item.expected_answer == ""

    def test_val_items_have_metadata(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        for item in ds.val:
            assert item.raw_text == ""
            assert item.question  # task objective
            assert item.expected_answer  # non-empty success description
            assert "game_file" in item.metadata
            assert "task_type" in item.metadata

    def test_deterministic_with_seed(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        d1 = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        d2 = load_alfworld(num_train=2, data_dir=alfworld_data_dir, seed=42)
        assert [i.raw_text for i in d1.train] == [i.raw_text for i in d2.train]
        assert [i.question for i in d1.val] == [i.question for i in d2.val]

    def test_category_filters_by_task_type(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=1, data_dir=alfworld_data_dir, category="heat")
        # Should only have heat tasks
        for item in ds.val:
            assert item.metadata["task_type"] == "heat"

    def test_category_none_returns_all(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir, category=None)
        assert len(ds.train) + len(ds.val) > 0

    def test_category_no_match_raises(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        with pytest.raises(ValueError, match="category"):
            load_alfworld(num_train=1, data_dir=alfworld_data_dir, category="nonexistent")

    def test_val_scorer_with_alfworld_installed(self, alfworld_data_dir):
        """When alfworld is importable, val_scorer should be ALFWorldValScorer."""
        import sys
        import unittest.mock

        from programmaticmemory.benchmarks.alfworld import ALFWorldValScorer, load_alfworld

        mock_alfworld = MagicMock()
        with unittest.mock.patch.dict(sys.modules, {"alfworld": mock_alfworld}):
            ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert isinstance(ds.val_scorer, ALFWorldValScorer)

    def test_val_scorer_none_without_alfworld(self, alfworld_data_dir):
        """Without alfworld installed, val_scorer should be None."""
        import sys
        import unittest.mock

        from programmaticmemory.benchmarks.alfworld import load_alfworld

        # Simulate alfworld not being installed
        with unittest.mock.patch.dict(sys.modules, {"alfworld": None}):
            ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert ds.val_scorer is None
        # But scorer should still be set for fallback
        assert ds.scorer is not None

    def test_available_categories(self, alfworld_data_dir):
        from programmaticmemory.benchmarks.alfworld import load_alfworld

        ds = load_alfworld(num_train=2, data_dir=alfworld_data_dir)
        assert ds.available_categories is not None
        assert "heat" in ds.available_categories
        assert "cool" in ds.available_categories


class TestALFWorldValScorer:
    """Tests for ALFWorld episode runner and action selection.

    _run_episode and _select_action are module-level functions (required for
    ProcessPoolExecutor pickling). score_batch tests patch _run_episode at module
    level to avoid spawning real processes.
    """

    @pytest.mark.skipif(
        not importlib.util.find_spec("textworld"),
        reason="textworld not installed (Linux only)",
    )
    def test_run_episode_success(self):
        """_run_episode returns score 1.0 when episode completes with reward."""
        from programmaticmemory.benchmarks.alfworld import _run_episode

        class MockEnv:
            def __init__(self):
                self.step_count = 0

            def reset(self):
                return ("You are in a room.",), ({"admissible_commands": [["go to desk 1", "look"]]},)

            def step(self, action):
                self.step_count += 1
                if self.step_count >= 2:
                    return ("Task complete.",), (1.0,), (True,), ({"admissible_commands": [[]]},)
                return (
                    ("You see a desk.",),
                    (0.0,),
                    (False,),
                    ({"admissible_commands": [["take lamp", "go to shelf 1"]]},),
                )

            def close(self):
                pass

        mock_env = MockEnv()
        with (
            patch("textworld.gym.register_games", return_value="fake-env-id"),
            patch("textworld.gym.make", return_value=mock_env),
            patch("programmaticmemory.benchmarks.alfworld._select_action", side_effect=["go to desk 1", "take lamp"]),
        ):
            transcript, score = _run_episode("/fake/game.tw-pddl", "Find lamp", "tips", "mock/model", 50)

        assert score == 1.0
        assert "ACTION: go to desk 1" in transcript

    @pytest.mark.skipif(
        not importlib.util.find_spec("textworld"),
        reason="textworld not installed (Linux only)",
    )
    def test_run_episode_failure_returns_zero(self):
        """_run_episode returns score 0.0 when max_steps exhausted."""
        from programmaticmemory.benchmarks.alfworld import _run_episode

        class NeverDoneEnv:
            def reset(self):
                return ("Room.",), ({"admissible_commands": [["look"]]},)

            def step(self, action):
                return ("Nothing.",), (0.0,), (False,), ({"admissible_commands": [["look"]]},)

            def close(self):
                pass

        with (
            patch("textworld.gym.register_games", return_value="fake-env-id"),
            patch("textworld.gym.make", return_value=NeverDoneEnv()),
            patch("programmaticmemory.benchmarks.alfworld._select_action", return_value="look"),
        ):
            _transcript, score = _run_episode("/fake/game.tw-pddl", "Do something", "tips", "mock/model", 3)

        assert score == 0.0

    def test_select_action_exact_match(self):
        """_select_action matches LLM output against admissible commands."""
        from programmaticmemory.benchmarks.alfworld import _select_action

        with patch("programmaticmemory.benchmarks.alfworld.litellm") as mock_litellm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "take lamp 1"
            mock_litellm.completion.return_value = mock_resp

            action = _select_action("Find lamp", "tips", "obs1", "", ["take lamp 1", "go to desk 1"], "mock/model")
        assert action == "take lamp 1"

    def test_select_action_substring_fallback(self):
        """Falls back to substring match when exact match fails."""
        from programmaticmemory.benchmarks.alfworld import _select_action

        with patch("programmaticmemory.benchmarks.alfworld.litellm") as mock_litellm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "I'll take lamp 1 from the desk"
            mock_litellm.completion.return_value = mock_resp

            action = _select_action("Find lamp", "tips", "obs1", "", ["take lamp 1", "go to desk 1"], "mock/model")
        assert action == "take lamp 1"

    def test_select_action_fallback_to_first(self):
        """Falls back to first admissible when no match found."""
        from programmaticmemory.benchmarks.alfworld import _select_action

        with patch("programmaticmemory.benchmarks.alfworld.litellm") as mock_litellm:
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "something completely unrelated"
            mock_litellm.completion.return_value = mock_resp

            action = _select_action("Find lamp", "tips", "obs1", "", ["take lamp 1", "go to desk 1"], "mock/model")
        assert action == "take lamp 1"

    def test_parse_action_response_strips_prefix(self):
        """_parse_action_response handles 'Action:' prefixes and case-insensitive matching."""
        from programmaticmemory.benchmarks.alfworld import _parse_action_response

        assert _parse_action_response("Action: go to desk 1", ["go to desk 1", "look"]) == "go to desk 1"
        assert _parse_action_response("GO TO DESK 1", ["go to desk 1", "look"]) == "go to desk 1"
        assert _parse_action_response("", ["go to desk 1", "look"]) == "go to desk 1"
        assert _parse_action_response("random text", ["go to desk 1", "look"]) == "go to desk 1"

    def test_score_batch_dispatches_to_run_episode(self):
        """score_batch dispatches episodes and collects results.

        ProcessPoolExecutor child processes don't see in-process mocks, so we
        mock both the executor (to run synchronously in-process) and _run_episode.
        """
        from programmaticmemory.benchmarks.alfworld import ALFWorldValScorer

        scorer = ALFWorldValScorer(max_steps=50, max_workers=2)
        items = [
            DataItem(raw_text="", question="Do X", expected_answer="", metadata={"game_file": "/fake1"}),
            DataItem(raw_text="", question="Do Y", expected_answer="", metadata={"game_file": "/fake2"}),
        ]
        retrieved = ["tips1", "tips2"]

        # ThreadPoolExecutor doesn't accept mp_context, so use a thin wrapper.
        import concurrent.futures

        class _FakeProcessPool(concurrent.futures.ThreadPoolExecutor):
            def __init__(self, max_workers=None, mp_context=None, **kw):
                super().__init__(max_workers=max_workers, **kw)

        with (
            patch("concurrent.futures.ProcessPoolExecutor", _FakeProcessPool),
            patch("programmaticmemory.benchmarks.alfworld._run_episode", return_value=("transcript", 1.0)) as mock_run,
        ):
            results = scorer.score_batch(items, retrieved, "mock/model", "instruction", "")

        assert len(results) == 2
        assert all(score == 1.0 for _, score in results)
        assert mock_run.call_count == 2

    def test_score_batch_handles_episode_failure(self):
        """score_batch returns zero score for episodes that raise exceptions."""
        from programmaticmemory.benchmarks.alfworld import ALFWorldValScorer

        scorer = ALFWorldValScorer(max_steps=50, max_workers=2)
        items = [
            DataItem(raw_text="", question="Do X", expected_answer="", metadata={"game_file": "/fake1"}),
        ]
        retrieved = ["tips1"]

        import concurrent.futures

        class _FakeProcessPool(concurrent.futures.ThreadPoolExecutor):
            def __init__(self, max_workers=None, mp_context=None, **kw):
                super().__init__(max_workers=max_workers, **kw)

        with (
            patch("concurrent.futures.ProcessPoolExecutor", _FakeProcessPool),
            patch("programmaticmemory.benchmarks.alfworld._run_episode", side_effect=RuntimeError("env crashed")),
        ):
            results = scorer.score_batch(items, retrieved, "mock/model", "instruction", "")

        assert len(results) == 1
        assert results[0][1] == 0.0
        assert "Episode failed" in results[0][0]


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

    def test_category_rejects_non_none(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import load_nyt_connections

        with pytest.raises(ValueError, match="category"):
            load_nyt_connections(data_dir=nyt_data_dir, category="something")

    def test_scorer_is_connections_scorer(self, nyt_data_dir):
        from programmaticmemory.benchmarks.nyt_connections import ConnectionsScorer, load_nyt_connections

        ds = load_nyt_connections(data_dir=nyt_data_dir)
        assert isinstance(ds.scorer, ConnectionsScorer)


# ── load_dataset category parameter ──────────────────────────────────────────


class TestLoadDatasetCategory:
    def test_category_passed_to_loader(self):
        """Category param is forwarded to the benchmark loader."""
        from unittest.mock import MagicMock

        from programmaticmemory.datasets import _CUSTOM_REGISTRY, load_dataset

        mock_loader = MagicMock(return_value=Dataset(train=[], val=[], test=[]))
        _CUSTOM_REGISTRY["_test_cat"] = mock_loader
        try:
            load_dataset("_test_cat", category="heat")
            mock_loader.assert_called_once_with(category="heat")
        finally:
            del _CUSTOM_REGISTRY["_test_cat"]

    def test_category_none_not_passed(self):
        """When category is None, it is still forwarded (loaders handle the default)."""
        from unittest.mock import MagicMock

        from programmaticmemory.datasets import _CUSTOM_REGISTRY, load_dataset

        mock_loader = MagicMock(return_value=Dataset(train=[], val=[], test=[]))
        _CUSTOM_REGISTRY["_test_cat2"] = mock_loader
        try:
            load_dataset("_test_cat2")
            mock_loader.assert_called_once_with(category=None)
        finally:
            del _CUSTOM_REGISTRY["_test_cat2"]


# ── Category metadata ─────────────────────────────────────────────────────────


class TestCategoryMetadata:
    @pytest.fixture()
    def locomo_data_dir(self, tmp_path):
        dest = tmp_path / "locomo"
        dest.mkdir()
        (dest / "locomo10.json").write_text(json.dumps(_LOCOMO_FIXTURE))
        return tmp_path

    def test_locomo_val_items_have_qa_category(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(num_conversations=1, data_dir=locomo_data_dir)
        for item in ds.val:
            assert "qa_category" in item.metadata
            assert item.metadata["qa_category"] in {1, 2, 3, 4}

    def test_dataset_category_key(self, locomo_data_dir):
        from programmaticmemory.benchmarks.locomo import load_locomo

        ds = load_locomo(data_dir=locomo_data_dir)
        assert ds.category_key == "qa_category"
