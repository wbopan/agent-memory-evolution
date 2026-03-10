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
    name: str = "seed_0"

    @property
    def score(self) -> float:
        return self.eval_result.score


class SelectionStrategy(Protocol):
    """Strategy for selecting a parent from the program pool."""

    def sample(self, entries: list[PoolEntry]) -> PoolEntry: ...
    def weights(self, entries: list[PoolEntry]) -> list[float]: ...


class SoftmaxSelection:
    """Score-proportional selection using softmax weighting."""

    def __init__(self, temperature: float = 0.15) -> None:
        if temperature <= 0:
            raise ValueError(f"temperature must be positive, got {temperature}")
        self.temperature = temperature

    def weights(self, entries: list[PoolEntry]) -> list[float]:
        max_score = max(e.score for e in entries)
        return [math.exp((e.score - max_score) / self.temperature) for e in entries]

    def sample(self, entries: list[PoolEntry]) -> PoolEntry:
        return random.choices(entries, weights=self.weights(entries), k=1)[0]

    def __repr__(self) -> str:
        return f"SoftmaxSelection(T={self.temperature})"


class RecencyDecaySelection:
    """Roughly uniform selection with exponential decay on older generations."""

    def __init__(self, decay_rate: float = 0.8) -> None:
        if not 0 < decay_rate <= 1:
            raise ValueError(f"decay_rate must be in (0, 1], got {decay_rate}")
        self.decay_rate = decay_rate

    def weights(self, entries: list[PoolEntry]) -> list[float]:
        return [self.decay_rate**e.program.generation for e in entries]

    def sample(self, entries: list[PoolEntry]) -> PoolEntry:
        return random.choices(entries, weights=self.weights(entries), k=1)[0]

    def __repr__(self) -> str:
        return f"RecencyDecaySelection(decay={self.decay_rate})"


class ProgramPool:
    """Unbounded pool of evaluated programs with pluggable parent selection."""

    def __init__(self, strategy: SelectionStrategy) -> None:
        self.entries: list[PoolEntry] = []
        self.strategy = strategy

    def add(self, program: KBProgram, eval_result: EvalResult, name: str = "seed_0") -> None:
        self.entries.append(PoolEntry(program=program, eval_result=eval_result, name=name))

    def sample_parent(self) -> PoolEntry:
        """Sample a parent using the configured selection strategy."""
        if len(self.entries) == 1:
            return self.entries[0]
        return self.strategy.sample(self.entries)

    @property
    def best(self) -> PoolEntry:
        return max(self.entries, key=lambda e: e.score)

    def __len__(self) -> int:
        return len(self.entries)

    def summary(self) -> str:
        """Format pool status: entries sorted by score with selection probabilities."""
        if not self.entries:
            return "Pool: empty"
        sorted_entries = sorted(self.entries, key=lambda e: e.score, reverse=True)
        weights = self.strategy.weights(sorted_entries)
        total = sum(weights)
        lines = [f"Pool ({len(self.entries)} programs, {self.strategy!r}):"]
        for entry, w in zip(sorted_entries, weights, strict=True):
            prob = w / total
            lines.append(
                f"  {entry.program.hash}  score={entry.score:.3f}  P={prob:.1%}"
                f"  gen={entry.program.generation}  programs/{entry.name}.py"
            )
        return "\n".join(lines)


@dataclass
class EvolutionRecord:
    """Record of a single evolution iteration."""

    iteration: int
    program: KBProgram
    score: float
    parent_hash: str | None = None


@dataclass
class EvolutionState:
    """Full state of an evolution run."""

    pool: ProgramPool
    best_score: float
    history: list[EvolutionRecord] = field(default_factory=list)
    total_iterations: int = 0

    @property
    def best_program(self) -> KBProgram:
        return self.pool.best.program
