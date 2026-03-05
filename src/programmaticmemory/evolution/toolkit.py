"""Toolkit — resources provided to Knowledge Base Programs during execution."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import chromadb
import litellm
from tenacity import retry, stop_after_attempt, wait_exponential


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
    llm_call_budget: int = 50


class Toolkit:
    """Resource bundle passed to Knowledge Base Program instances.

    Provides SQLite, ChromaDB, LLM access, and logging.
    Each evaluation run gets a fresh Toolkit via the factory.
    """

    def __init__(self, config: ToolkitConfig) -> None:
        self.db: sqlite3.Connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.chroma: chromadb.ClientAPI = chromadb.EphemeralClient()
        self.llm_model: str = config.llm_model
        self.logger: MemoryLogger = MemoryLogger()
        self._llm_call_budget: int = config.llm_call_budget
        self._llm_calls_used: int = 0

    def llm_completion(self, messages: list[dict], **kwargs: object) -> str:
        """Call LLM with budget enforcement and retry logic."""
        if self._llm_calls_used >= self._llm_call_budget:
            raise RuntimeError(
                f"LLM call budget exhausted ({self._llm_call_budget} calls). "
                "Knowledge base program is making too many LLM calls."
            )
        self._llm_calls_used += 1
        return self._llm_call_with_retry(messages, **kwargs)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _llm_call_with_retry(self, messages: list[dict], **kwargs: object) -> str:
        """Internal LLM call with tenacity retry (only retries API errors, not budget)."""
        response = litellm.completion(model=self.llm_model, messages=messages, caching=True, **kwargs)
        return response.choices[0].message.content

    def close(self) -> None:
        """Release resources."""
        self.db.close()
