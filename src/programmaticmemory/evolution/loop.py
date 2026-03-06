"""Evolution loop — the main GEPA cycle for Knowledge Base Program optimization."""

from __future__ import annotations

import weave

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.types import (
    Dataset,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    ProgramPool,
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
    """Population-based evolution loop for Knowledge Base Programs."""

    def __init__(
        self,
        evaluator: MemoryEvaluator,
        reflector: Reflector,
        dataset: Dataset,
        initial_programs: list[KBProgram] | None = None,
        max_iterations: int = 20,
        temperature: float = 0.15,
        stop_condition: StopperProtocol | None = None,
        tracker: ExperimentTracker | None = None,
        output_manager: RunOutputManager | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.reflector = reflector
        self.dataset = dataset
        self.initial_programs = initial_programs or [KBProgram(source_code=INITIAL_KB_PROGRAM)]
        self.max_iterations = max_iterations
        self._temperature = temperature
        self.stop_condition = stop_condition
        self.tracker = tracker
        self.output_manager = output_manager
        self.logger = get_logger()

    @weave.op()
    def run(self) -> EvolutionState:
        """Execute the evolution loop and return final state."""
        ds = self.dataset
        pool = ProgramPool(temperature=self._temperature)

        self.logger.log(
            f"Starting evolution: max_iter={self.max_iterations}, seeds={len(self.initial_programs)}, "
            f"train={len(ds.train)}, val={len(ds.val)}, temperature={pool.temperature}",
            header="EVOLUTION",
        )

        # Evaluate all seed programs
        seed_eval_results = []
        for idx, seed in enumerate(self.initial_programs):
            if self.output_manager:
                self.output_manager.set_phase(0, "train")
            self.logger.log(
                f"Evaluating seed {idx + 1}/{len(self.initial_programs)} (hash={seed.hash})",
                header="EVOLUTION",
            )
            eval_result = self.evaluator.evaluate(seed, ds.train, ds.val)
            pool.add(seed, eval_result)
            seed_eval_results.append(eval_result)
            self.logger.log(f"Seed {idx + 1} score: {eval_result.score:.3f}", header="EVOLUTION")

            if self.output_manager:
                self.output_manager.write_program(0, seed.source_code, accepted=True, score=eval_result.score)
            if self.output_manager and eval_result.failed_cases:
                self.output_manager.write_failed_cases(0, _serialize_failed_cases(eval_result.failed_cases))

        best_score = pool.best.score
        if self.tracker:
            self.tracker.log_metrics({"score": best_score, "accepted": 1}, iteration=0)

        state = EvolutionState(
            pool=pool,
            best_score=best_score,
            history=[
                EvolutionRecord(iteration=0, program=seed, score=er.score)
                for seed, er in zip(self.initial_programs, seed_eval_results, strict=True)
            ],
            total_iterations=0,
        )

        for i in range(1, self.max_iterations + 1):
            if self.stop_condition and self.stop_condition(state):
                self.logger.log(f"Stop condition triggered at iteration {i}", header="EVOLUTION")
                break

            self.logger.log(f"--- Iteration {i}/{self.max_iterations} ---", header="EVOLUTION")

            # Sample parent from pool
            parent_entry = pool.sample_parent()
            parent = parent_entry.program
            parent_eval = parent_entry.eval_result
            self.logger.log(
                f"Selected parent (hash={parent.hash}, score={parent_entry.score:.3f})",
                header="EVOLUTION",
            )

            # Reflect and mutate
            if self.output_manager:
                self.output_manager.set_phase(i, "reflect")
            self.logger.log("Starting reflection", header="EVOLUTION")
            child = self.reflector.reflect_and_mutate(parent, parent_eval, i)
            if child is None:
                self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
                state.history.append(
                    EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                )
                state.total_iterations = i
                continue

            # Evaluate child
            if self.output_manager:
                self.output_manager.set_phase(i, "train")
            self.logger.log(
                f"Evaluating child program (gen={child.generation}, hash={child.hash})",
                header="EVOLUTION",
            )
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
                child = KBProgram(
                    source_code=fixed_code,
                    generation=parent.generation + 1,
                    parent_hash=parent.hash,
                )
                child_result = self.evaluator.evaluate(child, ds.train, ds.val)

            child_score = child_result.score

            # Add child to pool unconditionally
            pool.add(child, child_result)

            improved = child_score > best_score
            self.logger.log(
                f"Child score: {child_score:.3f} (best: {best_score:.3f})",
                header="EVOLUTION",
            )
            if self.output_manager:
                self.output_manager.write_program(i, child.source_code, accepted=improved, score=child_score)
            if self.output_manager and child_result.failed_cases:
                self.output_manager.write_failed_cases(i, _serialize_failed_cases(child_result.failed_cases))

            if improved:
                self.logger.log(f"New best! {best_score:.3f} -> {child_score:.3f}", header="EVOLUTION")
                best_score = child_score

            state.history.append(
                EvolutionRecord(iteration=i, program=child, score=child_score, parent_hash=parent.hash)
            )
            state.best_score = best_score
            state.total_iterations = i

            if self.tracker:
                self.tracker.log_metrics(
                    {"score": child_score, "best_score": best_score, "pool_size": len(pool)},
                    iteration=i,
                )

        # Final summary
        self.logger.log(
            f"Evolution complete: {state.total_iterations} iterations, best score: {state.best_score:.3f}",
            header="EVOLUTION",
        )
        best = state.best_program
        summary = {
            "best_score": state.best_score,
            "total_iterations": state.total_iterations,
            "best_program_hash": best.hash,
            "best_program_generation": best.generation,
            "pool_size": len(pool),
            "score_history": [
                {"iteration": r.iteration, "score": r.score, "parent_hash": r.parent_hash} for r in state.history
            ],
            "best_program_source": best.source_code,
        }
        if self.tracker:
            self.tracker.log_summary(summary)
        if self.output_manager:
            self.output_manager.write_summary(summary)

        return state
