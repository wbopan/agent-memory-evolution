"""Tests for the WebArena benchmark core module."""

from __future__ import annotations

import inspect
import io
import json
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import ClassVar
from unittest.mock import MagicMock, patch

from programmaticmemory.benchmarks.webarena import (
    PLAYWRIGHT_TO_BROWSERGYM,
    WebArenaValScorer,
    format_trajectory_step,
    load_webarena,
    parse_selector,
    parse_trace_zip,
    trace_event_to_action,
)


class TestFormatTrajectoryStep:
    def test_basic_format(self):
        result = format_trajectory_step(1, 'click(button "Submit")', "Page loaded.")
        assert result == 'Step 1: click(button "Submit")\nObservation: Page loaded.'

    def test_empty_observation(self):
        result = format_trajectory_step(3, "go_back()", "")
        assert result == "Step 3: go_back()\nObservation: "

    def test_multiline_observation(self):
        obs = "Line 1\nLine 2\nLine 3"
        result = format_trajectory_step(2, 'goto("https://example.com")', obs)
        assert result == f'Step 2: goto("https://example.com")\nObservation: {obs}'

    def test_step_number_in_output(self):
        result = format_trajectory_step(42, "tab_close()", "closed")
        assert result.startswith("Step 42:")


class TestPlaywrightToBrowserGymMapping:
    # Agent-only BrowserGym actions (e.g. send_msg_to_user) do NOT appear in
    # Playwright traces, so we only expect the 10 recordable actions below.
    _EXPECTED_ACTION_NAMES: ClassVar[set[str]] = {
        "click",
        "fill",
        "hover",
        "select_option",
        "goto",
        "go_back",
        "go_forward",
        "keyboard_press",
        "scroll",
        "tab_close",
    }

    def test_all_recordable_actions_covered(self):
        covered = set(PLAYWRIGHT_TO_BROWSERGYM.values())
        assert covered == self._EXPECTED_ACTION_NAMES

    def test_frame_click(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "click")] == "click"

    def test_frame_fill(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "fill")] == "fill"

    def test_frame_type_maps_to_fill(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "type")] == "fill"

    def test_frame_hover(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "hover")] == "hover"

    def test_frame_select_option(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "selectOption")] == "select_option"

    def test_frame_goto(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "goto")] == "goto"

    def test_frame_go_back(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "goBack")] == "go_back"

    def test_frame_go_forward(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Frame", "goForward")] == "go_forward"

    def test_keyboard_press(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Keyboard", "press")] == "keyboard_press"

    def test_mouse_wheel_scroll(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Mouse", "wheel")] == "scroll"

    def test_page_close_tab_close(self):
        assert PLAYWRIGHT_TO_BROWSERGYM[("Page", "close")] == "tab_close"


class TestParseSelector:
    def test_role_with_name(self):
        assert parse_selector('internal:role=button[name="Submit"]') == 'button "Submit"'

    def test_role_only(self):
        assert parse_selector("internal:role=textbox") == "textbox"

    def test_text_selector(self):
        assert parse_selector('internal:text="Sign in"i') == '"Sign in"'

    def test_text_selector_without_case_flag(self):
        assert parse_selector('internal:text="Hello"') == '"Hello"'

    def test_label_selector(self):
        assert parse_selector('internal:label="Email"') == '"Email"'

    def test_css_selector(self):
        assert parse_selector("css=#search") == "#search"

    def test_xpath_selector(self):
        assert parse_selector("xpath=//div") == "//div"

    def test_chained_takes_first(self):
        result = parse_selector('internal:role=button[name="OK"] >> internal:text="OK"')
        assert result == 'button "OK"'

    def test_empty_string(self):
        assert parse_selector("") == ""


class TestTraceEventToAction:
    def _make_event(self, api_name: str, params: dict | None = None) -> dict:
        return {"type": "before", "apiName": api_name, "params": params or {}, "startTime": 1000.0}

    def test_click(self):
        event = self._make_event("Frame.click", {"selector": 'internal:role=button[name="Login"]'})
        result = trace_event_to_action(event)
        assert result == 'click(button "Login")'

    def test_fill(self):
        event = self._make_event("Frame.fill", {"selector": "internal:role=textbox", "value": "hello@example.com"})
        result = trace_event_to_action(event)
        assert result == 'fill(textbox, "hello@example.com")'

    def test_goto(self):
        event = self._make_event("Frame.goto", {"url": "https://example.com"})
        result = trace_event_to_action(event)
        assert result == 'goto("https://example.com")'

    def test_keyboard_press_frame(self):
        event = self._make_event("Frame.press", {"selector": "css=body", "key": "Enter"})
        result = trace_event_to_action(event)
        assert result == 'keyboard_press("Enter")'

    def test_keyboard_press_keyboard(self):
        event = self._make_event("Keyboard.press", {"key": "Tab"})
        result = trace_event_to_action(event)
        assert result == 'keyboard_press("Tab")'

    def test_select_option_string_list(self):
        event = self._make_event("Frame.selectOption", {"selector": "css=#dropdown", "options": ["value1"]})
        result = trace_event_to_action(event)
        assert result == 'select_option(#dropdown, "value1")'

    def test_select_option_dict_list(self):
        event = self._make_event("Frame.selectOption", {"selector": "css=#dropdown", "options": [{"value": "opt2"}]})
        result = trace_event_to_action(event)
        assert result == 'select_option(#dropdown, "opt2")'

    def test_scroll(self):
        event = self._make_event("Mouse.wheel", {"deltaX": 0, "deltaY": 300})
        result = trace_event_to_action(event)
        assert result == "scroll(0, 300)"

    def test_hover(self):
        event = self._make_event("Frame.hover", {"selector": 'internal:role=link[name="Menu"]'})
        result = trace_event_to_action(event)
        assert result == 'hover(link "Menu")'

    def test_go_back(self):
        event = self._make_event("Frame.goBack", {})
        result = trace_event_to_action(event)
        assert result == "go_back()"

    def test_unknown_method_returns_none(self):
        event = self._make_event("Frame.evaluate", {"expression": "document.title"})
        result = trace_event_to_action(event)
        assert result is None

    def test_missing_api_name_returns_none(self):
        result = trace_event_to_action({"type": "before", "params": {}})
        assert result is None

    def test_no_dot_in_api_name_returns_none(self):
        result = trace_event_to_action({"type": "before", "apiName": "something", "params": {}})
        assert result is None


class TestParseTraceZip:
    def _make_trace_zip(self, events: list[dict]) -> bytes:
        """Build an in-memory Playwright trace zip with the given NDJSON events."""
        ndjson = "\n".join(json.dumps(e) for e in events)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("trace.trace", ndjson)
        return buf.getvalue()

    def _write_zip(self, tmp_path, data: bytes):
        p = tmp_path / "trace.zip"
        p.write_bytes(data)
        return p

    def test_parses_correct_steps(self, tmp_path):
        events = [
            {
                "type": "before",
                "apiName": "Frame.goto",
                "params": {"url": "https://example.com"},
                "startTime": 1000.0,
                "title": 'Navigate to "https://example.com"',
            },
            {
                "type": "before",
                "apiName": "Frame.click",
                "params": {"selector": 'internal:role=button[name="OK"]'},
                "startTime": 2000.0,
                "title": "Click",
            },
            # Non-action event should be ignored
            {
                "type": "after",
                "apiName": "Frame.goto",
                "params": {},
                "startTime": 1500.0,
            },
            # Unknown action should be skipped
            {
                "type": "before",
                "apiName": "Frame.evaluate",
                "params": {"expression": "1+1"},
                "startTime": 3000.0,
            },
        ]
        zip_bytes = self._make_trace_zip(events)
        zip_path = self._write_zip(tmp_path, zip_bytes)

        steps = parse_trace_zip(zip_path)

        assert len(steps) == 2
        assert steps[0]["step_num"] == 1
        assert steps[0]["action"] == 'goto("https://example.com")'
        assert steps[0]["observation"] == "https://example.com"
        assert steps[1]["step_num"] == 2
        assert steps[1]["action"] == 'click(button "OK")'
        assert steps[1]["observation"] == "https://example.com"

    def test_steps_sorted_by_start_time(self, tmp_path):
        events = [
            {
                "type": "before",
                "apiName": "Frame.click",
                "params": {"selector": 'internal:role=button[name="B"]'},
                "startTime": 5000.0,
            },
            {
                "type": "before",
                "apiName": "Frame.goto",
                "params": {"url": "https://first.com"},
                "startTime": 1000.0,
            },
        ]
        zip_bytes = self._make_trace_zip(events)
        zip_path = self._write_zip(tmp_path, zip_bytes)

        steps = parse_trace_zip(zip_path)

        assert len(steps) == 2
        assert steps[0]["action"] == 'goto("https://first.com")'
        assert steps[1]["action"] == 'click(button "B")'

    def test_empty_zip_returns_empty_list(self, tmp_path):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w"):
            pass  # empty zip — no trace.trace file
        zip_path = tmp_path / "empty.zip"
        zip_path.write_bytes(buf.getvalue())

        steps = parse_trace_zip(zip_path)
        assert steps == []

    def test_no_recordable_events_returns_empty_list(self, tmp_path):
        events = [
            {
                "type": "before",
                "apiName": "Frame.evaluate",
                "params": {},
                "startTime": 1000.0,
            },
        ]
        zip_bytes = self._make_trace_zip(events)
        zip_path = self._write_zip(tmp_path, zip_bytes)

        steps = parse_trace_zip(zip_path)
        assert steps == []


# ---------------------------------------------------------------------------
# Dataset loader tests
# ---------------------------------------------------------------------------

_FAKE_TRACES = [
    {"task_id": 1, "trajectory": 'Step 1: goto("https://shop.example.com")\nObservation: Homepage loaded.'},
    {"task_id": 2, "trajectory": 'Step 1: click(button "Add to cart")\nObservation: Cart updated.'},
]

_FAKE_TASK_CONFIGS = [
    {"task_id": 1, "sites": ["shopping"], "intent": "Find a red shirt", "eval": {}},
    {"task_id": 2, "sites": ["shopping"], "intent": "Buy blue jeans", "eval": {}},
    {"task_id": 3, "sites": ["reddit"], "intent": "Post a comment", "eval": {}},
    {"task_id": 4, "sites": ["map"], "intent": "Find directions", "eval": {}},
    {"task_id": 5, "sites": ["wikipedia"], "intent": "Look up Python", "eval": {}},
]


class TestLoadWebarena:
    def _patch_both(self):
        """Return a context manager that patches _load_traces, _load_task_configs, and _HAS_BROWSERGYM."""
        return (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        )

    def test_registration(self):
        """load_dataset('webarena') should resolve to load_webarena."""
        from programmaticmemory.datasets import load_dataset

        with (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        ):
            ds = load_dataset("webarena")
        assert ds is not None

    def test_train_items_have_raw_text(self):
        """Tasks with traces should become train items with non-empty raw_text."""
        with (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        ):
            ds = load_webarena()
        assert len(ds.train) == 2
        for item in ds.train:
            assert item.raw_text != ""

    def test_val_items_have_empty_raw_text_and_metadata(self):
        """Tasks without traces become val items with raw_text='' and metadata populated."""
        with (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        ):
            ds = load_webarena()
        # task_id 3 (reddit) has no trace → val item
        assert len(ds.val) == 1
        item = ds.val[0]
        assert item.raw_text == ""
        assert "task_id" in item.metadata
        assert "sites" in item.metadata
        assert "task_id" in item.metadata

    def test_category_filter(self):
        """category='reddit' should return only reddit tasks."""
        with (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        ):
            ds = load_webarena(category="reddit")
        # task_id 3 (reddit, no trace) → val only
        assert len(ds.train) == 0
        assert len(ds.val) == 1
        assert ds.val[0].metadata["task_id"] == 3

    def test_excluded_sites_filtered(self):
        """Tasks touching excluded sites (map, wikipedia) should not appear."""
        with (
            patch("programmaticmemory.benchmarks.webarena._load_traces", return_value=_FAKE_TRACES),
            patch("programmaticmemory.benchmarks.webarena._load_task_configs", return_value=_FAKE_TASK_CONFIGS),
            patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", True),
        ):
            ds = load_webarena()
        all_items = ds.train + ds.val
        for item in all_items:
            sites = item.metadata["sites"]
            assert not any(s in {"map", "wikipedia"} for s in sites), f"Excluded site found in {sites}"

    def test_raises_import_error_when_no_browsergym(self):
        """Should raise ImportError when BrowserGym is not installed."""
        import pytest

        with patch("programmaticmemory.benchmarks.webarena._HAS_BROWSERGYM", False):
            with pytest.raises(ImportError, match="BrowserGym"):
                load_webarena()


# ---------------------------------------------------------------------------
# WebArenaValScorer tests
# ---------------------------------------------------------------------------


class TestWebArenaValScorer:
    def test_interface_compliance(self):
        """score_batch must have the expected signature."""
        sig = inspect.signature(WebArenaValScorer.score_batch)
        params = list(sig.parameters)
        assert "items" in params
        assert "retrieved" in params
        assert "task_model" in params
        assert "instruction_response" in params
        assert "always_on_knowledge" in params
        assert "reasoning_effort" in params

    def test_score_batch_dispatches_episodes(self):
        """score_batch should dispatch one _run_episode call per item."""
        scorer = WebArenaValScorer(max_steps=5, max_workers=2, episode_timeout=30.0)

        fake_item = MagicMock()
        fake_item.question = "Buy a red shirt"
        fake_item.metadata = {"task_id": 1}

        items = [fake_item]
        retrieved = ["Tip: always add to cart first."]

        def fake_run_episode(*args, **kwargs):
            return ("ACTION: click(button 'Submit')\nOBSERVATION: done", 1.0, "rationale")

        with patch("programmaticmemory.benchmarks.webarena._run_episode", side_effect=fake_run_episode):
            # Patch ProcessPoolExecutor to use ThreadPoolExecutor to avoid spawn overhead
            with patch("concurrent.futures.ProcessPoolExecutor", lambda **kw: ThreadPoolExecutor(max_workers=1)):
                results = scorer.score_batch(items, retrieved, "fake-model", "")

        assert len(results) == 1
        trajectory, score, rationale = results[0]
        assert score == 1.0
        assert "click" in trajectory

    def test_score_batch_handles_failure(self):
        """score_batch should return (error_message, 0.0) when _run_episode raises."""
        scorer = WebArenaValScorer(max_steps=5, max_workers=1, episode_timeout=30.0)

        fake_item = MagicMock()
        fake_item.question = "Do something"
        fake_item.metadata = {"task_id": 99}

        items = [fake_item]
        retrieved = [""]

        def failing_run_episode(*args, **kwargs):
            raise RuntimeError("Browser crashed")

        with patch("programmaticmemory.benchmarks.webarena._run_episode", side_effect=failing_run_episode):
            with patch("concurrent.futures.ProcessPoolExecutor", lambda **kw: ThreadPoolExecutor(max_workers=1)):
                results = scorer.score_batch(items, retrieved, "fake-model", "")

        assert len(results) == 1
        trajectory, score, rationale = results[0]
        assert score == 0.0
        assert "failed" in trajectory.lower() or "crashed" in trajectory.lower()


class TestTraceAgentConsistency:
    """Snapshot tests comparing human trace format vs agent trajectory format.

    These snapshots must be human-reviewed to verify:
    1. Human trace and agent trajectory use the same structural format
    2. Human traces contain useful, actionable strategy information
    """

    def test_human_trace_snapshot(self, snapshot):
        """Snapshot of a parsed human demonstration trace.

        Reviewer: verify this looks like a useful training signal —
        does it show a clear problem-solving strategy that a KB could learn from?
        """
        # Simulate realistic human trace events
        trace_events = [
            {
                "type": "before",
                "apiName": "Frame.goto",
                "params": {"url": "http://shopping.example.com"},
                "startTime": 1.0,
            },
            {
                "type": "before",
                "apiName": "Frame.click",
                "params": {"selector": 'internal:role=link[name="Electronics"]'},
                "startTime": 2.0,
            },
            {
                "type": "before",
                "apiName": "Frame.click",
                "params": {"selector": 'internal:role=button[name="Sort by: Price low to high"]'},
                "startTime": 3.0,
            },
            {
                "type": "before",
                "apiName": "Frame.fill",
                "params": {"selector": 'internal:role=textbox[name="Search"]', "value": "wireless headphones"},
                "startTime": 4.0,
            },
            {
                "type": "before",
                "apiName": "Keyboard.press",
                "params": {"key": "Enter"},
                "startTime": 5.0,
            },
        ]

        steps = []
        step_num = 0
        current_url = ""
        for event in trace_events:
            action = trace_event_to_action(event)
            if action is None:
                continue
            step_num += 1
            params = event.get("params", {}) or {}
            if action.startswith("goto("):
                current_url = params.get("url", "")
            steps.append(format_trajectory_step(step_num, action, current_url))

        trajectory = "\n".join(steps)
        assert trajectory == snapshot

    def test_agent_trajectory_snapshot(self, snapshot):
        """Snapshot of an agent-generated trajectory during a live episode.

        Reviewer: verify this uses the same structural format as the human trace above.
        """
        steps = [
            format_trajectory_step(1, 'goto("http://shopping.example.com")', "http://shopping.example.com"),
            format_trajectory_step(2, "click('a12')", "http://shopping.example.com/electronics"),
            format_trajectory_step(3, "click('b45')", "http://shopping.example.com/electronics?sort=price_asc"),
            format_trajectory_step(
                4, "fill('c78', \"wireless headphones\")", "http://shopping.example.com/electronics"
            ),
            format_trajectory_step(5, 'keyboard_press("Enter")', "http://shopping.example.com/search?q=wireless"),
            format_trajectory_step(6, 'send_msg_to_user("The cheapest is USB-C Headphones at $12.99")', ""),
        ]
        trajectory = "\n".join(steps)
        assert trajectory == snapshot

    def test_format_structure_consistency(self):
        """Both trace and agent trajectories use the same structural format.

        Note: action *arguments* intentionally differ:
        - Trace actions use descriptive selectors: click(link "Electronics")
        - Agent actions use BID strings: click('a51')
        The KB learns task *strategies* from traces, not exact element references.
        """
        trace_step = format_trajectory_step(1, 'click(link "Electronics")', "Navigate to Electronics")
        agent_step = format_trajectory_step(1, "click('a51')", "http://shop.example.com/electronics")

        # Both use identical structural format
        assert trace_step.startswith("Step 1:")
        assert agent_step.startswith("Step 1:")
        assert "\nObservation:" in trace_step
        assert "\nObservation:" in agent_step
        assert trace_step.count("\n") == agent_step.count("\n")
