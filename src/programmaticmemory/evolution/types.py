"""Core types for the programmatic memory evolution system."""

from __future__ import annotations

import hashlib
import math
import random
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
        always_on_knowledge: str,
    ) -> list[tuple[str, float]]: ...


@dataclass(frozen=True)
class KBProgram:
    """A candidate knowledge base program — the unit of evolution.

    Contains the full source code defining KnowledgeItem, Query, and KnowledgeBase classes.
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

    Train items with raw_text are batch-ingested as knowledge items.
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
    """One training write: the full message exchange that generated a KnowledgeItem."""

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
class PoolEntry:
    """A program in the population pool with its evaluation result."""

    program: KBProgram
    eval_result: EvalResult
    score: float


class ProgramPool:
    """Unbounded pool of evaluated programs with softmax parent selection."""

    def __init__(self, temperature: float = 0.15) -> None:
        self.entries: list[PoolEntry] = []
        self.temperature = temperature

    def add(self, program: KBProgram, eval_result: EvalResult) -> None:
        self.entries.append(PoolEntry(program=program, eval_result=eval_result, score=eval_result.score))

    def sample_parent(self) -> PoolEntry:
        """Sample a parent using softmax-weighted selection."""
        if len(self.entries) == 1:
            return self.entries[0]
        max_score = max(e.score for e in self.entries)
        weights = [math.exp((e.score - max_score) / self.temperature) for e in self.entries]
        return random.choices(self.entries, weights=weights, k=1)[0]

    @property
    def best(self) -> PoolEntry:
        return max(self.entries, key=lambda e: e.score)

    def __len__(self) -> int:
        return len(self.entries)


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
