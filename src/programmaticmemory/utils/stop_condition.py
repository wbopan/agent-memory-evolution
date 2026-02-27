"""Utility functions for graceful stopping of optimization runs."""

import os
import signal
import time
from typing import Any, Literal, Protocol, runtime_checkable


@runtime_checkable
class StopperProtocol(Protocol):
    """Protocol for stop condition objects.

    A stopper is a callable object that returns True when the optimization should stop.
    """

    def __call__(self, state: Any) -> bool:
        """Check if the optimization should stop.

        Args:
            state: The current optimization state.

        Returns:
            True if the optimization should stop, False otherwise.
        """
        ...


class TimeoutStopCondition(StopperProtocol):
    """Stop callback that stops after a specified timeout."""

    def __init__(self, timeout_seconds: float):
        self.timeout_seconds = timeout_seconds
        self.start_time = time.time()

    def __call__(self, state: Any) -> bool:
        return time.time() - self.start_time > self.timeout_seconds


class FileStopper(StopperProtocol):
    """Stop callback that stops when a specific file exists."""

    def __init__(self, stop_file_path: str):
        self.stop_file_path = stop_file_path

    def __call__(self, state: Any) -> bool:
        return os.path.exists(self.stop_file_path)

    def remove_stop_file(self):
        if os.path.exists(self.stop_file_path):
            os.remove(self.stop_file_path)


class SignalStopper(StopperProtocol):
    """Stop callback that stops when a signal is received."""

    def __init__(self, signals=None):
        self.signals = signals or [signal.SIGINT, signal.SIGTERM]
        self._stop_requested = False
        self._original_handlers = {}
        self._setup_signal_handlers()

    def _setup_signal_handlers(self):
        """Set up signal handlers for graceful shutdown."""

        def signal_handler(signum, frame):
            self._stop_requested = True

        for sig in self.signals:
            try:
                self._original_handlers[sig] = signal.signal(sig, signal_handler)
            except (OSError, ValueError):
                pass

    def __call__(self, state: Any) -> bool:
        return self._stop_requested

    def cleanup(self):
        """Restore original signal handlers."""
        for sig, handler in self._original_handlers.items():
            try:
                signal.signal(sig, handler)
            except (OSError, ValueError):
                pass


class CompositeStopper(StopperProtocol):
    """Stop callback that combines multiple stopping conditions."""

    def __init__(self, *stoppers: StopperProtocol, mode: Literal["any", "all"] = "any"):
        self.stoppers = stoppers
        self.mode = mode

    def __call__(self, state: Any) -> bool:
        if self.mode == "any":
            return any(stopper(state) for stopper in self.stoppers)
        elif self.mode == "all":
            return all(stopper(state) for stopper in self.stoppers)
        else:
            raise ValueError(f"Unknown mode: {self.mode}")
