"""Core types for the programmatic memory evolution system."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum


class EvalMode(StrEnum):
    """Evaluation mode — determines how training data is ingested.

    OFFLINE: Batch ingest train data (generate observations), then read-only val.
    ONLINE: Interleaved multi-turn train (query→answer→feedback→write), then read-only val.
    """

    OFFLINE = "offline"
    ONLINE = "online"


@dataclass(frozen=True)
class MemoryProgram:
    """A candidate memory program — the unit of evolution.

    Contains the full source code defining Observation, Query, and Memory classes.
    Tracked by content hash for deduplication.
    """

    source_code: str
    generation: int = 0
    parent_hash: str | None = None

    @property
    def hash(self) -> str:
        return hashlib.sha256(self.source_code.encode()).hexdigest()[:16]


@dataclass
class DataItem:
    """A single benchmark data item.

    For OFFLINE: raw_text is ingested during train, question+expected_answer during val.
    For ONLINE: all fields used in interleaved train/val.
    """

    raw_text: str
    question: str
    expected_answer: str


@dataclass
class Dataset:
    """A benchmark dataset with its associated evaluation mode."""

    train: list[DataItem]
    val: list[DataItem]
    test: list[DataItem]
    eval_mode: EvalMode


@dataclass
class FailedCase:
    """A single failed evaluation case, used to drive reflection."""

    question: str
    output: str
    expected: str
    score: float
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    memory_logs: list[str] = field(default_factory=list)


@dataclass
class EvalResult:
    """Aggregated evaluation result for a memory program."""

    score: float
    per_case_scores: list[float] = field(default_factory=list)
    per_case_outputs: list[str] = field(default_factory=list)
    failed_cases: list[FailedCase] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)


@dataclass
class EvolutionRecord:
    """Record of a single evolution iteration."""

    iteration: int
    program: MemoryProgram
    score: float
    accepted: bool


@dataclass
class EvolutionState:
    """Full state of an evolution run."""

    best_program: MemoryProgram
    best_score: float
    current_program: MemoryProgram
    current_score: float
    history: list[EvolutionRecord] = field(default_factory=list)
    total_iterations: int = 0
