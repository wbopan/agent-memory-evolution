from __future__ import annotations

import io
import os
import sys
from pathlib import Path
from typing import Protocol

from rich.console import Console
from rich.panel import Panel


class LoggerProtocol(Protocol):
    def log(self, message: str, header: str | None = None): ...


# Color palette for header hashing
_HEADER_COLORS = ["cyan", "green", "yellow", "magenta", "blue", "red"]


def _color_for_header(header: str) -> str:
    """Return a consistent color for a given header based on its hash."""
    return _HEADER_COLORS[hash(header) % len(_HEADER_COLORS)]


class RichLogger(LoggerProtocol):
    """A logger that uses rich formatting with colorful output."""

    def __init__(
        self,
        console: Console | None = None,
        indent_level: int = 0,
        log_file: Path | None = None,
        _log_fh: io.TextIOWrapper | None = None,
    ):
        self.console = console or Console()
        self._debug_enabled = os.environ.get("LOG_LEVEL", "").upper() == "DEBUG"
        self._indent_level = indent_level
        self._log_fh = _log_fh or (open(log_file, "a") if log_file else None)

    def log(self, message: str, header: str | None = None, flush: bool = False):
        """Log a message with optional colored header.

        Args:
            message: The message to log.
            header: Optional header text that will be uppercased and colored.
            flush: Whether to force flush stdout after logging.
        """
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

    def debug(self, message: str, header: str | None = None, flush: bool = True):
        """Log a debug message (only shown when LOG_LEVEL=DEBUG).

        Args:
            message: The message to log.
            header: Optional header text that will be uppercased and colored.
            flush: Whether to force flush stdout after logging (default True for debug).
        """
        if self._debug_enabled:
            self.log(message, header=header, flush=flush)

    def show(self, content: str, title: str | None = None):
        """Display content using a rich panel.

        Args:
            content: The content to display in the panel.
            title: Optional title for the panel.
        """
        indent = "  " * self._indent_level
        panel = Panel(content, title=title, expand=False)
        # Print indent separately then the panel
        if indent:
            self.console.print(indent, end="")
        self.console.print(panel)

    def indent(self) -> RichLogger:
        """Return a new logger with increased indentation level.

        Returns:
            A new RichLogger with indent_level incremented by 1.
        """
        return RichLogger(console=self.console, indent_level=self._indent_level + 1, _log_fh=self._log_fh)

    def close(self) -> None:
        """Close the log file handle if this logger owns it."""
        if self._log_fh is not None:
            self._log_fh.close()
            self._log_fh = None


# Global default logger instance
_default_logger: RichLogger | None = None


def get_logger() -> RichLogger:
    """Get the global default RichLogger instance.

    Returns:
        The global RichLogger instance, creating one if needed.
    """
    global _default_logger
    if _default_logger is None:
        _default_logger = RichLogger()
    return _default_logger


def set_logger(logger: RichLogger) -> None:
    """Set the global default RichLogger instance."""
    global _default_logger
    _default_logger = logger
