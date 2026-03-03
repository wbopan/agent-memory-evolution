"""Core types for the programmatic memory evolution system."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Protocol


class Scorer(Protocol):
    """Scoring function: compares model output against expected answer."""

    def __call__(self, output: str, expected: str) -> float: ...


class ValScorer(Protocol):
    """Pluggable val scoring strategy.

    Replaces the default LLM answer generation + string-compare scoring.
    Receives items with their KB-retrieved strings and returns (output, score) pairs.
    """

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
    ) -> list[tuple[str, float]]: ...


@dataclass(frozen=True)
class KBProgram:
    """A candidate knowledge base program — the unit of evolution.

    Contains the full source code defining Observation, Query, and KnowledgeBase classes.
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

    Train items with raw_text are batch-ingested as observations.
    Train items without raw_text use interactive QA (query→answer→feedback→write).
    Val items always use question+expected_answer for scoring.
    """

    raw_text: str
    question: str
    expected_answer: str
    metadata: dict = field(default_factory=dict)


@dataclass
class Dataset:
    """A benchmark dataset with its associated scorer."""

    train: list[DataItem]
    val: list[DataItem]
    test: list[DataItem]
    scorer: Scorer | None = None
    val_scorer: ValScorer | None = None
    available_categories: list[str] | None = None


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
class TrainExample:
    """One training write: the full message exchange that generated an Observation."""

    messages: list[dict[str, str]]  # [{"role":"user",...}, {"role":"assistant",...}]


@dataclass
class EvalResult:
    """Aggregated evaluation result for a knowledge base program."""

    score: float
    per_case_scores: list[float] = field(default_factory=list)
    per_case_outputs: list[str] = field(default_factory=list)
    failed_cases: list[FailedCase] = field(default_factory=list)
    success_cases: list[FailedCase] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    train_examples: list[TrainExample] = field(default_factory=list)
    runtime_violation: str | None = None


@dataclass
class EvolutionRecord:
    """Record of a single evolution iteration."""

    iteration: int
    program: KBProgram
    score: float
    accepted: bool


@dataclass
class EvolutionState:
    """Full state of an evolution run."""

    best_program: KBProgram
    best_score: float
    current_program: KBProgram
    current_score: float
    history: list[EvolutionRecord] = field(default_factory=list)
    total_iterations: int = 0
