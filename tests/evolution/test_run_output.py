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

            # Log a call to trigger directory creation
            start = datetime(2025, 1, 1, 12, 0, 0)
            end = datetime(2025, 1, 1, 12, 0, 1)
            kwargs = {"model": "m", "messages": []}
            response = MagicMock()
            response.choices = [MagicMock()]
            response.choices[0].message.content = "test"
            response.usage.prompt_tokens = 1
            response.usage.completion_tokens = 1
            response.usage.total_tokens = 2

            manager._callback.log_success_event(kwargs, response, start, end)

            iter_dir = manager.run_dir / "llm_calls" / "iter_3"
            assert iter_dir.exists()
            assert (iter_dir / "evaluate_001.json").exists()
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

    def test_write_program_creates_py_file(self, tmp_path):
        """write_program should save source code to programs/iter_N.py."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_program(iteration=1, source_code="class KnowledgeBase: pass", accepted=True, score=0.75)

            prog_path = manager.run_dir / "programs" / "iter_1.py"
            assert prog_path.exists()
            content = prog_path.read_text(encoding="utf-8")
            assert content.startswith("# iter_1  score=0.7500  accepted\n")
            assert "class KnowledgeBase: pass" in content
        finally:
            manager.close()

    def test_write_program_seed(self, tmp_path):
        """write_program with name='seed_0' should save to programs/seed_0.py."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_program(iteration=0, source_code="# seed", accepted=True, score=0.5, name="seed_0")
            prog_path = manager.run_dir / "programs" / "seed_0.py"
            assert prog_path.exists()
            content = prog_path.read_text(encoding="utf-8")
            assert "seed" in content
        finally:
            manager.close()

    def test_write_program_default_name_iter_0(self, tmp_path):
        """write_program at iteration 0 without name defaults to seed_0.py."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_program(iteration=0, source_code="# default", accepted=True, score=0.5)
            prog_path = manager.run_dir / "programs" / "seed_0.py"
            assert prog_path.exists()
        finally:
            manager.close()

    def test_write_summary_with_history(self, tmp_path):
        """write_summary should persist arbitrary extra fields including score_history."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            metrics = {
                "best_score": 0.9,
                "score_history": [{"iteration": 0, "score": 0.5, "accepted": True}],
                "best_program_source": "class KnowledgeBase: pass",
            }
            manager.write_summary(metrics)
            loaded = json.loads((manager.run_dir / "summary.json").read_text())
            assert loaded["score_history"][0]["iteration"] == 0
            assert "class KnowledgeBase" in loaded["best_program_source"]
        finally:
            manager.close()

    def test_write_failed_cases_creates_json(self, tmp_path):
        """write_failed_cases should write failed_cases.json under llm_calls/iter_N/."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            cases = [
                {"question": "Q1", "output": "A", "expected": "B", "score": 0.0, "memory_logs": ["log1"]},
            ]
            manager.write_failed_cases(iteration=2, cases=cases)

            out_path = manager.run_dir / "llm_calls" / "iter_2" / "failed_cases.json"
            assert out_path.exists()
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            assert loaded[0]["question"] == "Q1"
            assert loaded[0]["memory_logs"] == ["log1"]
        finally:
            manager.close()


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


class TestWriteEvalDir:
    """Tests for RunOutputManager.write_eval_dir."""

    def test_creates_meta_json(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            meta = {"program_hash": "abc123", "overall_score": 0.75}
            manager.write_eval_dir("iter_1_eval", meta=meta, cases=[])
            meta_path = manager.run_dir / "evals" / "iter_1_eval" / "meta.json"
            assert meta_path.exists()
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            assert loaded == meta
        finally:
            manager.close()

    def test_creates_per_item_jsons(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            cases = [
                {"question": "Q0", "score": 0.0},
                {"question": "Q1", "score": 1.0},
                {"question": "Q2", "score": 0.5},
            ]
            manager.write_eval_dir("run_eval", meta={}, cases=cases)
            eval_dir = manager.run_dir / "evals" / "run_eval"
            assert (eval_dir / "000.json").exists()
            assert (eval_dir / "001.json").exists()
            assert (eval_dir / "002.json").exists()

            loaded0 = json.loads((eval_dir / "000.json").read_text(encoding="utf-8"))
            assert loaded0["question"] == "Q0"
            loaded2 = json.loads((eval_dir / "002.json").read_text(encoding="utf-8"))
            assert loaded2["score"] == 0.5
        finally:
            manager.close()

    def test_zero_cases_creates_only_meta(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_eval_dir("empty_eval", meta={"score": 0.0}, cases=[])
            eval_dir = manager.run_dir / "evals" / "empty_eval"
            assert (eval_dir / "meta.json").exists()
            # No per-item files
            assert not (eval_dir / "000.json").exists()
        finally:
            manager.close()

    def test_different_names_produce_separate_dirs(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_eval_dir("eval_a", meta={"x": 1}, cases=[{"q": "a"}])
            manager.write_eval_dir("eval_b", meta={"x": 2}, cases=[{"q": "b"}])
            assert (manager.run_dir / "evals" / "eval_a" / "meta.json").exists()
            assert (manager.run_dir / "evals" / "eval_b" / "meta.json").exists()
            loaded_a = json.loads((manager.run_dir / "evals" / "eval_a" / "meta.json").read_text())
            assert loaded_a["x"] == 1
        finally:
            manager.close()

    def test_filenames_zero_padded_to_three_digits(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            cases = [{"i": i} for i in range(12)]
            manager.write_eval_dir("padded", meta={}, cases=cases)
            eval_dir = manager.run_dir / "evals" / "padded"
            # Check zero-padding
            assert (eval_dir / "000.json").exists()
            assert (eval_dir / "009.json").exists()
            assert (eval_dir / "010.json").exists()
            assert (eval_dir / "011.json").exists()
        finally:
            manager.close()


class TestCheckpoint:
    """Tests for write_checkpoint, load_checkpoint, and from_existing."""

    def test_write_and_load_round_trip(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            state = {"iteration": 5, "best_score": 0.88, "pool": ["hash1", "hash2"]}
            manager.write_checkpoint(state)
            loaded = RunOutputManager.load_checkpoint(manager.run_dir)
            assert loaded == state
        finally:
            manager.close()

    def test_load_checkpoint_missing_returns_none(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            result = RunOutputManager.load_checkpoint(manager.run_dir)
            assert result is None
        finally:
            manager.close()

    def test_load_checkpoint_accepts_string_path(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_checkpoint({"k": "v"})
            loaded = RunOutputManager.load_checkpoint(str(manager.run_dir))
            assert loaded["k"] == "v"
        finally:
            manager.close()

    def test_write_checkpoint_is_atomic_overwrite(self, tmp_path):
        """A second write_checkpoint should overwrite the first without leaving .tmp."""
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_checkpoint({"iteration": 1})
            manager.write_checkpoint({"iteration": 2})
            loaded = RunOutputManager.load_checkpoint(manager.run_dir)
            assert loaded["iteration"] == 2
            # No stale .tmp file
            assert not (manager.run_dir / "state.json.tmp").exists()
        finally:
            manager.close()

    def test_write_checkpoint_creates_state_json(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            manager.write_checkpoint({"x": 42})
            assert (manager.run_dir / "state.json").exists()
        finally:
            manager.close()

    def test_checkpoint_preserves_nested_structures(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        try:
            state = {
                "pool": [{"name": "seed_0", "score": 0.5}, {"name": "iter_1", "score": 0.7}],
                "history": [{"iteration": 1, "score": 0.7, "parent_hash": "deadbeef"}],
            }
            manager.write_checkpoint(state)
            loaded = RunOutputManager.load_checkpoint(manager.run_dir)
            assert loaded["pool"][1]["name"] == "iter_1"
            assert loaded["history"][0]["parent_hash"] == "deadbeef"
        finally:
            manager.close()


class TestFromExisting:
    """Tests for RunOutputManager.from_existing."""

    def test_opens_existing_dir(self, tmp_path):
        # Create a run first
        manager = RunOutputManager(tmp_path, config={"key": "value"})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        try:
            assert resumed.run_dir == run_dir
        finally:
            resumed.close()

    def test_does_not_overwrite_config(self, tmp_path):
        config = {"original": True, "model": "test-model"}
        manager = RunOutputManager(tmp_path, config=config)
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        try:
            loaded = json.loads((run_dir / "config.json").read_text(encoding="utf-8"))
            assert loaded == config
        finally:
            resumed.close()

    def test_registers_llm_callback(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        try:
            assert resumed._callback in litellm.callbacks
        finally:
            resumed.close()

    def test_close_removes_callback(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        callback = resumed._callback
        resumed.close()
        assert callback not in litellm.callbacks

    def test_accepts_string_path(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(str(run_dir))
        try:
            assert resumed.run_dir == run_dir
        finally:
            resumed.close()

    def test_can_write_checkpoint_after_resume(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        try:
            resumed.write_checkpoint({"resumed": True, "iteration": 10})
            loaded = RunOutputManager.load_checkpoint(run_dir)
            assert loaded["resumed"] is True
        finally:
            resumed.close()

    def test_can_write_program_after_resume(self, tmp_path):
        manager = RunOutputManager(tmp_path, config={})
        run_dir = manager.run_dir
        manager.close()

        resumed = RunOutputManager.from_existing(run_dir)
        try:
            resumed.write_program(iteration=5, source_code="# resumed program", accepted=True, score=0.9)
            prog_path = run_dir / "programs" / "iter_5.py"
            assert prog_path.exists()
        finally:
            resumed.close()


class TestLoggerTee:
    """Tests for RichLogger log_file tee functionality."""

    def test_logger_writes_to_file(self, tmp_path):
        from programmaticmemory.logging.logger import RichLogger

        log_file = tmp_path / "test.log"
        logger = RichLogger(log_file=log_file)
        logger.log("hello world", header="TEST")
        logger.log("second line")

        content = log_file.read_text()
        assert "[TEST] hello world" in content
        assert "second line" in content

    def test_logger_without_file_works(self):
        from programmaticmemory.logging.logger import RichLogger

        logger = RichLogger()
        logger.log("no crash")  # Should not raise

    def test_indent_preserves_file_handle(self, tmp_path):
        from programmaticmemory.logging.logger import RichLogger

        log_file = tmp_path / "test.log"
        logger = RichLogger(log_file=log_file)
        indented = logger.indent()
        indented.log("indented message", header="SUB")

        content = log_file.read_text()
        assert "[SUB] indented message" in content
