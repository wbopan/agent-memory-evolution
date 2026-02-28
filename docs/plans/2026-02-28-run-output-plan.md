# Run Output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add local `outputs/` directory that captures config, logs, summary, and all LLM calls per evolution run.

**Architecture:** A new `RunOutputManager` class creates a timestamped output directory and registers a litellm `CustomLogger` callback to intercept all LLM calls. The loop sets phase markers so the callback knows where to file each call. Zero changes to evaluator/reflector/toolkit.

**Tech Stack:** litellm `CustomLogger` callback API, Python `json`/`datetime`/`pathlib`

---

### Task 1: Add `outputs/` to `.gitignore`

**Files:**
- Modify: `.gitignore`

**Step 1: Add outputs/ to gitignore**

Append `outputs/` to `.gitignore`.

**Step 2: Commit**

```bash
git add .gitignore
git commit -m "chore: add outputs/ to gitignore"
```

---

### Task 2: Create `logging/run_output.py` — RunOutputManager + LLMCallLogger

**Files:**
- Create: `src/programmaticmemory/logging/run_output.py`
- Test: `tests/evolution/test_run_output.py`

**Step 1: Write the test file**

```python
"""Tests for RunOutputManager and LLMCallLogger."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from programmaticmemory.logging.run_output import LLMCallLogger, RunOutputManager


class TestRunOutputManager:
    def test_creates_timestamped_directory(self, tmp_path):
        config = {"dataset": "kv_memory", "iterations": 5}
        manager = RunOutputManager(base_dir=tmp_path, config=config)
        assert manager.run_dir.exists()
        assert manager.run_dir.parent == tmp_path

    def test_writes_config_json(self, tmp_path):
        config = {"dataset": "kv_memory", "iterations": 5, "seed": 42}
        manager = RunOutputManager(base_dir=tmp_path, config=config)
        config_path = manager.run_dir / "config.json"
        assert config_path.exists()
        data = json.loads(config_path.read_text())
        assert data["dataset"] == "kv_memory"
        assert data["seed"] == 42

    def test_write_summary(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.write_summary({"best_score": 0.85, "total_iterations": 3})
        summary_path = manager.run_dir / "summary.json"
        assert summary_path.exists()
        data = json.loads(summary_path.read_text())
        assert data["best_score"] == 0.85

    def test_get_log_path(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        log_path = manager.get_log_path()
        assert log_path == manager.run_dir / "run.log"

    def test_set_phase_creates_iter_dir(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.set_phase(1, "train")
        assert (manager.run_dir / "llm_calls" / "iter_1").is_dir()


class TestLLMCallLogger:
    def _make_kwargs(self, model="test-model", messages=None):
        return {
            "model": model,
            "messages": messages or [{"role": "user", "content": "hello"}],
            "optional_params": {},
        }

    def _make_response(self, content="response text"):
        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = content
        resp.model = "test-model"
        resp.usage = MagicMock()
        resp.usage.prompt_tokens = 10
        resp.usage.completion_tokens = 5
        resp.usage.total_tokens = 15
        return resp

    def test_log_success_writes_json_file(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.set_phase(0, "train")
        logger = manager._callback

        kwargs = self._make_kwargs()
        response = self._make_response()

        from datetime import datetime

        logger.log_success_event(kwargs, response, datetime.now(), datetime.now())

        iter_dir = manager.run_dir / "llm_calls" / "iter_0"
        files = sorted(iter_dir.glob("*.json"))
        assert len(files) == 1
        assert files[0].name.startswith("train_")

        data = json.loads(files[0].read_text())
        assert data["phase"] == "train"
        assert data["model"] == "test-model"
        assert data["messages"] == [{"role": "user", "content": "hello"}]
        assert data["response"] == "response text"

    def test_call_index_increments(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.set_phase(0, "val")
        logger = manager._callback

        kwargs = self._make_kwargs()
        response = self._make_response()

        from datetime import datetime

        now = datetime.now()
        logger.log_success_event(kwargs, response, now, now)
        logger.log_success_event(kwargs, response, now, now)

        iter_dir = manager.run_dir / "llm_calls" / "iter_0"
        files = sorted(iter_dir.glob("*.json"))
        assert len(files) == 2
        assert "val_001" in files[0].name
        assert "val_002" in files[1].name

    def test_phase_change_resets_index(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.set_phase(0, "train")
        logger = manager._callback

        kwargs = self._make_kwargs()
        response = self._make_response()

        from datetime import datetime

        now = datetime.now()
        logger.log_success_event(kwargs, response, now, now)

        manager.set_phase(0, "val")
        logger.log_success_event(kwargs, response, now, now)

        train_files = sorted((manager.run_dir / "llm_calls" / "iter_0").glob("train_*.json"))
        val_files = sorted((manager.run_dir / "llm_calls" / "iter_0").glob("val_*.json"))
        assert len(train_files) == 1
        assert len(val_files) == 1

    def test_log_failure_writes_json(self, tmp_path):
        manager = RunOutputManager(base_dir=tmp_path, config={})
        manager.set_phase(0, "reflect")
        logger = manager._callback

        kwargs = self._make_kwargs()

        from datetime import datetime

        now = datetime.now()
        logger.log_failure_event(kwargs, "API error", now, now)

        iter_dir = manager.run_dir / "llm_calls" / "iter_0"
        files = sorted(iter_dir.glob("*.json"))
        assert len(files) == 1
        data = json.loads(files[0].read_text())
        assert data["error"] == "API error"
```

**Step 2: Run tests to verify they fail**

```bash
uv run pytest tests/evolution/test_run_output.py -v
```

Expected: FAIL — `run_output` module does not exist.

**Step 3: Write `logging/run_output.py`**

```python
"""Local output directory for evolution runs — config, logs, LLM calls, summary."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger


class LLMCallLogger(CustomLogger):
    """litellm callback that writes each LLM call to a JSON file."""

    def __init__(self) -> None:
        self._run_dir: Path | None = None
        self._iteration: int = 0
        self._phase: str = "init"
        self._call_index: int = 0

    def set_context(self, run_dir: Path, iteration: int, phase: str) -> None:
        self._run_dir = run_dir
        self._iteration = iteration
        self._phase = phase
        self._call_index = 0

    def _iter_dir(self) -> Path:
        d = self._run_dir / "llm_calls" / f"iter_{self._iteration}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next_path(self) -> Path:
        self._call_index += 1
        return self._iter_dir() / f"{self._phase}_{self._call_index:03d}.json"

    def _extract_response_text(self, response_obj: Any) -> str:
        try:
            return response_obj.choices[0].message.content
        except Exception:
            return str(response_obj)

    def _extract_usage(self, response_obj: Any) -> dict:
        try:
            u = response_obj.usage
            return {
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "total_tokens": u.total_tokens,
            }
        except Exception:
            return {}

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        if self._run_dir is None:
            return
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "iteration": self._iteration,
            "phase": self._phase,
            "call_index": self._call_index + 1,
            "model": kwargs.get("model", ""),
            "messages": kwargs.get("messages", []),
            "response": self._extract_response_text(response_obj),
            "duration_ms": duration_ms,
            "usage": self._extract_usage(response_obj),
        }
        path = self._next_path()
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        if self._run_dir is None:
            return
        duration_ms = int((end_time - start_time).total_seconds() * 1000)
        record = {
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "iteration": self._iteration,
            "phase": self._phase,
            "call_index": self._call_index + 1,
            "model": kwargs.get("model", ""),
            "messages": kwargs.get("messages", []),
            "error": str(response_obj),
            "duration_ms": duration_ms,
        }
        path = self._next_path()
        path.write_text(json.dumps(record, indent=2, ensure_ascii=False))


class RunOutputManager:
    """Creates and manages a timestamped output directory for an evolution run."""

    def __init__(self, base_dir: str | Path, config: dict[str, Any]) -> None:
        base_dir = Path(base_dir)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        self.run_dir = base_dir / timestamp
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Write config
        (self.run_dir / "config.json").write_text(
            json.dumps(config, indent=2, ensure_ascii=False)
        )

        # Create callback and register with litellm
        self._callback = LLMCallLogger()
        litellm.callbacks.append(self._callback)

    def set_phase(self, iteration: int, phase: str) -> None:
        self._callback.set_context(self.run_dir, iteration, phase)

    def write_summary(self, metrics: dict[str, Any]) -> None:
        (self.run_dir / "summary.json").write_text(
            json.dumps(metrics, indent=2, ensure_ascii=False)
        )

    def get_log_path(self) -> Path:
        return self.run_dir / "run.log"

    def close(self) -> None:
        try:
            litellm.callbacks.remove(self._callback)
        except ValueError:
            pass
```

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/evolution/test_run_output.py -v
```

Expected: all PASS.

**Step 5: Lint**

```bash
uv run ruff check src/programmaticmemory/logging/run_output.py
uv run ruff format src/programmaticmemory/logging/run_output.py
```

**Step 6: Commit**

```bash
git add src/programmaticmemory/logging/run_output.py tests/evolution/test_run_output.py
git commit -m "feat: add RunOutputManager with litellm callback for local LLM call logging"
```

---

### Task 3: Add log file tee to RichLogger

**Files:**
- Modify: `src/programmaticmemory/logging/logger.py`
- Test: `tests/evolution/test_run_output.py` (append)

**Step 1: Write the test**

Append to `tests/evolution/test_run_output.py`:

```python
class TestLoggerTee:
    def test_logger_writes_to_file(self, tmp_path):
        from programmaticmemory.logging.logger import RichLogger

        log_file = tmp_path / "test.log"
        logger = RichLogger(log_file=log_file)
        logger.log("hello world", header="TEST")
        logger.log("second line")

        content = log_file.read_text()
        assert "hello world" in content
        assert "second line" in content

    def test_logger_without_file_works(self):
        from programmaticmemory.logging.logger import RichLogger

        logger = RichLogger()
        logger.log("no crash")  # Should not raise
```

**Step 2: Run test to verify it fails**

```bash
uv run pytest tests/evolution/test_run_output.py::TestLoggerTee -v
```

Expected: FAIL — `RichLogger.__init__` does not accept `log_file`.

**Step 3: Modify `logger.py`**

Add `log_file` parameter to `RichLogger.__init__`. Open file handle, write plain-text version on each `log()` call.

In `RichLogger.__init__`, add:
```python
def __init__(self, console: Console | None = None, indent_level: int = 0, log_file: Path | None = None):
    self.console = console or Console()
    self._debug_enabled = os.environ.get("LOG_LEVEL", "").upper() == "DEBUG"
    self._indent_level = indent_level
    self._log_file = open(log_file, "a") if log_file else None
```

In `RichLogger.log`, after `self.console.print(...)`, add:
```python
if self._log_file:
    indent = "  " * self._indent_level
    prefix = f"[{header.upper()}] " if header else ""
    self._log_file.write(f"{indent}{prefix}{message}\n")
    self._log_file.flush()
```

Also pass `log_file` through in `indent()`:
```python
def indent(self) -> RichLogger:
    return RichLogger(console=self.console, indent_level=self._indent_level + 1, log_file=self._log_file.name if self._log_file else None)
```

Wait — passing `log_file` path reopens the file. Instead, store the path and reopen. Actually simpler: just store the file handle and pass it directly. Refine:

```python
from pathlib import Path

class RichLogger(LoggerProtocol):
    def __init__(self, console: Console | None = None, indent_level: int = 0, log_file: Path | None = None, _log_fh=None):
        self.console = console or Console()
        self._debug_enabled = os.environ.get("LOG_LEVEL", "").upper() == "DEBUG"
        self._indent_level = indent_level
        self._log_fh = _log_fh or (open(log_file, "a") if log_file else None)

    def log(self, message: str, header: str | None = None, flush: bool = False):
        indent = "  " * self._indent_level
        if header:
            color = _color_for_header(header)
            formatted_header = f"[{color}][{header.upper()}][/{color}]"
            self.console.print(f"{indent}{formatted_header} {message}")
        else:
            self.console.print(f"{indent}{message}")
        if flush:
            sys.stdout.flush()
        # Tee to file
        if self._log_fh:
            prefix = f"[{header.upper()}] " if header else ""
            self._log_fh.write(f"{indent}{prefix}{message}\n")
            self._log_fh.flush()

    def indent(self) -> RichLogger:
        return RichLogger(console=self.console, indent_level=self._indent_level + 1, _log_fh=self._log_fh)
```

**Step 4: Run tests**

```bash
uv run pytest tests/evolution/test_run_output.py::TestLoggerTee -v
```

Expected: PASS.

**Step 5: Run existing tests to check no regression**

```bash
uv run pytest tests/evolution/ -m "not llm" -v
```

**Step 6: Commit**

```bash
git add src/programmaticmemory/logging/logger.py tests/evolution/test_run_output.py
git commit -m "feat: add log_file tee to RichLogger"
```

---

### Task 4: Wire RunOutputManager into `loop.py` with `set_phase` calls

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py`

**Step 1: Add `output_manager` optional parameter to `EvolutionLoop.__init__`**

Add to `__init__` signature:
```python
from programmaticmemory.logging.run_output import RunOutputManager

def __init__(
    self,
    ...
    output_manager: RunOutputManager | None = None,
) -> None:
    ...
    self.output_manager = output_manager
```

**Step 2: Add `set_phase` calls in `run()`**

In `run()`, add phase markers at these points:

Before initial evaluation (line ~62):
```python
if self.output_manager:
    self.output_manager.set_phase(0, "train")
```

In the loop, before `self.reflector.reflect_and_mutate(...)` (line ~88):
```python
if self.output_manager:
    self.output_manager.set_phase(i, "reflect")
```

Before `self.evaluator.evaluate(child, ...)` (line ~96):
```python
if self.output_manager:
    self.output_manager.set_phase(i, "train")
```

After the final summary log (line ~145), write summary:
```python
if self.output_manager:
    self.output_manager.write_summary({
        "best_score": state.best_score,
        "total_iterations": state.total_iterations,
        "best_program_hash": state.best_program.hash,
        "best_program_generation": state.best_program.generation,
    })
```

**Step 3: Run existing tests to check no regression**

```bash
uv run pytest tests/evolution/ -m "not llm" -v
```

Expected: all PASS (output_manager defaults to None, no behavior change).

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py
git commit -m "feat: wire RunOutputManager phase markers into evolution loop"
```

---

### Task 5: Wire into `__main__.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py`

**Step 1: Add `--no-output` flag and init RunOutputManager**

Add argument:
```python
parser.add_argument("--no-output", action="store_true", help="Disable local output directory")
```

After loading data, before configuring evaluator, add:
```python
from programmaticmemory.logging.run_output import RunOutputManager

output_manager = None
if not args.no_output:
    output_manager = RunOutputManager(
        base_dir="outputs",
        config=vars(args),
    )
```

Set up logger with tee:
```python
if output_manager:
    from programmaticmemory.logging.logger import RichLogger, _default_logger
    import programmaticmemory.logging.logger as logger_mod
    logger_mod._default_logger = RichLogger(log_file=output_manager.get_log_path())
```

Pass to EvolutionLoop:
```python
loop = EvolutionLoop(
    ...
    output_manager=output_manager,
)
```

After the loop completes, close:
```python
if output_manager:
    output_manager.close()
```

**Step 2: Test manually**

```bash
uv run python -m programmaticmemory.evolution --no-weave --iterations 1 --num-items 2
ls outputs/
```

Expected: a timestamped directory with `config.json`, `run.log`, `summary.json`, and `llm_calls/` with JSON files.

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: wire RunOutputManager into CLI entry point"
```

---

### Task 6: Verify end-to-end and add `outputs/` to CLAUDE.md

**Files:**
- Modify: `CLAUDE.md`

**Step 1: Run full e2e test**

```bash
uv run python -m programmaticmemory.evolution --no-weave --dataset kv_memory --iterations 2 --num-items 3
```

Verify:
- `outputs/YYYY-MM-DD-HH-MM-SS/config.json` exists and has all args
- `outputs/.../run.log` has evolution progress text
- `outputs/.../summary.json` has best_score and total_iterations
- `outputs/.../llm_calls/iter_0/` has train_*.json and val_*.json files
- `outputs/.../llm_calls/iter_1/` has reflect_*.json files
- Each JSON file has model, messages, response, duration_ms, usage

**Step 2: Update CLAUDE.md build commands section**

Add after the existing evolution commands:
```markdown
# Local output directory (default: outputs/YYYY-MM-DD-HH-mm-SS/)
# Contains config.json, run.log, summary.json, llm_calls/ with per-call JSON
# Disable with --no-output
```

**Step 3: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add run output directory info to CLAUDE.md"
```
