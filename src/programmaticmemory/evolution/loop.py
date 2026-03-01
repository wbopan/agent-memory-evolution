"""Evolution loop — the main GEPA cycle for Memory Program optimization."""

from __future__ import annotations

import weave

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.prompts import INITIAL_MEMORY_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.types import (
    Dataset,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    MemoryProgram,
)
from programmaticmemory.logging.experiment_tracker import ExperimentTracker
from programmaticmemory.logging.logger import get_logger
from programmaticmemory.logging.run_output import RunOutputManager
from programmaticmemory.utils.stop_condition import StopperProtocol


def _serialize_failed_cases(failed_cases: list[FailedCase]) -> list[dict]:
    return [
        {
            "question": fc.question,
            "output": fc.output,
            "expected": fc.expected,
            "score": fc.score,
            "memory_logs": fc.memory_logs,
        }
        for fc in failed_cases
    ]


class EvolutionLoop:
    """Serial greedy evolution loop for Memory Programs."""

    def __init__(
        self,
        evaluator: MemoryEvaluator,
        reflector: Reflector,
        dataset: Dataset,
        initial_program: MemoryProgram | None = None,
        max_iterations: int = 20,
        stop_condition: StopperProtocol | None = None,
        tracker: ExperimentTracker | None = None,
        output_manager: RunOutputManager | None = None,
        drop_degraded_program: bool = False,
    ) -> None:
        self.evaluator = evaluator
        self.reflector = reflector
        self.dataset = dataset
        self.initial_program = initial_program or MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        self.max_iterations = max_iterations
        self.stop_condition = stop_condition
        self.tracker = tracker
        self.output_manager = output_manager
        self.drop_degraded_program = drop_degraded_program
        self.logger = get_logger()

    @weave.op()
    def run(self) -> EvolutionState:
        """Execute the evolution loop and return final state."""
        current = self.initial_program
        ds = self.dataset
        self.logger.log(
            f"Starting evolution: max_iter={self.max_iterations}, train={len(ds.train)}, val={len(ds.val)}",
            header="EVOLUTION",
        )

        # Evaluate initial program
        if self.output_manager:
            self.output_manager.set_phase(0, "train")
        self.logger.log(f"Evaluating initial program (hash={current.hash})", header="EVOLUTION")
        eval_result = self.evaluator.evaluate(current, ds.train, ds.val)
        best_score = eval_result.score
        best_program = current
        self.logger.log(f"Initial score: {best_score:.3f}", header="EVOLUTION")

        if self.output_manager:
            self.output_manager.write_program(0, current.source_code, accepted=True, score=best_score)
        if self.output_manager and eval_result.failed_cases:
            self.output_manager.write_failed_cases(0, _serialize_failed_cases(eval_result.failed_cases))

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
            if self.output_manager:
                self.output_manager.set_phase(i, "reflect")
            child = self.reflector.reflect_and_mutate(current, eval_result, i)
            if child is None:
                self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
                if self.output_manager:
                    self.output_manager.write_program(i, current.source_code, accepted=False, score=best_score)
                state.history.append(EvolutionRecord(iteration=i, program=current, score=best_score, accepted=False))
                state.total_iterations = i
                continue

            # Evaluate child
            if self.output_manager:
                self.output_manager.set_phase(i, "train")
            child_result = self.evaluator.evaluate(child, ds.train, ds.val)

            # Runtime violation fix loop
            for _fix_attempt in range(self.reflector.max_fix_attempts):
                if not child_result.runtime_violation:
                    break
                self.logger.log(
                    f"Runtime violation: {child_result.runtime_violation}, attempting fix",
                    header="EVOLUTION",
                )
                fixed_code = self.reflector.fix_runtime_violation(child.source_code, child_result.runtime_violation)
                if fixed_code is None:
                    self.logger.log("Runtime fix failed, giving up", header="EVOLUTION")
                    break
                child = MemoryProgram(
                    source_code=fixed_code,
                    generation=current.generation + 1,
                    parent_hash=current.hash,
                )
                child_result = self.evaluator.evaluate(child, ds.train, ds.val)

            child_score = child_result.score
            self.logger.log(
                f"Child score: {child_score:.3f} (best: {best_score:.3f})",
                header="EVOLUTION",
            )

            improved = child_score > best_score
            if self.output_manager:
                self.output_manager.write_program(i, child.source_code, accepted=improved, score=child_score)
            if self.output_manager and child_result.failed_cases:
                self.output_manager.write_failed_cases(i, _serialize_failed_cases(child_result.failed_cases))
            if improved:
                self.logger.log(
                    f"Improved! {best_score:.3f} -> {child_score:.3f}",
                    header="EVOLUTION",
                )
                best_score = child_score
                best_program = child
            elif self.drop_degraded_program:
                self.logger.log(
                    f"Dropped ({child_score:.3f} <= {best_score:.3f}), reverting to best",
                    header="EVOLUTION",
                )
            else:
                self.logger.log(
                    f"Degraded ({child_score:.3f} <= {best_score:.3f}), continuing anyway",
                    header="EVOLUTION",
                )

            # Always advance to child unless drop_degraded_program is set and score didn't improve
            if improved or not self.drop_degraded_program:
                current = child
                eval_result = child_result

            accepted = improved or not self.drop_degraded_program

            state.history.append(EvolutionRecord(iteration=i, program=child, score=child_score, accepted=accepted))
            state.best_program = best_program
            state.best_score = best_score
            state.current_program = current
            state.current_score = child_score if accepted else best_score
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
        summary = {
            "best_score": state.best_score,
            "total_iterations": state.total_iterations,
            "best_program_hash": state.best_program.hash,
            "best_program_generation": state.best_program.generation,
            "score_history": [
                {"iteration": r.iteration, "score": r.score, "accepted": r.accepted} for r in state.history
            ],
            "best_program_source": state.best_program.source_code,
        }
        if self.tracker:
            self.tracker.log_summary(summary)
        if self.output_manager:
            self.output_manager.write_summary(summary)

        return state
