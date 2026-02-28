"""Tests for run output directory and LLM call logging."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import MagicMock

import litellm

from programmaticmemory.logging.run_output import LLMCallLogger, RunOutputManager


class TestRunOutputManager:
    """Tests for RunOutputManager."""

    def test_creates_timestamped_directory(self, tmp_path):
        """RunOutputManager should create a timestamped subdirectory under base_dir."""
        manager = RunOutputManager(tmp_path, config={"key": "value"})
        try:
            assert manager.run_dir.exists()
            assert manager.run_dir.parent == tmp_path
            # Directory name should look like a timestamp: YYYY-MM-DD-HH-MM-SS
            parts = manager.run_dir.name.split("-")
            assert len(parts) == 6
            # Year should be 4 digits
            assert len(parts[0]) == 4
        finally:
            manager.close()

    def test_writes_config_json(self, tmp_path):
        """RunOutputManager should write the provided config to config.json."""
        config = {"iterations": 5, "model": "test-model", "seed": 42}
        manager = RunOutputManager(tmp_path, config=config)
        try:
            config_path = manager.run_dir / "config.json"
            assert config_path.exists()
            loaded = json.loads(config_path.read_text(encoding="utf-8"))
            assert loaded == config
        finally:
            manager.close()

    def test_write_summary(self, tmp_path):
        """write_summary should write metrics to summary.json."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            metrics = {"best_score": 0.85, "total_iterations": 10}
            manager.write_summary(metrics)

            summary_path = manager.run_dir / "summary.json"
            assert summary_path.exists()
            loaded = json.loads(summary_path.read_text(encoding="utf-8"))
            assert loaded == metrics
        finally:
            manager.close()

    def test_get_log_path(self, tmp_path):
        """get_log_path should return run_dir / run.log."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            log_path = manager.get_log_path()
            assert log_path == manager.run_dir / "run.log"
        finally:
            manager.close()

    def test_set_phase_creates_iter_dir(self, tmp_path):
        """set_phase should cause the iter directory to be created on next log call."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.set_phase(iteration=3, phase="evaluate")

            # The iter dir is created lazily when _iter_dir() is called (via logging),
            # but we can verify the callback state was set correctly
            assert manager._callback._iteration == 3
            assert manager._callback._phase == "evaluate"
            assert manager._callback._run_dir == manager.run_dir
            assert manager._callback._call_index == 0

            # Trigger directory creation by calling _iter_dir
            iter_dir = manager._callback._iter_dir()
            assert iter_dir.exists()
            assert iter_dir.name == "iter_3"
        finally:
            manager.close()

    def test_close_removes_callback(self, tmp_path):
        """close should remove the callback from litellm.callbacks."""
        manager = RunOutputManager(tmp_path, config={})
        callback = manager._callback
        assert callback in litellm.callbacks
        manager.close()
        assert callback not in litellm.callbacks

    def test_close_idempotent(self, tmp_path):
        """Calling close twice should not raise."""
        manager = RunOutputManager(tmp_path, config={})
        manager.close()
        manager.close()  # Should not raise


class TestLLMCallLogger:
    """Tests for LLMCallLogger."""

    def _make_response(self, content: str = "Hello!", prompt_tokens: int = 10, completion_tokens: int = 5):
        """Create a mock litellm response object."""
        response = MagicMock()
        response.choices = [MagicMock()]
        response.choices[0].message.content = content
        response.usage.prompt_tokens = prompt_tokens
        response.usage.completion_tokens = completion_tokens
        response.usage.total_tokens = prompt_tokens + completion_tokens
        return response

    def test_log_success_writes_json_file(self, tmp_path):
        """log_success_event should write a JSON file with expected fields."""
        logger = LLMCallLogger()
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        logger.set_context(run_dir, iteration=1, phase="evaluate")

        start = datetime(2025, 1, 1, 12, 0, 0)
        end = datetime(2025, 1, 1, 12, 0, 1)
        kwargs = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        response = self._make_response("World!")

        logger.log_success_event(kwargs, response, start, end)

        # Check file was created
        json_path = run_dir / "llm_calls" / "iter_1" / "evaluate_001.json"
        assert json_path.exists()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["iteration"] == 1
        assert data["phase"] == "evaluate"
        assert data["call_index"] == 1
        assert data["model"] == "test-model"
        assert data["messages"] == [{"role": "user", "content": "Hello"}]
        assert data["response"] == "World!"
        assert data["duration_ms"] == 1000.0
        assert data["usage"]["prompt_tokens"] == 10
        assert data["usage"]["completion_tokens"] == 5
        assert data["usage"]["total_tokens"] == 15

    def test_call_index_increments(self, tmp_path):
        """Each log_success_event call should increment the call index."""
        logger = LLMCallLogger()
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        logger.set_context(run_dir, iteration=0, phase="eval")

        start = datetime(2025, 1, 1, 12, 0, 0)
        end = datetime(2025, 1, 1, 12, 0, 0, 500000)
        kwargs = {"model": "m", "messages": []}
        response = self._make_response()

        logger.log_success_event(kwargs, response, start, end)
        logger.log_success_event(kwargs, response, start, end)
        logger.log_success_event(kwargs, response, start, end)

        iter_dir = run_dir / "llm_calls" / "iter_0"
        files = sorted(iter_dir.iterdir())
        assert len(files) == 3
        assert files[0].name == "eval_001.json"
        assert files[1].name == "eval_002.json"
        assert files[2].name == "eval_003.json"

    def test_phase_change_resets_index(self, tmp_path):
        """set_context with a new phase should reset call_index to 0."""
        logger = LLMCallLogger()
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()

        start = datetime(2025, 1, 1, 12, 0, 0)
        end = datetime(2025, 1, 1, 12, 0, 1)
        kwargs = {"model": "m", "messages": []}
        response = self._make_response()

        # Phase 1: evaluate
        logger.set_context(run_dir, iteration=0, phase="evaluate")
        logger.log_success_event(kwargs, response, start, end)
        logger.log_success_event(kwargs, response, start, end)
        assert logger._call_index == 2

        # Phase 2: reflect — index should reset
        logger.set_context(run_dir, iteration=0, phase="reflect")
        assert logger._call_index == 0
        logger.log_success_event(kwargs, response, start, end)

        reflect_path = run_dir / "llm_calls" / "iter_0" / "reflect_001.json"
        assert reflect_path.exists()

    def test_log_failure_writes_json(self, tmp_path):
        """log_failure_event should write a JSON file with error field."""
        logger = LLMCallLogger()
        run_dir = tmp_path / "test_run"
        run_dir.mkdir()
        logger.set_context(run_dir, iteration=2, phase="evaluate")

        start = datetime(2025, 1, 1, 12, 0, 0)
        end = datetime(2025, 1, 1, 12, 0, 2)
        kwargs = {
            "model": "test-model",
            "messages": [{"role": "user", "content": "Fail"}],
        }
        error = Exception("API timeout")

        logger.log_failure_event(kwargs, error, start, end)

        json_path = run_dir / "llm_calls" / "iter_2" / "evaluate_001.json"
        assert json_path.exists()

        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["iteration"] == 2
        assert data["phase"] == "evaluate"
        assert data["call_index"] == 1
        assert data["model"] == "test-model"
        assert data["error"] == "API timeout"
        assert data["duration_ms"] == 2000.0
        assert "response" not in data
        assert "usage" not in data

    def test_no_run_dir_skips_logging(self, tmp_path):
        """If _run_dir is None, log_success_event and log_failure_event should be no-ops."""
        logger = LLMCallLogger()
        # _run_dir is None by default
        start = datetime(2025, 1, 1, 12, 0, 0)
        end = datetime(2025, 1, 1, 12, 0, 1)
        kwargs = {"model": "m", "messages": []}

        # These should not raise or create any files
        logger.log_success_event(kwargs, self._make_response(), start, end)
        logger.log_failure_event(kwargs, Exception("err"), start, end)

    def test_extract_response_text_fallback(self, tmp_path):
        """If response has no choices, _extract_response_text should fall back to str()."""
        logger = LLMCallLogger()
        bad_response = "raw string response"
        result = logger._extract_response_text(bad_response)
        assert result == "raw string response"

    def test_extract_usage_fallback(self, tmp_path):
        """If response has no usage, _extract_usage should return Nones."""
        logger = LLMCallLogger()
        bad_response = MagicMock(spec=[])  # No attributes
        result = logger._extract_usage(bad_response)
        assert result == {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
