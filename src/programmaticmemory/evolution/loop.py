"""Evolution loop — the main GEPA cycle for Knowledge Base Program optimization."""

from __future__ import annotations

import weave

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.strategies import FullDataset
from programmaticmemory.evolution.types import (
    Dataset,
    EvalStrategy,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    ProgramPool,
    SelectionStrategy,
    SoftmaxSelection,
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
        strategy: SelectionStrategy | None = None,
        stop_condition: StopperProtocol | None = None,
        tracker: ExperimentTracker | None = None,
        output_manager: RunOutputManager | None = None,
        eval_strategy: EvalStrategy | None = None,
    ) -> None:
        self.evaluator = evaluator
        self.reflector = reflector
        self.dataset = dataset
        self.initial_programs = initial_programs or [KBProgram(source_code=INITIAL_KB_PROGRAM)]
        self.max_iterations = max_iterations
        self.strategy = strategy or SoftmaxSelection()
        self.stop_condition = stop_condition
        self.tracker = tracker
        self.output_manager = output_manager
        self.eval_strategy = eval_strategy or FullDataset()
        self.logger = get_logger()

    @weave.op()
    def run(self) -> EvolutionState:
        """Execute the evolution loop and return final state."""
        ds = self.dataset
        pool = ProgramPool(strategy=self.strategy)

        self.logger.log(
            f"Starting evolution: max_iter={self.max_iterations}, seeds={len(self.initial_programs)}, "
            f"train={len(ds.train)}, val={len(ds.val)}, strategy={pool.strategy!r}, "
            f"eval_strategy={self.eval_strategy.__class__.__name__}",
            header="EVOLUTION",
        )

        # Evaluate all seed programs
        train, val = self.eval_strategy.select(self.dataset, 0)
        seed_eval_results = []
        for idx, seed in enumerate(self.initial_programs):
            seed_name = f"seed_{idx}"
            if self.output_manager:
                self.output_manager.set_phase(0, "train")
            self.logger.log(
                f"Evaluating seed {idx + 1}/{len(self.initial_programs)} (hash={seed.hash})",
                header="EVOLUTION",
            )
            eval_result = self.evaluator.evaluate(seed, train, val)
            pool.add(seed, eval_result, name=seed_name)
            seed_eval_results.append(eval_result)
            self.logger.log(f"Seed {idx + 1} score: {eval_result.score:.3f}", header="EVOLUTION")

            if self.output_manager:
                self.output_manager.write_program(
                    0, seed.source_code, accepted=True, score=eval_result.score, name=seed_name
                )
            if self.output_manager and eval_result.failed_cases:
                self.output_manager.write_failed_cases(0, _serialize_failed_cases(eval_result.failed_cases))

        best_score = pool.best.score
        self.logger.log(pool.summary(), header="EVOLUTION")
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
            train, val = self.eval_strategy.select(self.dataset, i)
            if self.output_manager:
                self.output_manager.set_phase(i, "train")
            self.logger.log(
                f"Evaluating child program (gen={child.generation}, hash={child.hash})",
                header="EVOLUTION",
            )
            child_result = self.evaluator.evaluate(child, train, val)

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
                child_result = self.evaluator.evaluate(child, train, val)

            child_score = child_result.score

            # Add child to pool unconditionally
            pool.add(child, child_result, name=f"iter_{i}")

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

            self.logger.log(pool.summary(), header="EVOLUTION")

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

        # Final evaluation
        final_data = self.eval_strategy.final_eval_data(self.dataset)
        if final_data is not None:
            candidates = self.eval_strategy.final_candidates(pool)
            self.logger.log(
                f"Final evaluation: {len(candidates)} candidate(s) on full dataset "
                f"(train={len(final_data[0])}, val={len(final_data[1])})",
                header="EVOLUTION",
            )
            for entry in candidates:
                final_result = self.evaluator.evaluate(entry.program, *final_data)
                state.final_scores[entry.program.hash] = final_result.score
                self.logger.log(
                    f"Final score for {entry.program.hash}: {final_result.score:.3f} (evolution: {entry.score:.3f})",
                    header="EVOLUTION",
                )

        # Test evaluation (held-out test set)
        test_data = self.eval_strategy.test_eval_data(self.dataset)
        if test_data is not None:
            # Pick best program: winner from final_scores if available, else pool.best
            if state.final_scores:
                best_hash = max(state.final_scores, key=state.final_scores.get)
                best_entry = next(e for e in pool.entries if e.program.hash == best_hash)
            else:
                best_entry = pool.best
            test_result = self.evaluator.evaluate(best_entry.program, *test_data)
            state.test_scores[best_entry.program.hash] = test_result.score
            self.logger.log(
                f"Test evaluation: {best_entry.program.hash} score={test_result.score:.3f}",
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
            "final_evaluation": {
                "strategy": self.eval_strategy.__class__.__name__,
                "candidates": [{"hash": h, "final_score": s} for h, s in state.final_scores.items()],
            }
            if state.final_scores
            else None,
            "test_evaluation": {
                "scores": dict(state.test_scores.items()),
            }
            if state.test_scores
            else None,
        }
        if self.tracker:
            self.tracker.log_summary(summary)
        if self.output_manager:
            self.output_manager.write_summary(summary)

        return state
