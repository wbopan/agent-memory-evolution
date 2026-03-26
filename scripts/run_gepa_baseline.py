"""Run GEPA baseline on Engram benchmarks.

GEPA optimizes ONLY the ALWAYS_ON_KNOWLEDGE prompt constant. The code
skeleton (KnowledgeItem, Query, KnowledgeBase, other INSTRUCTION_*)
is frozen from the seed program.  Data splits, train subsets, scorer,
and model parameters match the Engram evolution pipeline exactly.

Usage:
    uv run python scripts/run_gepa_baseline.py \
        --dataset locomo --test-size 100 --test-train-ratio 3 \
        --seed-program src/programmaticmemory/seeds/vector_search.py \
        --max-metric-calls 200 --output-dir outputs/gepa-locomo
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gepa.api import optimize
from gepa.core.adapter import EvaluationBatch
from gepa.utils.stop_condition import MaxCandidateProposalsStopper

from programmaticmemory.cache import configure_cache
from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.__main__ import split_val_test
from programmaticmemory.evolution.evaluator import (
    ExactMatchScorer,
    MemoryEvaluator,
    set_batch_pool_size,
)
from programmaticmemory.evolution.sandbox import (
    CompileError,
    compile_kb_program,
    smoke_test,
)
from programmaticmemory.evolution.strategies import SplitValidation, _subset_train_for_eval
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import DataItem, KBProgram


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _parse_extra_kwargs(extra: list[str]) -> dict:
    kwargs: dict = {}
    for arg in extra:
        if "=" not in arg:
            print(f"Error: unrecognized argument: {arg}", file=sys.stderr)
            sys.exit(1)
        key, value = arg.split("=", 1)
        for coerce in (int, float):
            try:
                value = coerce(value)
                break
            except ValueError:
                continue
        kwargs[key] = value
    return kwargs


def inject_always_on_knowledge(source: str, value: str) -> str:
    """Replace ALWAYS_ON_KNOWLEDGE in source with a new value."""
    pattern = re.compile(
        r"^(ALWAYS_ON_KNOWLEDGE\s*=\s*)("
        r'"{3}[\s\S]*?"{3}'
        r"|'{3}[\s\S]*?'{3}"
        r"|\([\s\S]*?\n\)"
        r'|"(?:[^"\\]|\\.)*"'
        r"|'(?:[^'\\]|\\.)*'"
        r")",
        re.MULTILINE,
    )
    return pattern.sub(lambda m: m.group(1) + repr(value), source, count=1)


# ---------------------------------------------------------------------------
# GEPA Adapter
# ---------------------------------------------------------------------------

REFLECTION_PROMPT = """\
You are optimizing a "always-on knowledge" prompt for an LLM-based question-answering system.

This prompt is injected into EVERY query the system handles. It should contain domain knowledge, \
strategies, heuristics, or behavioral rules that help the system answer questions correctly.

The current prompt is:
```
<curr_param>
```

Below are execution traces showing questions, expected answers, actual outputs, and scores.

<side_info>

Based on the failures, write an improved version of the always-on knowledge prompt. \
Focus on adding knowledge or strategies that would help the system answer the failed questions correctly. \
Keep it concise — this text is prepended to every query.

Output ONLY the new prompt text, nothing else."""


@dataclass
class Trace:
    question: str
    expected: str
    output: str
    score: float


class AlwaysOnAdapter:
    """GEPA adapter: evolves only ALWAYS_ON_KNOWLEDGE."""

    propose_new_texts = None  # use GEPA's default LLM proposer

    def __init__(
        self,
        frozen_skeleton: str,
        train_items: list[DataItem],
        evaluator: MemoryEvaluator,
    ) -> None:
        self.frozen_skeleton = frozen_skeleton
        self.train_items = train_items
        self.evaluator = evaluator

    def _build_source(self, candidate: dict[str, str]) -> str:
        return inject_always_on_knowledge(self.frozen_skeleton, candidate["always_on"])

    def evaluate(
        self,
        batch: list[DataItem],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        source = self._build_source(candidate)
        n = len(batch)

        # Compile check
        compile_result = compile_kb_program(source)
        if isinstance(compile_result, CompileError):
            print(f"  [GEPA] Compile error: {compile_result.message}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    Trace(item.question, item.expected_answer, "", 0.0) for item in batch
                ]
                if capture_traces
                else None,
            )

        program = KBProgram(source_code=source)
        try:
            eval_result = self.evaluator.evaluate(program, self.train_items, batch)
        except Exception as exc:
            print(f"  [GEPA] Evaluation error: {exc}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    Trace(item.question, item.expected_answer, "", 0.0) for item in batch
                ]
                if capture_traces
                else None,
            )

        if eval_result.runtime_violation:
            print(f"  [GEPA] Runtime violation: {eval_result.runtime_violation}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    Trace(item.question, item.expected_answer, "", 0.0) for item in batch
                ]
                if capture_traces
                else None,
            )

        scores = eval_result.per_case_scores or [0.0] * n
        outputs = eval_result.per_case_outputs or [""] * n
        # Pad to batch size (GEPA contract: len == len(batch))
        scores = (scores + [0.0] * n)[:n]
        outputs = (outputs + [""] * n)[:n]

        trajectories = None
        if capture_traces:
            trajectories = [
                Trace(
                    question=batch[i].question,
                    expected=batch[i].expected_answer,
                    output=outputs[i] if i < len(outputs) else "",
                    score=scores[i] if i < len(scores) else 0.0,
                )
                for i in range(n)
            ]

        avg = sum(scores) / len(scores) if scores else 0.0
        print(f"  [GEPA] Batch score: {avg:.3f} ({n} items)")
        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build reflection data — question/expected/output/score only, no memory internals."""
        records: list[dict[str, Any]] = []
        if eval_batch.trajectories:
            for traj in eval_batch.trajectories:
                if not isinstance(traj, Trace):
                    continue
                records.append(
                    {
                        "Inputs": f"Question: {traj.question}",
                        "Generated Outputs": traj.output or "(empty)",
                        "Feedback": f"Score: {traj.score:.2f}. Expected answer: {traj.expected}",
                        "score": traj.score,
                    }
                )
        records.sort(key=lambda r: r["score"])
        return dict.fromkeys(components_to_update, records)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run GEPA baseline (ALWAYS_ON_KNOWLEDGE only) on Engram benchmarks"
    )
    # --- Dataset / split flags (mirror __main__.py) ---
    parser.add_argument("--dataset", default="locomo")
    parser.add_argument("--category", default=None)
    parser.add_argument("--test-size", type=int, default=100)
    parser.add_argument("--test-train-ratio", type=int, default=3)
    parser.add_argument("--seed", type=int, default=42)
    # --- Model flags (mirror __main__.py) ---
    parser.add_argument("--task-model", default="openrouter/deepseek/deepseek-v3.2")
    parser.add_argument("--toolkit-model", default="openrouter/deepseek/deepseek-v3.2")
    parser.add_argument("--toolkit-budget", type=int, default=1)
    parser.add_argument(
        "--task-lm-thinking-effort",
        choices=["low", "medium", "high"],
        default=None,
    )
    parser.add_argument("--embedding-model", default="openrouter/baai/bge-m3")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--batch-concurrency", type=int, default=64)
    parser.add_argument("--azure-api-base", default=os.environ.get("AZURE_API_BASE"))
    parser.add_argument("--azure-api-version", default="2024-12-01-preview")
    # --- SplitValidation flags (mirror __main__.py for experiment script compat) ---
    parser.add_argument("--eval-train-ratio", type=int, default=2)
    parser.add_argument("--eval-static-size", type=int, default=60)
    parser.add_argument("--eval-rotate-size", type=int, default=5)
    # --- GEPA-specific flags ---
    parser.add_argument("--reflection-model", default="openrouter/openai/gpt-5.3-codex")
    parser.add_argument("--max-proposals", type=int, default=20,
                        help="Number of candidate proposals (iterations). Matches --iterations in ours.")
    parser.add_argument("--reflection-minibatch-size", type=int, default=5)
    parser.add_argument("--seed-program", type=Path, required=True)
    parser.add_argument("--output-dir", type=str, required=True)
    args, extra = parser.parse_known_args()

    random.seed(args.seed)
    configure_cache("disk")
    set_batch_pool_size(args.batch_concurrency)

    # Azure config
    from programmaticmemory.evolution.azure_config import configure_azure_auth

    all_models = [
        args.task_model,
        args.reflection_model,
        args.toolkit_model,
        args.judge_model,
        args.embedding_model,
    ]
    configure_azure_auth(all_models, args.azure_api_base, args.azure_api_version)

    # Load dataset + split (identical to __main__.py)
    dataset_kwargs = _parse_extra_kwargs(extra)
    judge_model = args.judge_model if args.judge_model is not None else args.task_model
    dataset_kwargs.setdefault("judge_model", judge_model)
    dataset = load_dataset(args.dataset, category=args.category, **dataset_kwargs)
    split_val_test(dataset, test_size=args.test_size, seed=args.seed)

    # Build SplitValidation (identical to __main__.py)
    split_strat = SplitValidation(
        dataset,
        static_size=args.eval_static_size,
        rotate_size=args.eval_rotate_size,
        train_val_ratio=args.eval_train_ratio,
        test_train_ratio=args.test_train_ratio,
        embedding_model=args.embedding_model,
    )

    # Extract data subsets
    train_items = [dataset.train[i] for i in split_strat._train_indices]
    static_val = [dataset.val[i] for i in split_strat._static_indices]
    rotate_pool = [dataset.val[i] for i in split_strat._rotate_pool]

    print(
        f"Dataset: {args.dataset}, train_subset={len(train_items)}, "
        f"static_val={len(static_val)}, rotate_pool={len(rotate_pool)}, "
        f"test={len(dataset.test)}"
    )

    # Load seed program
    seed_source = args.seed_program.read_text()
    st = smoke_test(seed_source)
    if not st.success:
        print(f"Error: seed program failed smoke test: {st.error}", file=sys.stderr)
        sys.exit(1)

    seed_compiled = compile_kb_program(seed_source)
    if isinstance(seed_compiled, CompileError):
        print(f"Error: seed compilation failed: {seed_compiled.message}", file=sys.stderr)
        sys.exit(1)
    seed_aok = seed_compiled.always_on_knowledge

    # Build evaluator (identical to __main__.py)
    compare_fn = dataset.compare_fn or ExactMatchScorer()
    toolkit_config = ToolkitConfig(
        llm_model=args.toolkit_model,
        reasoning_effort=args.task_lm_thinking_effort,
        llm_call_budget=args.toolkit_budget,
    )
    evaluator = MemoryEvaluator(
        compare_fn=compare_fn,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
        val_scorer=dataset.val_scorer,
        reasoning_effort=args.task_lm_thinking_effort,
    )

    # Build adapter
    adapter = AlwaysOnAdapter(
        frozen_skeleton=seed_source,
        train_items=train_items,
        evaluator=evaluator,
    )

    print(f"Frozen skeleton: {args.seed_program.name}")
    print(f"Seed ALWAYS_ON_KNOWLEDGE: {seed_aok[:100]!r}...")
    print(f"Max proposals (iterations): {args.max_proposals}")
    print(f"Reflection model: {args.reflection_model}")
    print("=" * 60)

    # --- GEPA optimization ---
    result = optimize(
        seed_candidate={"always_on": seed_aok},
        trainset=rotate_pool,
        valset=static_val,
        adapter=adapter,
        reflection_lm=args.reflection_model,
        reflection_prompt_template={"always_on": REFLECTION_PROMPT},
        candidate_selection_strategy="pareto",
        frontier_type="instance",
        reflection_minibatch_size=args.reflection_minibatch_size,
        stop_callbacks=MaxCandidateProposalsStopper(max_proposals=args.max_proposals),
        run_dir=args.output_dir,
        seed=args.seed,
        skip_perfect_score=True,
        module_selector="all",
    )

    best_aok = result.best_candidate["always_on"]
    best_val_score = result.val_aggregate_scores[result.best_idx]
    print(
        f"\nGEPA complete: {result.total_metric_calls} metric calls, "
        f"best val score: {best_val_score:.3f}"
    )
    print(f"Best ALWAYS_ON_KNOWLEDGE:\n{best_aok}")

    # --- Test evaluation (same pipeline as Engram final_eval_data) ---
    best_source = inject_always_on_knowledge(seed_source, best_aok)
    if best_source == seed_source and best_aok != seed_aok:
        print("Error: inject_always_on_knowledge produced no change — check seed format", file=sys.stderr)
        sys.exit(1)
    best_program = KBProgram(source_code=best_source)

    test_score = None
    test_category_scores: dict[str, float] = {}
    test_extra_metrics: dict[str, float] = {}

    if dataset.test:
        # Same train subset logic as SplitValidation.final_eval_data()
        if args.test_train_ratio > 0:
            test_train = _subset_train_for_eval(
                dataset.train,
                dataset.test,
                args.test_train_ratio,
                embedding_model=args.embedding_model,
            )
        else:
            test_train = dataset.train

        print(f"\nTest evaluation: {len(dataset.test)} items, train={len(test_train)}")
        test_result = evaluator.evaluate(best_program, test_train, dataset.test)
        test_score = test_result.score
        print(f"Test score: {test_score:.3f}")

        # Per-category breakdown
        if dataset.category_key and test_result.per_case_scores:
            cat_scores: dict[str, list[float]] = {}
            for sv, item in zip(test_result.per_case_scores, dataset.test):
                cat = str(item.metadata.get(dataset.category_key, "unknown"))
                cat_scores.setdefault(cat, []).append(sv)
            for cat, scores in sorted(cat_scores.items()):
                avg = sum(scores) / len(scores)
                test_category_scores[cat] = avg
                print(f"  {cat}: {avg:.3f} ({len(scores)} items)")

        # Extra scorers
        if dataset.extra_scorers and test_result.per_case_outputs:
            for name, scorer in dataset.extra_scorers.items():
                scores = [
                    scorer(out, item.expected_answer)[0]
                    for out, item in zip(test_result.per_case_outputs, dataset.test)
                ]
                avg = sum(scores) / len(scores) if scores else 0.0
                test_extra_metrics[name] = avg
                print(f"  {name}: {avg:.3f}")

    # --- Save summary (compatible with Engram summary.json) ---
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    (out_dir / "best_program.py").write_text(best_source)

    summary = {
        "best_score": best_val_score,
        "total_iterations": result.total_metric_calls,
        "best_program_hash": best_program.hash,
        "best_program_generation": 0,
        "pool_size": len(result.candidates),
        "best_program_source": best_source,
        "final_evaluation": {
            "strategy": "GEPA_AlwaysOn",
            "candidates": [{"hash": best_program.hash, "final_score": test_score}],
            "extra_metrics": {best_program.hash: test_extra_metrics},
            "category_scores": {best_program.hash: test_category_scores},
        }
        if test_score is not None
        else None,
        "test_evaluation": None,
        "gepa": {
            "method": "always_on_knowledge_only",
            "max_proposals": args.max_proposals,
            "total_metric_calls": result.total_metric_calls,
            "candidates_explored": len(result.candidates),
            "best_val_score": best_val_score,
            "reflection_model": args.reflection_model,
            "frozen_skeleton": args.seed_program.name,
        },
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved to: {args.output_dir}")


if __name__ == "__main__":
    main()
