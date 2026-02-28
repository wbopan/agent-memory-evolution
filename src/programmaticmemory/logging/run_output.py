"""Local run output directory and LLM call logging via litellm CustomLogger."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger


class LLMCallLogger(CustomLogger):
    """Litellm callback that logs every LLM call to a JSON file on disk."""

    def __init__(self) -> None:
        super().__init__()
        self._run_dir: Path | None = None
        self._iteration: int = 0
        self._phase: str = "init"
        self._call_index: int = 0

    def set_context(self, run_dir: Path, iteration: int, phase: str) -> None:
        """Update the current logging context.

        Args:
            run_dir: Root directory for this run's output.
            iteration: Current evolution iteration number.
            phase: Current phase name (e.g. "evaluate", "reflect").
        """
        self._run_dir = run_dir
        self._iteration = iteration
        self._phase = phase
        self._call_index = 0

    def _iter_dir(self) -> Path:
        """Return (and create) the directory for the current iteration's LLM calls."""
        assert self._run_dir is not None
        d = self._run_dir / "llm_calls" / f"iter_{self._iteration}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next_path(self) -> Path:
        """Increment call index and return the path for the next log file."""
        self._call_index += 1
        return self._iter_dir() / f"{self._phase}_{self._call_index:03d}.json"

    def _extract_response_text(self, response_obj: Any) -> str:
        """Best-effort extraction of the response text from a litellm response."""
        try:
            return response_obj.choices[0].message.content
        except Exception:
            return str(response_obj)

    def _extract_usage(self, response_obj: Any) -> dict[str, int | None]:
        """Best-effort extraction of token usage from a litellm response."""
        try:
            usage = response_obj.usage
            return {
                "prompt_tokens": usage.prompt_tokens,
                "completion_tokens": usage.completion_tokens,
                "total_tokens": usage.total_tokens,
            }
        except Exception:
            return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}

    def log_success_event(self, kwargs: dict, response_obj: Any, start_time: datetime, end_time: datetime) -> None:
        """Log a successful LLM call to disk."""
        if self._run_dir is None:
            return

        path = self._next_path()
        duration_ms = (end_time - start_time).total_seconds() * 1000

        record = {
            "timestamp": end_time.isoformat(),
            "iteration": self._iteration,
            "phase": self._phase,
            "call_index": self._call_index,
            "model": kwargs.get("model", "unknown"),
            "messages": kwargs.get("messages", []),
            "response": self._extract_response_text(response_obj),
            "duration_ms": round(duration_ms, 2),
            "usage": self._extract_usage(response_obj),
        }

        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")

    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: datetime, end_time: datetime) -> None:
        """Log a failed LLM call to disk."""
        if self._run_dir is None:
            return

        path = self._next_path()
        duration_ms = (end_time - start_time).total_seconds() * 1000

        record = {
            "timestamp": end_time.isoformat(),
            "iteration": self._iteration,
            "phase": self._phase,
            "call_index": self._call_index,
            "model": kwargs.get("model", "unknown"),
            "messages": kwargs.get("messages", []),
            "error": str(response_obj),
            "duration_ms": round(duration_ms, 2),
        }

        path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")


class RunOutputManager:
    """Manages a timestamped run output directory and wires up LLM call logging."""

    def __init__(self, base_dir: str | Path, config: dict[str, Any]) -> None:
        """Create a new run output directory and register the LLM call logger.

        Args:
            base_dir: Parent directory under which run directories are created.
            config: Configuration dict to persist as config.json in the run dir.
        """
        base_dir = Path(base_dir)
        timestamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        self.run_dir = base_dir / timestamp
        self.run_dir.mkdir(parents=True, exist_ok=True)

        # Write config
        (self.run_dir / "config.json").write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")

        # Create and register the callback
        self._callback = LLMCallLogger()
        litellm.callbacks.append(self._callback)  # type: ignore[arg-type]

    def set_phase(self, iteration: int, phase: str) -> None:
        """Update the LLM logger context for the current iteration and phase.

        Args:
            iteration: Current evolution iteration number.
            phase: Current phase name (e.g. "evaluate", "reflect").
        """
        self._callback.set_context(self.run_dir, iteration, phase)

    def write_summary(self, metrics: dict[str, Any]) -> None:
        """Write a summary.json file with final metrics.

        Args:
            metrics: Dictionary of summary metrics.
        """
        (self.run_dir / "summary.json").write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")

    def get_log_path(self) -> Path:
        """Return the path for the run's log file."""
        return self.run_dir / "run.log"

    def close(self) -> None:
        """Remove the LLM call logger from litellm callbacks."""
        try:
            litellm.callbacks.remove(self._callback)  # type: ignore[arg-type]
        except ValueError:
            pass
