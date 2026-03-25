"""Evolution loop — the main GEPA cycle for Knowledge Base Program optimization.

Determinism: given a fixed random seed (set via ``random.seed()`` in __main__)
and LLM cache hits (``caching=True`` on all litellm calls), the evolution
trajectory is fully reproducible. Sources of randomness and their controls:

- Parent selection (``random.choices``/``random.choice``) → ``random.seed()``
- Batching k-means (numpy) → ``np.random.RandomState(seed=42)`` in batching.py
- LLM calls (evaluator, reflector, toolkit) → litellm disk cache
- ChromaDB embeddings → deterministic sentence-transformer on same hardware
"""

from __future__ import annotations

import weave

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM, ReferenceProgram, build_lineage_log
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.sandbox import (
    CompileError,
    compile_kb_program,
    freeze_instruction_constants,
    smoke_test,
)
from programmaticmemory.evolution.types import (
    Dataset,
    EvalResult,
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
            "rationale": fc.rationale,
            "score": fc.score,
            "conversation_history": fc.conversation_history,
            "memory_logs": fc.memory_logs,
        }
        for fc in failed_cases
    ]


def _build_eval_cases(eval_result: EvalResult) -> list[dict]:
    """Build per-item case dicts from an EvalResult."""
    all_cases = []
    if eval_result.per_case_scores:
        used = set()
        for score, output in zip(eval_result.per_case_scores, eval_result.per_case_outputs, strict=False):
            case_dict: dict = {"score": score, "output": output}
            for fc in eval_result.failed_cases + eval_result.success_cases:
                fc_id = id(fc)
                if fc_id not in used and fc.output == output and fc.score == score:
                    case_dict = {
                        "question": fc.question,
                        "output": fc.output,
                        "rationale": fc.rationale,
                        "score": fc.score,
                        "conversation_history": fc.conversation_history,
                        "memory_logs": fc.memory_logs,
                    }
                    used.add(fc_id)
                    break
            all_cases.append(case_dict)
    else:
        for fc in eval_result.failed_cases + eval_result.success_cases:
            all_cases.append(
                {
                    "question": fc.question,
                    "output": fc.output,
                    "rationale": fc.rationale,
                    "score": fc.score,
                    "conversation_history": fc.conversation_history,
                    "memory_logs": fc.memory_logs,
                }
            )
    return all_cases


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
        freeze_instructions: bool = False,
        freeze_code: bool = False,
        use_references: bool = True,
        seed_commit_messages: list[str | None] | None = None,
        start_iteration: int = 0,
        resumed_pool: ProgramPool | None = None,
        resumed_state: EvolutionState | None = None,
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
        self.eval_strategy = eval_strategy
        self.freeze_instructions = freeze_instructions
        self.freeze_code = freeze_code
        self.use_references = use_references
        self.seed_commit_messages = seed_commit_messages
        self.start_iteration = start_iteration
        self.resumed_pool = resumed_pool
        self.resumed_state = resumed_state
        self.logger = get_logger()

    def _has_reflection_val(self) -> bool:
        # Check class hierarchy rather than instance to avoid Mock auto-attribute creation
        return any("select_reflection_val" in cls.__dict__ for cls in type(self.eval_strategy).__mro__)

    def _evaluate_program(
        self,
        program: KBProgram,
        train: list,
        val: list,
        reflect_val: list | None,
    ) -> tuple:
        """Evaluate program. Returns (score_result, reflect_result_or_None)."""
        if reflect_val is not None:
            return self.evaluator.evaluate_dual(program, train, val, reflect_val)
        return self.evaluator.evaluate(program, train, val), None

    def _write_eval(
        self,
        name: str,
        program: KBProgram,
        eval_result: EvalResult,
        commit_message: str | None = None,
    ) -> None:
        if not self.output_manager:
            return
        meta = {
            "program_hash": program.hash,
            "overall_score": eval_result.score,
            "generation": program.generation,
            "parent_hash": program.parent_hash,
            "commit_message": commit_message,
            "num_items": len(eval_result.per_case_scores)
            if eval_result.per_case_scores
            else len(eval_result.failed_cases) + len(eval_result.success_cases),
            "runtime_violation": eval_result.runtime_violation,
        }
        cases = _build_eval_cases(eval_result)
        self.output_manager.write_eval_dir(name, meta, cases)

    def _write_checkpoint(self, pool: ProgramPool, state: EvolutionState, last_iteration: int) -> None:
        if not self.output_manager:
            return
        import base64
        import pickle
        import random as _random

        from programmaticmemory.evolution.checkpoint import serialize_pool_entry

        checkpoint = {
            "last_completed_iteration": last_iteration,
            "best_score": state.best_score,
            "random_state": base64.b64encode(pickle.dumps(_random.getstate())).decode("ascii"),
            "pool": [serialize_pool_entry(e) for e in pool.entries],
            "score_history": [
                {
                    "iteration": r.iteration,
                    "score": r.score,
                    "parent_hash": r.parent_hash,
                    "program_hash": r.program.hash,
                }
                for r in state.history
            ],
        }
        if hasattr(self.eval_strategy, "get_state"):
            checkpoint["eval_strategy_state"] = self.eval_strategy.get_state()
        self.output_manager.write_checkpoint(checkpoint)

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

        if self.resumed_pool is not None and self.resumed_state is not None:
            pool = self.resumed_pool
            state = self.resumed_state
            best_score = state.best_score
            self.logger.log(
                f"Resumed from iteration {self.start_iteration}, pool={len(pool)}, best={best_score:.3f}",
                header="EVOLUTION",
            )
            self.logger.log(pool.summary(), header="EVOLUTION")
        else:
            # Evaluate all seed programs
            train, val = self.eval_strategy.select(self.dataset, 0)
            reflect_val = (
                self.eval_strategy.select_reflection_val(self.dataset, 0) if self._has_reflection_val() else None
            )
            seed_eval_results = []
            for idx, seed in enumerate(self.initial_programs):
                seed_name = f"seed_{idx}"
                if self.output_manager:
                    self.output_manager.set_phase(0, "train")
                self.logger.log(
                    f"Evaluating seed {idx + 1}/{len(self.initial_programs)} (hash={seed.hash})",
                    header="EVOLUTION",
                )
                eval_result, reflect_result = self._evaluate_program(seed, train, val, reflect_val)
                seed_msg = self.seed_commit_messages[idx] if self.seed_commit_messages else None
                pool.add(seed, eval_result, name=seed_name, reflection_result=reflect_result, commit_message=seed_msg)
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

            # Write evals and checkpoint for seeds
            for idx, (seed, er) in enumerate(zip(self.initial_programs, seed_eval_results, strict=True)):
                seed_name = f"seed_{idx}"
                self._write_eval(
                    seed_name,
                    seed,
                    er,
                    commit_message=self.seed_commit_messages[idx] if self.seed_commit_messages else None,
                )
                seed_entry = pool.entries[idx]
                if seed_entry.reflection_result is not None:
                    self._write_eval(f"{seed_name}_reflect", seed, seed_entry.reflection_result)
            self._write_checkpoint(pool, state, 0)

        start = self.start_iteration + 1 if self.resumed_pool is not None else 1
        for i in range(start, self.max_iterations + 1):
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
            parent_eval_for_reflect = (
                parent_entry.reflection_result
                if parent_entry.reflection_result is not None
                else parent_entry.eval_result
            )

            # Build cross-program references for reflector context
            references: list[ReferenceProgram] = []
            if self.use_references:
                best_sibling, child_or_parent = pool.find_references(parent_entry)
                if best_sibling is not None:
                    references.append(
                        ReferenceProgram(
                            source_code=best_sibling.program.source_code,
                            score=best_sibling.score,
                            relationship="best_sibling",
                        )
                    )
                if child_or_parent is not None:
                    is_child = child_or_parent.program.parent_hash == parent_entry.program.hash
                    references.append(
                        ReferenceProgram(
                            source_code=child_or_parent.program.source_code,
                            score=child_or_parent.score,
                            relationship="latest_child" if is_child else "parent",
                        )
                    )

            if references:
                ref_desc = ", ".join(f"{r.relationship}={r.score:.3f}" for r in references)
                self.logger.log(f"References: {ref_desc}", header="EVOLUTION")

            lineage_log = build_lineage_log(pool, parent_entry)
            # Use static val score (parent_entry.score) in the prompt, not the
            # rotating val score which can be very noisy on small subsets.
            result = self.reflector.reflect_and_mutate(
                parent,
                parent_eval_for_reflect,
                i,
                references=references or None,
                lineage_log=lineage_log,
                score_override=parent_entry.score,
            )
            if result is None:
                self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
                state.history.append(
                    EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                )
                state.total_iterations = i
                if self.tracker:
                    self.tracker.log_metrics(
                        {
                            "score": parent_entry.score,
                            "best_score": state.best_score,
                            "pool_size": len(pool),
                            "skipped": True,
                        },
                        iteration=i,
                    )
                continue

            child = result.program
            commit_message = result.commit_message

            # Freeze instruction constants if requested
            if self.freeze_instructions:
                frozen_source = freeze_instruction_constants(parent.source_code, child.source_code)
                compile_result = compile_kb_program(frozen_source)
                if isinstance(compile_result, CompileError):
                    self.logger.log(f"Frozen child failed compilation: {compile_result.message}", header="EVOLUTION")
                    state.history.append(
                        EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                    )
                    state.total_iterations = i
                    if self.tracker:
                        self.tracker.log_metrics(
                            {
                                "score": parent_entry.score,
                                "best_score": state.best_score,
                                "pool_size": len(pool),
                                "skipped": True,
                            },
                            iteration=i,
                        )
                    continue
                smoke = smoke_test(frozen_source)
                if not smoke.success:
                    self.logger.log(f"Frozen child failed smoke test: {smoke.error}", header="EVOLUTION")
                    state.history.append(
                        EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                    )
                    state.total_iterations = i
                    if self.tracker:
                        self.tracker.log_metrics(
                            {
                                "score": parent_entry.score,
                                "best_score": state.best_score,
                                "pool_size": len(pool),
                                "skipped": True,
                            },
                            iteration=i,
                        )
                    continue
                child = KBProgram(
                    source_code=frozen_source,
                    generation=child.generation,
                    parent_hash=child.parent_hash,
                )

            # Freeze code structure if requested (GEPA baseline: only instructions evolve)
            if self.freeze_code:
                # Swap args: extract instructions from child, graft onto parent's code
                frozen_source = freeze_instruction_constants(child.source_code, parent.source_code)
                compile_result = compile_kb_program(frozen_source)
                if isinstance(compile_result, CompileError):
                    self.logger.log(
                        f"Frozen-code child failed compilation: {compile_result.message}", header="EVOLUTION"
                    )
                    state.history.append(
                        EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                    )
                    state.total_iterations = i
                    if self.tracker:
                        self.tracker.log_metrics(
                            {
                                "score": parent_entry.score,
                                "best_score": state.best_score,
                                "pool_size": len(pool),
                                "skipped": True,
                            },
                            iteration=i,
                        )
                    continue
                smoke = smoke_test(frozen_source)
                if not smoke.success:
                    self.logger.log(f"Frozen-code child failed smoke test: {smoke.error}", header="EVOLUTION")
                    state.history.append(
                        EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
                    )
                    state.total_iterations = i
                    if self.tracker:
                        self.tracker.log_metrics(
                            {
                                "score": parent_entry.score,
                                "best_score": state.best_score,
                                "pool_size": len(pool),
                                "skipped": True,
                            },
                            iteration=i,
                        )
                    continue
                child = KBProgram(
                    source_code=frozen_source,
                    generation=child.generation,
                    parent_hash=child.parent_hash,
                )

            # Evaluate child
            train, val = self.eval_strategy.select(self.dataset, i)
            reflect_val = (
                self.eval_strategy.select_reflection_val(self.dataset, i) if self._has_reflection_val() else None
            )
            if self.output_manager:
                self.output_manager.set_phase(i, "train")
            self.logger.log(
                f"Evaluating child program (gen={child.generation}, hash={child.hash})",
                header="EVOLUTION",
            )
            child_result, child_reflect = self._evaluate_program(child, train, val, reflect_val)

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
                child_result, child_reflect = self._evaluate_program(child, train, val, reflect_val)

            child_score = child_result.score

            # Add child to pool unconditionally
            pool.add(
                child, child_result, name=f"iter_{i}", reflection_result=child_reflect, commit_message=commit_message
            )

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

            self._write_eval(f"iter_{i}", child, child_result, commit_message=commit_message)
            if child_reflect is not None:
                self._write_eval(f"iter_{i}_reflect", child, child_reflect)
            self._write_checkpoint(pool, state, i)

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
                if self.dataset.extra_scorers and final_result.per_case_outputs:
                    final_items = final_data[1]
                    state.final_extra_metrics[entry.program.hash] = {}
                    for name, scorer in self.dataset.extra_scorers.items():
                        scores = [
                            scorer(out, item.expected_answer)[0]
                            for out, item in zip(final_result.per_case_outputs, final_items, strict=False)
                        ]
                        avg = sum(scores) / len(scores) if scores else 0.0
                        state.final_extra_metrics[entry.program.hash][name] = avg
                        self.logger.log(
                            f"Final extra metric '{name}' for {entry.program.hash}: {avg:.3f}", header="EVOLUTION"
                        )
                # Per-category breakdown
                if self.dataset.category_key:
                    cat_key = self.dataset.category_key
                    final_items = final_data[1]
                    cat_scores: dict[str, list[float]] = {}
                    for score_val, item in zip(final_result.per_case_scores, final_items, strict=False):
                        cat = str(item.metadata.get(cat_key, "unknown"))
                        cat_scores.setdefault(cat, []).append(score_val)
                    state.final_category_scores[entry.program.hash] = {
                        cat: sum(s) / len(s) for cat, s in cat_scores.items()
                    }
                    for cat, avg in state.final_category_scores[entry.program.hash].items():
                        self.logger.log(
                            f"Final category '{cat}' for {entry.program.hash}: {avg:.3f}",
                            header="EVOLUTION",
                        )
                if self.output_manager and final_result.failed_cases:
                    self.output_manager.write_eval_cases(
                        f"final_{entry.program.hash[:8]}",
                        _serialize_failed_cases(final_result.failed_cases),
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
            if self.dataset.extra_scorers and test_result.per_case_outputs:
                test_items = test_data[1]
                state.test_extra_metrics[best_entry.program.hash] = {}
                for name, scorer in self.dataset.extra_scorers.items():
                    scores = [
                        scorer(out, item.expected_answer)[0]
                        for out, item in zip(test_result.per_case_outputs, test_items, strict=False)
                    ]
                    avg = sum(scores) / len(scores) if scores else 0.0
                    state.test_extra_metrics[best_entry.program.hash][name] = avg
                    self.logger.log(f"Test extra metric '{name}': {avg:.3f}", header="EVOLUTION")
            # Per-category breakdown
            if self.dataset.category_key:
                cat_key = self.dataset.category_key
                test_items = test_data[1]
                test_cat_scores: dict[str, list[float]] = {}
                for score_val, item in zip(test_result.per_case_scores, test_items, strict=False):
                    cat = str(item.metadata.get(cat_key, "unknown"))
                    test_cat_scores.setdefault(cat, []).append(score_val)
                state.test_category_scores[best_entry.program.hash] = {
                    cat: sum(s) / len(s) for cat, s in test_cat_scores.items()
                }
                for cat, avg in state.test_category_scores[best_entry.program.hash].items():
                    self.logger.log(
                        f"Test category '{cat}' for {best_entry.program.hash}: {avg:.3f}",
                        header="EVOLUTION",
                    )
            if self.output_manager and test_result.failed_cases:
                self.output_manager.write_eval_cases("test", _serialize_failed_cases(test_result.failed_cases))

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
                "extra_metrics": dict(state.final_extra_metrics),
                "category_scores": dict(state.final_category_scores),
            }
            if state.final_scores
            else None,
            "test_evaluation": {
                "scores": dict(state.test_scores.items()),
                "extra_metrics": dict(state.test_extra_metrics),
                "category_scores": dict(state.test_category_scores),
            }
            if state.test_scores
            else None,
        }
        if self.tracker:
            self.tracker.log_summary(summary)
        if self.output_manager:
            self.output_manager.write_summary(summary)

        return state
