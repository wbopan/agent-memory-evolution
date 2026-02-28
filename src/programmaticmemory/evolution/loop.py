"""Evolution loop — the main GEPA cycle for Memory Program optimization."""

from __future__ import annotations

from typing import Literal

import weave

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.prompts import INITIAL_MEMORY_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.sandbox import smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import (
    DataItem,
    EvolutionRecord,
    EvolutionState,
    MemoryProgram,
)
from programmaticmemory.logging.experiment_tracker import ExperimentTracker
from programmaticmemory.logging.logger import get_logger
from programmaticmemory.utils.stop_condition import StopperProtocol


class EvolutionLoop:
    """Serial greedy evolution loop for Memory Programs."""

    def __init__(
        self,
        evaluator: MemoryEvaluator,
        reflector: Reflector,
        train_data: list[DataItem],
        val_data: list[DataItem],
        initial_program: MemoryProgram | None = None,
        dataset_type: Literal["A", "B"] = "A",
        max_iterations: int = 20,
        toolkit_config: ToolkitConfig | None = None,
        stop_condition: StopperProtocol | None = None,
        tracker: ExperimentTracker | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.reflector = reflector
        self.train_data = train_data
        self.val_data = val_data
        self.initial_program = initial_program or MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        self.dataset_type = dataset_type
        self.max_iterations = max_iterations
        self.toolkit_config = toolkit_config
        self.stop_condition = stop_condition
        self.tracker = tracker
        self.logger = get_logger()

    @weave.op()
    def run(self) -> EvolutionState:
        """Execute the evolution loop and return final state."""
        current = self.initial_program
        self.logger.log(
            f"Starting evolution: max_iter={self.max_iterations}, "
            f"train={len(self.train_data)}, val={len(self.val_data)}, "
            f"type={self.dataset_type}",
            header="EVOLUTION",
        )

        # Evaluate initial program
        self.logger.log(f"Evaluating initial program (hash={current.hash})", header="EVOLUTION")
        eval_result = self.evaluator.evaluate(current, self.train_data, self.val_data, self.dataset_type)
        best_score = eval_result.score
        best_program = current
        self.logger.log(f"Initial score: {best_score:.3f}", header="EVOLUTION")

        if self.tracker:
            self.tracker.log_metrics({"score": best_score, "accepted": 1}, iteration=0)

        state = EvolutionState(
            best_program=best_program,
            best_score=best_score,
            current_program=current,
            current_score=best_score,
            history=[EvolutionRecord(iteration=0, program=current, score=best_score, accepted=True)],
            total_iterations=0,
        )

        for i in range(1, self.max_iterations + 1):
            # Check stop condition
            if self.stop_condition and self.stop_condition(state):
                self.logger.log(f"Stop condition triggered at iteration {i}", header="EVOLUTION")
                break

            self.logger.log(f"Iteration {i}/{self.max_iterations}", header="EVOLUTION")

            # Reflect and mutate
            child = self.reflector.reflect_and_mutate(current, eval_result, i)
            if child is None:
                self.logger.log("Reflection failed to produce code, skipping", header="EVOLUTION")
                state.history.append(EvolutionRecord(iteration=i, program=current, score=best_score, accepted=False))
                state.total_iterations = i
                continue

            # Smoke test
            st = smoke_test(child.source_code, self.toolkit_config)
            if not st.success:
                self.logger.log(f"Smoke test failed: {st.error}", header="EVOLUTION")
                state.history.append(EvolutionRecord(iteration=i, program=child, score=0.0, accepted=False))
                if self.tracker:
                    self.tracker.log_metrics({"score": 0.0, "accepted": 0, "smoke_test_fail": 1}, iteration=i)
                continue

            # Evaluate child
            child_result = self.evaluator.evaluate(child, self.train_data, self.val_data, self.dataset_type)
            child_score = child_result.score
            self.logger.log(
                f"Child score: {child_score:.3f} (best: {best_score:.3f})",
                header="EVOLUTION",
            )

            accepted = child_score > best_score
            if accepted:
                self.logger.log(
                    f"Accepted! {best_score:.3f} -> {child_score:.3f}",
                    header="EVOLUTION",
                )
                current = child
                eval_result = child_result
                best_score = child_score
                best_program = child
            else:
                self.logger.log(
                    f"Rejected ({child_score:.3f} <= {best_score:.3f})",
                    header="EVOLUTION",
                )

            state.history.append(EvolutionRecord(iteration=i, program=child, score=child_score, accepted=accepted))
            state.best_program = best_program
            state.best_score = best_score
            state.current_program = current
            state.current_score = best_score
            state.total_iterations = i

            if self.tracker:
                self.tracker.log_metrics(
                    {"score": child_score, "best_score": best_score, "accepted": int(accepted)},
                    iteration=i,
                )

        # Final summary
        self.logger.log(
            f"Evolution complete: {state.total_iterations} iterations, best score: {state.best_score:.3f}",
            header="EVOLUTION",
        )
        if self.tracker:
            self.tracker.log_summary(
                {
                    "best_score": state.best_score,
                    "total_iterations": state.total_iterations,
                    "best_program_hash": state.best_program.hash,
                    "best_program_generation": state.best_program.generation,
                }
            )

        return state
