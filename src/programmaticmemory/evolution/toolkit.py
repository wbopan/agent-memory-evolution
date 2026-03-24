"""Toolkit — resources provided to Knowledge Base Programs during execution."""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass

import chromadb
import litellm
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_NON_RETRYABLE = (
    litellm.ContentPolicyViolationError,
    litellm.AuthenticationError,
    litellm.NotFoundError,
    litellm.BadRequestError,
    litellm.Timeout,
)


def _should_retry(exc: BaseException) -> bool:
    # BadRequestError is the parent of ContentPolicyViolationError;
    # keep both explicit for clarity. Never retry these.
    return not isinstance(exc, _NON_RETRYABLE)


def _log_retry(retry_state):
    exc = retry_state.outcome.exception()
    wait = retry_state.next_action.sleep
    model = retry_state.kwargs.get("model", "unknown")
    logger.warning(
        "[LLM RETRY] model=%s attempt=%d/%d wait=%.1fs error=%s: %s",
        model,
        retry_state.attempt_number,
        3,
        wait,
        type(exc).__name__,
        exc,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(min=1, max=30),
    retry=retry_if_exception(_should_retry),
    before_sleep=_log_retry,
)
def completion_with_retry(**kwargs: object) -> litellm.ModelResponse:
    """litellm.completion with tenacity retry on transient API errors."""
    return litellm.completion(**kwargs)


class MemoryLogger:
    """Internal logger for knowledge base programs to record debug info."""

    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def debug(self, message: str) -> None:
        self.log(message)

    def clear(self) -> None:
        self.logs.clear()


@dataclass
class ToolkitConfig:
    """Configuration for Toolkit creation."""

    llm_model: str
    llm_call_budget: int = 1
    reasoning_effort: str | None = None


class _ThreadSafeSQLiteConnection:
    """Proxy that serializes all access to an in-memory SQLite connection.

    Evolved KB programs call ``self.toolkit.db.execute(...)``, ``self.toolkit.db.cursor()``,
    etc. directly.  When multiple threads share the same Toolkit (parallel kb.read()),
    concurrent access causes ``InterfaceError: bad parameter or other API misuse``.
    This wrapper routes every attribute/method call through a lock.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        object.__setattr__(self, "_conn", conn)
        object.__setattr__(self, "_lock", threading.Lock())

    def execute(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.execute(*args, **kwargs)  # type: ignore[arg-type]

    def executemany(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executemany(*args, **kwargs)  # type: ignore[arg-type]

    def executescript(self, *args: object, **kwargs: object) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.executescript(*args, **kwargs)  # type: ignore[arg-type]

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def cursor(self) -> sqlite3.Cursor:
        with self._lock:
            return self._conn.cursor()

    def __getattr__(self, name: str) -> object:
        return getattr(self._conn, name)


class Toolkit:
    """Resource bundle passed to Knowledge Base Program instances.

    Provides SQLite, ChromaDB, LLM access, and logging.
    Each evaluation run gets a fresh Toolkit via the factory.
    """

    def __init__(self, config: ToolkitConfig) -> None:
        self.db: sqlite3.Connection | _ThreadSafeSQLiteConnection = _ThreadSafeSQLiteConnection(
            sqlite3.connect(":memory:", check_same_thread=False)
        )
        self.chroma: chromadb.ClientAPI = chromadb.EphemeralClient()
        self.llm_model: str = config.llm_model
        self.logger: MemoryLogger = MemoryLogger()
        self._llm_call_budget: int = config.llm_call_budget
        self._llm_calls_used: int = 0
        self._reasoning_effort: str | None = config.reasoning_effort

    def reset_llm_budget(self) -> None:
        """Reset the LLM call counter. Called before each guarded write/read."""
        self._llm_calls_used = 0

    def llm_completion(self, messages: list[dict], **kwargs: object) -> str:
        """Call LLM with budget enforcement and retry logic."""
        if self._llm_calls_used >= self._llm_call_budget:
            raise RuntimeError(
                f"LLM call budget exhausted ({self._llm_call_budget} calls). "
                "Knowledge base program is making too many LLM calls."
            )
        self._llm_calls_used += 1
        return self._llm_call_with_retry(messages, **kwargs)

    def _llm_call_with_retry(self, messages: list[dict], **kwargs: object) -> str:
        """Internal LLM call with retry (only retries API errors, not budget)."""
        if self.llm_model == "smoke-test/noop":
            return "smoke-test-response"
        if self._reasoning_effort is not None:
            kwargs.setdefault("reasoning_effort", self._reasoning_effort)
        response = completion_with_retry(
            model=self.llm_model, messages=[{"role": "system", "content": " "}, *messages], caching=True, **kwargs
        )
        return response.choices[0].message.content

    def close(self) -> None:
        """Release resources."""
        self.db.close()
        # ChromaDB EphemeralClient uses internal SQLite databases; not closing
        # it leaks file descriptors on every evaluation cycle.
        try:
            self.chroma.close()
        except Exception:
            pass
