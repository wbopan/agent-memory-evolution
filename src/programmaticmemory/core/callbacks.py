"""Callback protocol for optimization instrumentation.

This module provides a callback system for observing optimization runs.
Callbacks are synchronous, observational (cannot modify state), and receive
state access for maximum flexibility.

Example usage:

    class MyCallback:
        def on_optimization_start(self, event: OptimizationStartEvent) -> None:
            print(f"Starting optimization with {event['trainset_size']} training examples")

        def on_iteration_end(self, event: IterationEndEvent) -> None:
            status = 'accepted' if event['proposal_accepted'] else 'rejected'
            print(f"Iteration {event['iteration']}: {status}")
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, TypedDict, runtime_checkable

logger = logging.getLogger(__name__)


# =============================================================================
# Event TypedDicts
# =============================================================================


class OptimizationStartEvent(TypedDict):
    """Event for on_optimization_start callback."""

    seed_candidate: dict[str, str]
    trainset_size: int
    valset_size: int
    config: dict[str, Any]


class OptimizationEndEvent(TypedDict):
    """Event for on_optimization_end callback."""

    best_candidate_idx: int
    total_iterations: int
    total_metric_calls: int
    final_state: Any


class IterationStartEvent(TypedDict):
    """Event for on_iteration_start callback."""

    iteration: int
    state: Any


class IterationEndEvent(TypedDict):
    """Event for on_iteration_end callback."""

    iteration: int
    state: Any
    proposal_accepted: bool


class EvaluationStartEvent(TypedDict):
    """Event for on_evaluation_start callback."""

    iteration: int
    candidate_idx: int | None
    batch_size: int
    capture_traces: bool


class EvaluationEndEvent(TypedDict):
    """Event for on_evaluation_end callback."""

    iteration: int
    candidate_idx: int | None
    scores: list[float]
    has_trajectories: bool
    outputs: list[Any]
    trajectories: list[Any] | None
    objective_scores: list[dict[str, float]] | None


class ProposalStartEvent(TypedDict):
    """Event for on_proposal_start callback."""

    iteration: int
    parent_candidate: dict[str, str]
    components: list[str]
    reflective_dataset: dict[str, list[dict[str, Any]]]


class ProposalEndEvent(TypedDict):
    """Event for on_proposal_end callback."""

    iteration: int
    new_instructions: dict[str, str]


class CandidateAcceptedEvent(TypedDict):
    """Event for on_candidate_accepted callback."""

    iteration: int
    new_candidate_idx: int
    new_score: float


class CandidateRejectedEvent(TypedDict):
    """Event for on_candidate_rejected callback."""

    iteration: int
    old_score: float
    new_score: float
    reason: str


class ErrorEvent(TypedDict):
    """Event for on_error callback."""

    iteration: int
    exception: Exception
    will_continue: bool


@runtime_checkable
class Callback(Protocol):
    """Protocol for optimization callbacks.

    All methods are optional - implement only those you need.
    Callbacks are called synchronously and should not modify the state.
    """

    def on_optimization_start(self, event: OptimizationStartEvent) -> None: ...

    def on_optimization_end(self, event: OptimizationEndEvent) -> None: ...

    def on_iteration_start(self, event: IterationStartEvent) -> None: ...

    def on_iteration_end(self, event: IterationEndEvent) -> None: ...

    def on_evaluation_start(self, event: EvaluationStartEvent) -> None: ...

    def on_evaluation_end(self, event: EvaluationEndEvent) -> None: ...

    def on_proposal_start(self, event: ProposalStartEvent) -> None: ...

    def on_proposal_end(self, event: ProposalEndEvent) -> None: ...

    def on_candidate_accepted(self, event: CandidateAcceptedEvent) -> None: ...

    def on_candidate_rejected(self, event: CandidateRejectedEvent) -> None: ...

    def on_error(self, event: ErrorEvent) -> None: ...


class CompositeCallback:
    """A callback that delegates to multiple child callbacks."""

    def __init__(self, callbacks: list[Any] | None = None):
        self._callbacks: list[Any] = []
        self._method_cache: dict[str, list[tuple[Any, Any]]] = {}
        if callbacks:
            for cb in callbacks:
                self.add(cb)

    @property
    def callbacks(self) -> list[Any]:
        """Return the list of registered callbacks."""
        return self._callbacks

    def add(self, callback: Any) -> None:
        """Add a callback to the composite."""
        self._callbacks.append(callback)
        for method_name in self._method_cache:
            method = getattr(callback, method_name, None)
            if method is not None:
                self._method_cache[method_name].append((callback, method))

    def _notify(self, method_name: str, event: Any) -> None:
        """Notify all callbacks of an event."""
        if method_name not in self._method_cache:
            self._method_cache[method_name] = []
            for callback in self._callbacks:
                method = getattr(callback, method_name, None)
                if method is not None:
                    self._method_cache[method_name].append((callback, method))

        for callback, method in self._method_cache[method_name]:
            try:
                method(event)
            except Exception as e:
                logger.warning(f"Callback {callback} failed on {method_name}: {e}")

    def on_optimization_start(self, event: OptimizationStartEvent) -> None:
        self._notify("on_optimization_start", event)

    def on_optimization_end(self, event: OptimizationEndEvent) -> None:
        self._notify("on_optimization_end", event)

    def on_iteration_start(self, event: IterationStartEvent) -> None:
        self._notify("on_iteration_start", event)

    def on_iteration_end(self, event: IterationEndEvent) -> None:
        self._notify("on_iteration_end", event)

    def on_evaluation_start(self, event: EvaluationStartEvent) -> None:
        self._notify("on_evaluation_start", event)

    def on_evaluation_end(self, event: EvaluationEndEvent) -> None:
        self._notify("on_evaluation_end", event)

    def on_proposal_start(self, event: ProposalStartEvent) -> None:
        self._notify("on_proposal_start", event)

    def on_proposal_end(self, event: ProposalEndEvent) -> None:
        self._notify("on_proposal_end", event)

    def on_candidate_accepted(self, event: CandidateAcceptedEvent) -> None:
        self._notify("on_candidate_accepted", event)

    def on_candidate_rejected(self, event: CandidateRejectedEvent) -> None:
        self._notify("on_candidate_rejected", event)

    def on_error(self, event: ErrorEvent) -> None:
        self._notify("on_error", event)


def notify_callbacks(
    callbacks: list[Any] | None,
    method_name: str,
    event: Any,
) -> None:
    """Utility function to notify a list of callbacks."""
    if callbacks is None:
        return

    for callback in callbacks:
        method = getattr(callback, method_name, None)
        if method is not None:
            try:
                method(event)
            except Exception as e:
                logger.warning(f"Callback {callback} failed on {method_name}: {e}")


class VerboseCallback:
    """Prints detailed progress during optimization using RichLogger."""

    def __init__(self) -> None:
        from programmaticmemory.logging.logger import get_logger

        self._logger = get_logger()
        self._indent_logger = self._logger.indent()

    def on_optimization_start(self, event: OptimizationStartEvent) -> None:
        self._logger.log(
            f"Optimization started: {event['trainset_size']} train, {event['valset_size']} val examples",
            header="start",
        )

    def on_iteration_start(self, event: IterationStartEvent) -> None:
        self._logger.log(f"Starting iteration {event['iteration']}...", header="iter")

    def on_evaluation_start(self, event: EvaluationStartEvent) -> None:
        self._indent_logger.log(f"Evaluating batch of {event['batch_size']} examples...", header="eval")

    def on_evaluation_end(self, event: EvaluationEndEvent) -> None:
        avg = sum(event["scores"]) / len(event["scores"]) if event["scores"] else 0
        self._indent_logger.log(f"Done. Avg score: {avg:.2%}", header="eval")

    def on_candidate_accepted(self, event: CandidateAcceptedEvent) -> None:
        self._indent_logger.log(
            f"New candidate {event['new_candidate_idx']} accepted (score: {event['new_score']:.2f})", header="accept"
        )

    def on_candidate_rejected(self, event: CandidateRejectedEvent) -> None:
        self._indent_logger.log(f"Candidate rejected: {event['reason']}", header="reject")
