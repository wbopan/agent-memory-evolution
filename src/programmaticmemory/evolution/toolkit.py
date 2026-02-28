"""Toolkit — resources provided to Memory Programs during execution."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

import chromadb
import litellm
from tenacity import retry, stop_after_attempt, wait_exponential


class MemoryLogger:
    """Internal logger for memory programs to record debug info."""

    def __init__(self) -> None:
        self.logs: list[str] = []

    def log(self, message: str) -> None:
        self.logs.append(message)

    def clear(self) -> None:
        self.logs.clear()


@dataclass
class ToolkitConfig:
    """Configuration for Toolkit creation."""

    llm_model: str = "openrouter/deepseek/deepseek-v3.2"
    llm_call_budget: int = 50
    llm_temperature: float = 0.0


class Toolkit:
    """Resource bundle passed to Memory Program instances.

    Provides SQLite, ChromaDB, LLM access, and logging.
    Each evaluation run gets a fresh Toolkit via the factory.
    """

    def __init__(self, config: ToolkitConfig | None = None) -> None:
        config = config or ToolkitConfig()
        self.db: sqlite3.Connection = sqlite3.connect(":memory:")
        self.chroma: chromadb.ClientAPI = chromadb.EphemeralClient()
        self.llm_model: str = config.llm_model
        self.logger: MemoryLogger = MemoryLogger()
        self._llm_call_budget: int = config.llm_call_budget
        self._llm_calls_used: int = 0
        self._llm_temperature: float = config.llm_temperature

    def llm_completion(self, messages: list[dict], **kwargs: object) -> str:
        """Call LLM with budget enforcement and retry logic."""
        if self._llm_calls_used >= self._llm_call_budget:
            raise RuntimeError(
                f"LLM call budget exhausted ({self._llm_call_budget} calls). "
                "Memory program is making too many LLM calls."
            )
        self._llm_calls_used += 1
        kwargs.setdefault("temperature", self._llm_temperature)
        return self._llm_call_with_retry(messages, **kwargs)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10))
    def _llm_call_with_retry(self, messages: list[dict], **kwargs: object) -> str:
        """Internal LLM call with tenacity retry (only retries API errors, not budget)."""
        response = litellm.completion(model=self.llm_model, messages=messages, **kwargs)
        return response.choices[0].message.content

    def reset(self) -> None:
        """Reset all state for a fresh evaluation."""
        self.db.close()
        self.db = sqlite3.connect(":memory:")
        self.chroma = chromadb.EphemeralClient()
        self.logger.clear()
        self._llm_calls_used = 0

    def close(self) -> None:
        """Release resources."""
        self.db.close()


def create_toolkit(config: ToolkitConfig | None = None) -> Toolkit:
    """Factory function to create a fresh Toolkit."""
    return Toolkit(config)
