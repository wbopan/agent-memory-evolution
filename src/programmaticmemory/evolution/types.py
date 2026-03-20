"""Core types for the programmatic memory evolution system."""

from __future__ import annotations

import ast
import hashlib
import math
import random
from dataclasses import dataclass, field
from typing import Any, Protocol


class ValScorer(Protocol):
    """Pluggable val scoring strategy.

    Replaces the default LLM answer generation + string-compare scoring.
    Receives items with their KB-retrieved strings and returns (output, score, rationale) triples.
    """

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
        always_on_knowledge: str,
        *,
        reasoning_effort: str | None = None,
    ) -> list[tuple[str, float, str]]: ...


class EvalStrategy(Protocol):
    """Controls data selection during evolution and final evaluation."""

    def select(self, dataset: Dataset, iteration: int) -> tuple[list[DataItem], list[DataItem]]:
        """Return (train, val) for a given evolution iteration."""
        ...

    def final_candidates(self, pool: ProgramPool) -> list[PoolEntry]:
        """Select which programs to revalidate on full dataset after evolution."""
        ...

    def final_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        """Return (train, val) for final evaluation, or None to skip."""
        ...

    def test_eval_data(self, dataset: Dataset) -> tuple[list[DataItem], list[DataItem]] | None:
        """Return (train, test) for held-out test evaluation, or None to skip."""
        ...


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
    compare_fn: Any | None = None
    val_scorer: ValScorer | None = None
    available_categories: list[str] | None = None
    extra_scorers: dict[str, Any] = field(default_factory=dict)
    category_key: str | None = None


@dataclass
class FailedCase:
    """A single failed evaluation case, used to drive reflection."""

    question: str
    output: str
    rationale: str
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
    reflection_result: EvalResult | None = None
    commit_message: str | None = None

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


class MaxSelection:
    """Always select the highest-scoring entry."""

    def weights(self, entries: list[PoolEntry]) -> list[float]:
        max_score = max(e.score for e in entries)
        return [1.0 if e.score == max_score else 0.0 for e in entries]

    def sample(self, entries: list[PoolEntry]) -> PoolEntry:
        max_score = max(e.score for e in entries)
        best = [e for e in entries if e.score == max_score]
        return random.choice(best)

    def __repr__(self) -> str:
        return "MaxSelection()"


class ProgramPool:
    """Unbounded pool of evaluated programs with pluggable parent selection."""

    def __init__(self, strategy: SelectionStrategy) -> None:
        self.entries: list[PoolEntry] = []
        self.strategy = strategy

    def _ancestor_hashes(self, entry: PoolEntry) -> set[str]:
        """Walk parent_hash chain upward, returning all ancestor hashes."""
        ancestors: set[str] = set()
        hash_to_entry = {e.program.hash: e for e in self.entries}
        current = entry
        while current.program.parent_hash and current.program.parent_hash in hash_to_entry:
            ancestors.add(current.program.parent_hash)
            current = hash_to_entry[current.program.parent_hash]
        return ancestors

    def _descendant_hashes(self, entry: PoolEntry) -> set[str]:
        """Find all descendant hashes by BFS on parent_hash links."""
        descendants: set[str] = set()
        frontier = [entry.program.hash]
        while frontier:
            parent_h = frontier.pop()
            for e in self.entries:
                if e.program.parent_hash == parent_h and e.program.hash not in descendants:
                    descendants.add(e.program.hash)
                    frontier.append(e.program.hash)
        return descendants

    def find_references(self, entry: PoolEntry) -> tuple[PoolEntry | None, PoolEntry | None]:
        """Find two reference programs for cross-program context in reflection.

        Returns (best_sibling, latest_child_or_parent):
        - best_sibling: highest-scoring program NOT in entry's ancestor/descendant chain
        - latest_child_or_parent: most recently added child of entry, or entry's parent if no children
        """
        my_hash = entry.program.hash
        lineage = {my_hash} | self._ancestor_hashes(entry) | self._descendant_hashes(entry)

        # Best sibling: highest score outside lineage
        best_sibling: PoolEntry | None = None
        for e in self.entries:
            if e.program.hash in lineage:
                continue
            if best_sibling is None or e.score > best_sibling.score:
                best_sibling = e

        # Latest child: most recently added entry whose parent_hash == my_hash
        latest_child: PoolEntry | None = None
        for e in self.entries:
            if e.program.parent_hash == my_hash:
                latest_child = e  # entries are append-only, so last match = latest

        if latest_child is not None:
            return best_sibling, latest_child

        # Fallback: parent
        if entry.program.parent_hash:
            for e in self.entries:
                if e.program.hash == entry.program.parent_hash:
                    return best_sibling, e

        return best_sibling, None

    def add(
        self,
        program: KBProgram,
        eval_result: EvalResult,
        name: str = "seed_0",
        reflection_result: EvalResult | None = None,
        commit_message: str | None = None,
    ) -> None:
        self.entries.append(
            PoolEntry(
                program=program,
                eval_result=eval_result,
                name=name,
                reflection_result=reflection_result,
                commit_message=commit_message,
            )
        )

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
    final_scores: dict[str, float] = field(default_factory=dict)
    test_scores: dict[str, float] = field(default_factory=dict)
    final_extra_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    test_extra_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
    final_category_scores: dict[str, dict[str, float]] = field(default_factory=dict)
    test_category_scores: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def best_program(self) -> KBProgram:
        return self.pool.best.program


def _extract_function_names(source: str) -> set[str] | None:
    """Extract top-level function names and class method names from source code.
    Returns None on parse failure (distinct from empty set for valid code with no functions).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(f"{node.name}.{child.name}")
    return names


def diff_functions(parent_source: str, child_source: str) -> tuple[list[str], list[str]]:
    """Compare two program sources and return (added, removed) function/method names.
    Graceful on parse failure: returns ([], []) if either source fails to parse.
    """
    parent_names = _extract_function_names(parent_source)
    child_names = _extract_function_names(child_source)
    if parent_names is None or child_names is None:
        return [], []
    added = sorted(child_names - parent_names)
    removed = sorted(parent_names - child_names)
    return added, removed
