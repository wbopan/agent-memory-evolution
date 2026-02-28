"""Utility functions for graceful stopping of optimization runs."""

import signal
from typing import Any, Protocol, runtime_checkable


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
