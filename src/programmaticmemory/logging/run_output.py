"""Local run output directory and LLM call logging via litellm CustomLogger."""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import litellm
from litellm.integrations.custom_logger import CustomLogger

from programmaticmemory.logging.logger import get_logger


class LLMCallLogger(CustomLogger):
    """Litellm callback that logs every LLM call to a JSON file on disk."""

    def __init__(self) -> None:
        super().__init__()
        self._run_dir: Path | None = None
        self._iteration: int = 0
        self._phase: str = "init"
        self._call_index: int = 0
        self._lock = threading.Lock()

    def set_context(self, run_dir: Path, iteration: int, phase: str) -> None:
        """Update the current logging context.

        Args:
            run_dir: Root directory for this run's output.
            iteration: Current evolution iteration number.
            phase: Current phase name (e.g. "evaluate", "reflect").
        """
        with self._lock:
            self._run_dir = run_dir
            self._iteration = iteration
            self._phase = phase
            self._call_index = 0

    def _iter_dir(self, run_dir: Path, iteration: int) -> Path:
        """Return (and create) the directory for the given iteration's LLM calls."""
        d = run_dir / "llm_calls" / f"iter_{iteration}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _next_path(self) -> tuple[Path, int, str, int]:
        """Atomically increment call index and return (path, iteration, phase, call_index)."""
        with self._lock:
            if self._run_dir is None:
                raise RuntimeError("_next_path called before set_context")
            self._call_index += 1
            iteration = self._iteration
            phase = self._phase
            call_index = self._call_index
            path = self._iter_dir(self._run_dir, iteration) / f"{phase}_{call_index:03d}.json"
        return path, iteration, phase, call_index

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
        try:
            if self._run_dir is None:
                return

            path, iteration, phase, call_index = self._next_path()
            duration_ms = (end_time - start_time).total_seconds() * 1000

            record = {
                "timestamp": end_time.isoformat(),
                "iteration": iteration,
                "phase": phase,
                "call_index": call_index,
                "model": kwargs.get("model", "unknown"),
                "messages": kwargs.get("messages", []),
                "response": self._extract_response_text(response_obj),
                "duration_ms": round(duration_ms, 2),
                "usage": self._extract_usage(response_obj),
            }

            path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass  # logging must never crash the evolution loop

    def log_failure_event(self, kwargs: dict, response_obj: Any, start_time: datetime, end_time: datetime) -> None:
        """Log a failed LLM call to disk."""
        try:
            if self._run_dir is None:
                return

            path, iteration, phase, call_index = self._next_path()
            duration_ms = (end_time - start_time).total_seconds() * 1000

            record = {
                "timestamp": end_time.isoformat(),
                "iteration": iteration,
                "phase": phase,
                "call_index": call_index,
                "model": kwargs.get("model", "unknown"),
                "messages": kwargs.get("messages", []),
                "error": str(response_obj),
                "duration_ms": round(duration_ms, 2),
            }

            path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass  # logging must never crash the evolution loop


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
        config_path = self.run_dir / "config.json"
        config_path.write_text(json.dumps(config, indent=2, default=str), encoding="utf-8")

        get_logger().log(f"Saved config → {config_path}", header="OUTPUT")

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
        summary_path = self.run_dir / "summary.json"
        summary_path.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
        get_logger().log(f"Saved summary → {summary_path}", header="OUTPUT")

    def write_program(
        self, iteration: int, source_code: str, accepted: bool, score: float, name: str | None = None
    ) -> None:
        """Save a Knowledge Base Program's source code to programs/<name>.py.

        Args:
            iteration: Evolution iteration number.
            source_code: Full Python source of the Knowledge Base Program.
            accepted: Whether the program was accepted as the new best.
            score: Evaluation score to embed in the file header comment.
            name: Filename stem (without .py). Defaults to "seed_0" for iteration 0, "iter_N" otherwise.
        """
        try:
            programs_dir = self.run_dir / "programs"
            programs_dir.mkdir(exist_ok=True)
            if name is None:
                name = "seed_0" if iteration == 0 else f"iter_{iteration}"
            label = "seed" if name.startswith("seed_") else ("accepted" if accepted else "rejected")
            header = f"# {name}  score={score:.4f}  {label}\n\n"
            program_path = programs_dir / f"{name}.py"
            program_path.write_text(header + source_code, encoding="utf-8")
            get_logger().log(f"Saved program ({label}) → {program_path}", header="OUTPUT")
        except Exception:
            pass  # logging must never crash the evolution loop

    def _iter_dir(self, iteration: int) -> Path:
        """Return (and create) the llm_calls/iter_N/ directory for the given iteration."""
        return self._callback._iter_dir(self.run_dir, iteration)

    def write_failed_cases(self, iteration: int, cases: list[dict]) -> None:
        """Save failed evaluation cases to llm_calls/iter_N/failed_cases.json.

        Args:
            iteration: Evolution iteration number.
            cases: List of dicts, each with question/output/expected/score/memory_logs.
        """
        try:
            out_path = self._iter_dir(iteration) / "failed_cases.json"
            out_path.write_text(json.dumps(cases, indent=2, default=str), encoding="utf-8")
            get_logger().log(f"Saved {len(cases)} failed cases → {out_path}", header="OUTPUT")
        except Exception:
            pass  # logging must never crash the evolution loop

    def write_eval_cases(self, label: str, cases: list[dict]) -> None:
        """Save evaluation cases to llm_calls/{label}/failed_cases.json.

        Args:
            label: Directory label (e.g. "final", "test").
            cases: List of dicts, each with question/output/expected/score/memory_logs.
        """
        try:
            d = self.run_dir / "llm_calls" / label
            d.mkdir(parents=True, exist_ok=True)
            out_path = d / "failed_cases.json"
            out_path.write_text(json.dumps(cases, indent=2, default=str), encoding="utf-8")
            get_logger().log(f"Saved {len(cases)} failed cases → {out_path}", header="OUTPUT")
        except Exception:
            pass

    def get_log_path(self) -> Path:
        """Return the path for the run's log file."""
        return self.run_dir / "run.log"

    def close(self) -> None:
        """Remove the LLM call logger from litellm callbacks."""
        try:
            litellm.callbacks.remove(self._callback)  # type: ignore[arg-type]
        except ValueError:
            pass
