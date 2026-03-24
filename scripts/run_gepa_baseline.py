"""Run GEPA baseline on Engram benchmarks.

Uses the official GEPA library to evolve KBProgram source code, treating it as
a text artifact optimized via LLM-based reflection and Pareto-efficient search.

Usage:
    uv run python scripts/run_gepa_baseline.py \
        --dataset locomo --category 8 \
        --max-metric-calls 200 \
        --seed-program src/programmaticmemory/seeds/vector_search.py

The script wraps our MemoryEvaluator inside a GEPAAdapter so that GEPA controls
the optimization loop while using the exact same evaluation pipeline (same task
model, scorer, data splits) as Engram.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gepa.api import optimize
from gepa.core.adapter import EvaluationBatch

from programmaticmemory.cache import configure_cache
from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.evaluator import MemoryEvaluator, TokenF1Scorer, set_batch_pool_size
from programmaticmemory.evolution.sandbox import CompileError, compile_kb_program, smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import DataItem, Dataset, KBProgram


def split_val_test(dataset: Dataset, test_size: int, seed: int) -> None:
    """Split dataset.val into evolution-val and held-out test (same as __main__.py)."""
    if test_size == -1:
        dataset.test = list(dataset.val)
        return
    if test_size == 0:
        dataset.test = []
        return
    if test_size >= len(dataset.val):
        print(f"Error: --test-size ({test_size}) must be < len(val) ({len(dataset.val)})", file=sys.stderr)
        sys.exit(1)
    val_copy = list(dataset.val)
    rng = random.Random(seed)
    rng.shuffle(val_copy)
    dataset.val = val_copy[:-test_size]
    dataset.test = val_copy[-test_size:]


@dataclass
class TrajectoryRecord:
    """Per-example execution trace for GEPA reflection."""

    question: str
    expected: str
    output: str
    score: float
    conversation_history: list[dict[str, str]]
    memory_logs: str


REFLECTION_PROMPT_TEMPLATE = """\
You are an expert Python programmer optimizing a Knowledge Base Program — a Python \
module that defines how an LLM agent stores and retrieves information.

The current program is:
```python
<curr_param>
```

Below are execution traces showing how the program performed on recent examples. \
Each trace shows the question asked, the expected answer, the program's output, and \
relevant conversation history.

<side_info>

Analyze the failures and propose an improved version of the COMPLETE Python module. \
You may modify:
- The INSTRUCTION_* string constants (prompt engineering)
- The ALWAYS_ON_KNOWLEDGE constant
- The KnowledgeItem/Query dataclass fields
- The KnowledgeBase.write() and read() logic

Constraints:
- Must define: KnowledgeItem, Query, KnowledgeBase (classes) + 4 string constants
- Allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb
- KnowledgeBase.__init__(self, toolkit) receives a Toolkit with: .db (SQLite), .chroma (ChromaDB), .llm_completion(prompt, max_tokens), .logger
- read() output must be ≤ 1000 chars
- Each write()/read() call must complete within 5 seconds

Output ONLY the complete Python module, nothing else."""


class EngramGEPAAdapter:
    """GEPAAdapter that wraps our MemoryEvaluator for GEPA optimization."""

    propose_new_texts = None  # use GEPA's default LLM proposer

    def __init__(
        self,
        train_items: list[DataItem],
        evaluator: MemoryEvaluator,
    ) -> None:
        self.train_items = train_items
        self.evaluator = evaluator

    def evaluate(
        self,
        batch: list[DataItem],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch:
        source_code = candidate["memory_program"]
        n = len(batch)

        # Compile check
        compile_result = compile_kb_program(source_code)
        if isinstance(compile_result, CompileError):
            print(f"  [GEPA] Compile error: {compile_result.message}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    TrajectoryRecord(
                        question=item.question,
                        expected=item.expected_answer,
                        output="",
                        score=0.0,
                        conversation_history=[],
                        memory_logs=f"Compile error: {compile_result.message}\n{compile_result.details}",
                    )
                    for item in batch
                ]
                if capture_traces
                else None,
            )

        # Smoke test
        st = smoke_test(source_code)
        if not st.success:
            print(f"  [GEPA] Smoke test failed: {st.error}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    TrajectoryRecord(
                        question=item.question,
                        expected=item.expected_answer,
                        output="",
                        score=0.0,
                        conversation_history=[],
                        memory_logs=f"Smoke test error: {st.error}",
                    )
                    for item in batch
                ]
                if capture_traces
                else None,
            )

        # Run full evaluation pipeline
        program = KBProgram(source_code=source_code)
        try:
            eval_result = self.evaluator.evaluate(program, self.train_items, batch)
        except Exception as exc:
            print(f"  [GEPA] Evaluation error: {exc}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    TrajectoryRecord(
                        question=item.question,
                        expected=item.expected_answer,
                        output="",
                        score=0.0,
                        conversation_history=[],
                        memory_logs=f"Evaluation error: {exc}",
                    )
                    for item in batch
                ]
                if capture_traces
                else None,
            )

        # Runtime violation → all zeros
        if eval_result.runtime_violation:
            print(f"  [GEPA] Runtime violation: {eval_result.runtime_violation}")
            return EvaluationBatch(
                outputs=[""] * n,
                scores=[0.0] * n,
                trajectories=[
                    TrajectoryRecord(
                        question=item.question,
                        expected=item.expected_answer,
                        output="",
                        score=0.0,
                        conversation_history=[],
                        memory_logs=f"Runtime violation: {eval_result.runtime_violation}",
                    )
                    for item in batch
                ]
                if capture_traces
                else None,
            )

        scores = eval_result.per_case_scores
        outputs = eval_result.per_case_outputs or [""] * n

        trajectories = None
        if capture_traces:
            # Build trajectory from failed + success cases, indexed by question
            case_map: dict[str, Any] = {}
            for fc in eval_result.failed_cases:
                case_map[fc.question] = fc
            for sc in eval_result.success_cases:
                case_map[sc.question] = sc

            trajectories = []
            for i, item in enumerate(batch):
                case = case_map.get(item.question)
                trajectories.append(
                    TrajectoryRecord(
                        question=item.question,
                        expected=item.expected_answer,
                        output=outputs[i] if i < len(outputs) else "",
                        score=scores[i] if i < len(scores) else 0.0,
                        conversation_history=case.conversation_history if case else [],
                        memory_logs=case.memory_logs if case else "",
                    )
                )

        print(f"  [GEPA] Batch score: {sum(scores) / len(scores):.3f} ({len(batch)} items)")
        return EvaluationBatch(
            outputs=outputs,
            scores=scores,
            trajectories=trajectories,
        )

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch,
        components_to_update: list[str],
    ) -> Mapping[str, Sequence[Mapping[str, Any]]]:
        records: list[dict[str, Any]] = []

        if eval_batch.trajectories is None:
            return {comp: [] for comp in components_to_update}

        for traj, score in zip(eval_batch.trajectories, eval_batch.scores, strict=False):
            if not isinstance(traj, TrajectoryRecord):
                continue
            records.append(
                {
                    "Inputs": f"Question: {traj.question}",
                    "Generated Outputs": traj.output or "(empty)",
                    "Feedback": (
                        f"Score: {score:.3f}. Expected: {traj.expected}\nMemory logs: {traj.memory_logs[:500]}"
                    ),
                    "score": score,
                }
            )

        # Sort by score (worst first) so the LLM focuses on failures
        records.sort(key=lambda r: r["score"])
        return dict.fromkeys(components_to_update, records)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run GEPA baseline on Engram benchmarks")
    parser.add_argument("--dataset", default="locomo", help="Dataset name")
    parser.add_argument("--category", default=None, help="Dataset category filter")
    parser.add_argument("--seed-program", type=Path, required=True, help="Seed KBProgram .py file")
    parser.add_argument("--task-model", default="openrouter/deepseek/deepseek-v3.2", help="Task agent model")
    parser.add_argument("--reflection-model", default="openrouter/openai/gpt-5.3-codex", help="GEPA reflection model")
    parser.add_argument("--toolkit-model", default="openrouter/deepseek/deepseek-v3.2", help="Toolkit LLM model")
    parser.add_argument("--max-metric-calls", type=int, default=200, help="Max GEPA metric calls (default: 200)")
    parser.add_argument("--reflection-minibatch-size", type=int, default=5, help="Reflection minibatch size")
    parser.add_argument("--test-size", type=int, default=-1, help="Held-out test split size (-1 = copy full val)")
    parser.add_argument("--test-train-ratio", type=int, default=-1, help="Train items per test item for test eval")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--batch-concurrency", type=int, default=64, help="Max concurrent LLM calls")
    parser.add_argument("--run-dir", type=str, default=None, help="Output directory (default: auto-generated)")
    args, extra = parser.parse_known_args()

    # Parse benchmark kwargs
    dataset_kwargs: dict = {}
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
        dataset_kwargs[key] = value

    random.seed(args.seed)
    configure_cache("disk")
    set_batch_pool_size(args.batch_concurrency)

    # Load dataset
    dataset = load_dataset(args.dataset, category=args.category, **dataset_kwargs)
    split_val_test(dataset, test_size=args.test_size, seed=args.seed)

    print(f"Dataset: {args.dataset}, train={len(dataset.train)}, val={len(dataset.val)}, test={len(dataset.test)}")

    # Load seed program
    seed_source = args.seed_program.read_text()
    st = smoke_test(seed_source)
    if not st.success:
        print(f"Error: seed program failed smoke test: {st.error}", file=sys.stderr)
        sys.exit(1)
    print(f"Seed program: {args.seed_program.name}")

    # Build evaluator
    scorer = dataset.scorer or TokenF1Scorer()
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=scorer,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
        val_scorer=dataset.val_scorer,
    )

    # Build adapter
    adapter = EngramGEPAAdapter(
        train_items=dataset.train,
        evaluator=evaluator,
    )

    # Run dir
    if args.run_dir is None:
        from datetime import datetime

        ts = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
        run_dir = f"outputs/gepa-{args.dataset}-{ts}"
    else:
        run_dir = args.run_dir

    print(f"Run dir: {run_dir}")
    print(f"Max metric calls: {args.max_metric_calls}")
    print(f"Reflection model: {args.reflection_model}")
    print(f"Task model: {args.task_model}")
    print("=" * 60)

    # Run GEPA optimization
    result = optimize(
        seed_candidate={"memory_program": seed_source},
        trainset=dataset.val,  # GEPA samples from these for reflection
        valset=dataset.val,  # GEPA tracks Pareto frontier on these
        adapter=adapter,
        reflection_lm=args.reflection_model,
        reflection_prompt_template={"memory_program": REFLECTION_PROMPT_TEMPLATE},
        candidate_selection_strategy="pareto",
        frontier_type="instance",
        reflection_minibatch_size=args.reflection_minibatch_size,
        max_metric_calls=args.max_metric_calls,
        run_dir=run_dir,
        seed=args.seed,
        skip_perfect_score=True,
        module_selector="all",  # single component, always update it
    )

    # Print results
    print("\n" + "=" * 60)
    print("GEPA optimization complete!")
    print(f"Total metric calls: {result.total_metric_calls}")
    print(f"Best validation score: {result.val_aggregate_scores[result.best_idx]:.3f}")
    print(f"Candidates explored: {len(result.candidates)}")

    best_code = result.best_candidate["memory_program"]
    print(f"\nBest program:\n{'=' * 60}")
    print(best_code)

    # Save best program
    Path(run_dir).mkdir(parents=True, exist_ok=True)
    (Path(run_dir) / "best_program.py").write_text(best_code)

    # Run test evaluation if test set exists
    if dataset.test:
        print(f"\n{'=' * 60}")
        print(f"Running test evaluation on {len(dataset.test)} held-out items...")

        # Determine test train items
        if args.test_train_ratio == -1:
            test_train = dataset.train
        else:
            # Simple subset: take ratio * test_size items
            n_train = min(args.test_train_ratio * len(dataset.test), len(dataset.train))
            test_train = dataset.train[:n_train]

        program = KBProgram(source_code=best_code)
        test_result = evaluator.evaluate(program, test_train, dataset.test)
        print(f"Test score: {test_result.score:.3f}")

        # Per-category breakdown
        if dataset.category_key:
            cat_scores: dict[str, list[float]] = {}
            for score_val, item in zip(test_result.per_case_scores, dataset.test, strict=False):
                cat = str(item.metadata.get(dataset.category_key, "unknown"))
                cat_scores.setdefault(cat, []).append(score_val)
            for cat, scores in sorted(cat_scores.items()):
                print(f"  {cat}: {sum(scores) / len(scores):.3f} ({len(scores)} items)")

        # Save test results
        test_summary = {
            "test_score": test_result.score,
            "test_items": len(dataset.test),
            "gepa_val_score": result.val_aggregate_scores[result.best_idx],
            "total_metric_calls": result.total_metric_calls,
            "candidates_explored": len(result.candidates),
        }
        (Path(run_dir) / "test_results.json").write_text(json.dumps(test_summary, indent=2))

    # Save full summary
    summary = {
        "dataset": args.dataset,
        "category": args.category,
        "seed_program": str(args.seed_program),
        "task_model": args.task_model,
        "reflection_model": args.reflection_model,
        "max_metric_calls": args.max_metric_calls,
        "total_metric_calls": result.total_metric_calls,
        "candidates_explored": len(result.candidates),
        "best_val_score": result.val_aggregate_scores[result.best_idx],
        "val_scores": result.val_aggregate_scores,
    }
    (Path(run_dir) / "summary.json").write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved to: {run_dir}")


if __name__ == "__main__":
    main()
