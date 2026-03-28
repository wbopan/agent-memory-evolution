#!/usr/bin/env python3
"""Re-score existing LoCoMo runs with LLM-judge metric.

Reads per-item eval results from failed_cases.json, calls LLMJudgeScorer on each
(output, expected_answer) pair, and patches summary.json with the new metric.

For runs with corrupted failed_cases.json (e.g. t1-locomo-ours where a second final
eval overwrote the file), use --reeval to re-run the evaluation pipeline and get
correct per-item outputs before LLM-judge scoring.

Usage:
    uv run python scripts/rescore_llm_judge.py                    # dry-run
    uv run python scripts/rescore_llm_judge.py --apply            # write to summary.json
    uv run python scripts/rescore_llm_judge.py --apply --force    # overwrite existing llm_judge
    uv run python scripts/rescore_llm_judge.py --runs t1-locomo-ours bl-locomo-mem0  # specific runs
    uv run python scripts/rescore_llm_judge.py --reeval --apply   # re-evaluate corrupted runs
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
from pathlib import Path

# Project imports
from programmaticmemory.benchmarks.locomo import load_locomo
from programmaticmemory.evolution.__main__ import split_val_test
from programmaticmemory.evolution.evaluator import MemoryEvaluator, TokenF1Scorer
from programmaticmemory.evolution.toolkit import ToolkitConfig, completion_with_retry
from programmaticmemory.evolution.types import Dataset, KBProgram

OUTPUTS_DIR = Path(__file__).resolve().parent.parent / "outputs"
JUDGE_MODEL = "azure/gpt-5.4-mini"
TASK_MODEL = "azure/gpt-5.4-mini"


class LLMJudgeScorer:
    """LLM-as-judge scorer: 1 if the output contains the key information from the expected answer, 0 otherwise."""

    def __init__(self, model: str) -> None:
        self.model = model

    def __call__(self, output: str, expected: str) -> tuple[float, str]:
        response = completion_with_retry(
            model=self.model,
            messages=[
                {"role": "system", "content": " "},
                {
                    "role": "user",
                    "content": (
                        "You are an answer correctness judge. Determine if the output contains the key "
                        "information from the expected answer.\n\n"
                        "Rules:\n"
                        "- Score 1 if the output captures the core meaning of the expected answer, even if "
                        "worded differently, abbreviated, or missing minor details.\n"
                        "- Score 1 if the expected answer is a list and the output covers the main items "
                        "(missing one or two minor items is OK).\n"
                        "- Score 0 if the output is wrong, irrelevant, or missing the key information.\n"
                        "- Score 0 if the output says 'not found' or equivalent.\n\n"
                        f"Expected answer: {expected}\n"
                        f"Actual output: {output}\n\n"
                        "Reply ONLY with 1 or 0."
                    ),
                },
            ],
            caching=True,
        )
        text = response.choices[0].message.content.strip()
        try:
            score = float(int(text))
        except (ValueError, TypeError):
            score = 0.0
        verdict = "correct" if score >= 1.0 else "incorrect"
        return (
            score,
            f'LLM judge (correctness). Verdict: {verdict}. Expected: "{expected}"',
        )
MAX_WORKERS = 32

# ---------------------------------------------------------------------------
# Dataset loading — build question → (expected_answer, category) map
# ---------------------------------------------------------------------------


def _build_question_map(seed: int = 42, test_size: int = 100) -> dict[str, tuple[str, int | None]]:
    """Load LoCoMo and return {question: (expected_answer, qa_category)} for the test split."""
    dataset = load_locomo()
    split_val_test(dataset, test_size=test_size, seed=seed)
    qmap: dict[str, tuple[str, int | None]] = {}
    for item in dataset.test:
        cat = item.metadata.get("qa_category") if item.metadata else None
        qmap[item.question] = (item.expected_answer, cat)
    return qmap


# Cache to avoid reloading for same (seed, test_size)
_QMAP_CACHE: dict[tuple[int, int], dict[str, tuple[str, int | None]]] = {}


def _get_question_map(seed: int = 42, test_size: int = 100) -> dict[str, tuple[str, int | None]]:
    """Cached version of _build_question_map."""
    key = (seed, test_size)
    if key not in _QMAP_CACHE:
        _QMAP_CACHE[key] = _build_question_map(seed=seed, test_size=test_size)
    return _QMAP_CACHE[key]


# ---------------------------------------------------------------------------
# Parse expected answer from rationale string (fallback)
# ---------------------------------------------------------------------------

_EXPECTED_RE = re.compile(r'Expected answer:\s*"(.*)"', re.DOTALL)


def _parse_expected_from_rationale(rationale: str) -> str | None:
    m = _EXPECTED_RE.search(rationale)
    return m.group(1) if m else None


# ---------------------------------------------------------------------------
# Discover runs
# ---------------------------------------------------------------------------


def _is_corrupted(cases: list[dict]) -> bool:
    """Detect corrupted eval files where all outputs are empty/not-found."""
    if not cases:
        return True
    empty_count = sum(1 for c in cases if not c["output"] or "not found" in c["output"].lower())
    return empty_count == len(cases)


def _reeval_run(
    run_dir: Path,
    dataset: Dataset,
) -> list[dict] | None:
    """Re-run evaluation for a corrupted run. Returns list of per-item case dicts."""
    config_path = run_dir / "config.json"
    config = json.loads(config_path.read_text()) if config_path.exists() else {}
    program_hash = None

    # Find the best program hash from summary
    summary = json.loads((run_dir / "summary.json").read_text())
    fe = summary.get("final_evaluation")
    if fe and fe.get("candidates"):
        program_hash = fe["candidates"][0]["hash"]
    if not program_hash:
        print(f"  SKIP re-eval {run_dir.name}: cannot determine best program hash")
        return None

    # Find the program source file
    program_file = None
    # Check best_program.py first (GEPA runs store the best program here directly)
    best_prog = run_dir / "best_program.py"
    if best_prog.exists():
        program_file = best_prog
    else:
        for p in (run_dir / "programs").glob("*.py"):
            text = p.read_text()
            if program_hash[:8] in text or program_hash in text:
                program_file = p
                break
        if not program_file:
            # Try matching by hash in meta.json
            for eval_dir in (run_dir / "evals").iterdir():
                if eval_dir.name.endswith("_reflect"):
                    continue
                meta_path = eval_dir / "meta.json"
                if meta_path.exists():
                    meta = json.loads(meta_path.read_text())
                    if meta.get("program_hash") == program_hash:
                        program_file = run_dir / "programs" / f"{eval_dir.name}.py"
                        break
    if not program_file or not program_file.exists():
        print(f"  SKIP re-eval {run_dir.name}: cannot find program for {program_hash}")
        return None

    source = program_file.read_text()
    # Strip header comment
    lines = source.split("\n")
    if lines[0].startswith("# "):
        source = "\n".join(lines[1:])

    print(f"  Re-evaluating {run_dir.name} with program from {program_file.name}...")

    # Patch _ThreadSafeSQLiteConnection to forward __setattr__ for row_factory etc.
    # The original runs didn't have this wrapper; programs that set self.db.row_factory
    # would silently set it on the proxy instead of the underlying connection.
    from programmaticmemory.evolution.toolkit import _ThreadSafeSQLiteConnection

    if not hasattr(_ThreadSafeSQLiteConnection, "_patched_setattr"):

        def _proxy_setattr(self: object, name: str, value: object) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
            else:
                setattr(object.__getattribute__(self, "_conn"), name, value)

        _ThreadSafeSQLiteConnection.__setattr__ = _proxy_setattr  # type: ignore[assignment]
        _ThreadSafeSQLiteConnection._patched_setattr = True  # type: ignore[attr-defined]

    program = KBProgram(source_code=source)
    task_model = config.get("task_model", TASK_MODEL)
    toolkit_config = ToolkitConfig(
        llm_model=config.get("toolkit_model", task_model),
        llm_call_budget=config.get("toolkit_budget", 1),
    )
    evaluator = MemoryEvaluator(
        task_model=task_model,
        toolkit_config=toolkit_config,
        compare_fn=TokenF1Scorer(),
        reasoning_effort=config.get("task_lm_thinking_effort"),
    )

    eval_result = evaluator.evaluate(program, dataset.train, dataset.test)

    # Build case dicts with outputs
    cases = []
    for i, item in enumerate(dataset.test):
        output = eval_result.per_case_outputs[i] if i < len(eval_result.per_case_outputs) else ""
        score = eval_result.per_case_scores[i] if i < len(eval_result.per_case_scores) else 0.0
        cases.append({
            "question": item.question,
            "output": output,
            "expected_answer": item.expected_answer,
            "score": score,
        })

    print(f"  Re-eval complete: {len(cases)} items, avg Token F1={sum(c['score'] for c in cases)/len(cases):.3f}")
    return cases


def _find_locomo_runs(outputs_dir: Path, run_filter: list[str] | None = None) -> list[Path]:
    """Return sorted list of locomo run directories."""
    runs = []
    for d in sorted(outputs_dir.iterdir()):
        if not d.is_dir() or "locomo" not in d.name:
            continue
        if run_filter and d.name not in run_filter:
            continue
        if not (d / "summary.json").exists():
            continue
        runs.append(d)
    return runs


def _find_eval_cases(run_dir: Path) -> tuple[Path | None, str]:
    """Find the failed_cases.json for final or test eval. Returns (path, eval_type)."""
    # Final eval (ours / ablation)
    llm_calls = run_dir / "llm_calls"
    if not llm_calls.exists():
        return None, "none"
    for d in sorted(llm_calls.iterdir()):
        if d.name.startswith("final_") and (d / "failed_cases.json").exists():
            return d / "failed_cases.json", "final"
    # Test eval (baselines)
    test_fc = llm_calls / "test" / "failed_cases.json"
    if test_fc.exists():
        return test_fc, "test"
    return None, "none"


# ---------------------------------------------------------------------------
# Re-scoring
# ---------------------------------------------------------------------------


def _rescore_run(
    run_dir: Path,
    scorer: LLMJudgeScorer,
    *,
    force: bool = False,
    reeval: bool = False,
) -> dict | None:
    """Re-score a single run. Returns patch dict for summary.json, or None if skipped."""
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text())

    # Read per-run config for seed/test_size
    config_path = run_dir / "config.json"
    if config_path.exists():
        config = json.loads(config_path.read_text())
        seed = config.get("seed", 42)
        test_size = config.get("test_size", 100)
    else:
        seed, test_size = 42, 100
    question_map = _get_question_map(seed=seed, test_size=test_size)

    cases_path, eval_type = _find_eval_cases(run_dir)
    if cases_path is None and not reeval:
        print(f"  SKIP {run_dir.name}: no eval cases found (use --reeval to run evaluation)")
        return None

    # Check if already scored
    if eval_type == "final":
        eval_key = "final_evaluation"
    elif eval_type == "test":
        eval_key = "test_evaluation"
    else:
        # Infer from summary: prefer final_evaluation if present (e.g. GEPA runs)
        eval_key = "final_evaluation" if summary.get("final_evaluation") else "test_evaluation"
    eval_block = summary.get(eval_key)
    if eval_block is None:
        print(f"  SKIP {run_dir.name}: no {eval_key} in summary.json")
        return None

    extra = eval_block.get("extra_metrics", {})
    # extra_metrics is {program_hash: {metric: value}}
    program_hash = next(iter(extra)) if extra else None
    if program_hash and "llm_judge" in extra.get(program_hash, {}) and not force:
        print(f"  SKIP {run_dir.name}: already has llm_judge (use --force to overwrite)")
        return None

    # If no extra_metrics yet, find the program hash from the eval block
    if not program_hash:
        if eval_type == "final":
            candidates = eval_block.get("candidates", [])
            program_hash = candidates[0]["hash"] if candidates else None
        else:
            scores = eval_block.get("scores", {})
            program_hash = next(iter(scores)) if scores else None
    if not program_hash:
        print(f"  SKIP {run_dir.name}: cannot determine program hash")
        return None

    # Load eval cases — check for corruption
    no_cases = cases_path is None
    cases = [] if no_cases else json.loads(cases_path.read_text())  # type: ignore[union-attr]
    corrupted = no_cases or _is_corrupted(cases)

    if corrupted and not reeval:
        if no_cases:
            print(f"  SKIP {run_dir.name}: no failed_cases.json (use --reeval to run evaluation)")
        else:
            print(f"  SKIP {run_dir.name}: corrupted failed_cases.json (all outputs empty). Use --reeval to fix.")
        return None

    if corrupted and reeval:
        dataset = load_locomo()
        split_val_test(dataset, test_size=test_size, seed=seed)
        reeval_cases = _reeval_run(run_dir, dataset)
        if reeval_cases is None:
            return None
        # Use re-evaluated cases: they already have expected_answer from the dataset
        tasks: list[tuple[str, str, int | None]] = []
        for case in reeval_cases:
            q = case["question"]
            expected = case.get("expected_answer", "")
            cat: int | None = question_map[q][1] if q in question_map else None
            if not expected and q in question_map:
                expected = question_map[q][0]
            tasks.append((case["output"], expected, cat))
        missing_count = 0
        missing_cats: list[int | None] = []
    else:
        # Normal path: use saved failed_cases.json
        tasks = []
        matched_questions: set[str] = set()

        for case in cases:
            q = case["question"]
            output = case["output"]
            matched_questions.add(q)

            if q in question_map:
                expected, cat = question_map[q]
            else:
                # Fallback: parse from rationale
                expected = _parse_expected_from_rationale(case.get("rationale", ""))
                cat = None
                if not expected:
                    print(f"  WARN {run_dir.name}: cannot find expected answer for: {q[:60]}...")
                    continue

            tasks.append((output, expected, cat))

        # Items missing from failed_cases (perfect Token F1 = 1.0 → LLM judge = 1.0)
        missing_count = 0
        missing_cats = []
        for q, (_exp, cat) in question_map.items():
            if q not in matched_questions:
                missing_count += 1
                missing_cats.append(cat)

    # Score with LLM judge (parallel)
    print(f"  Scoring {run_dir.name}: {len(tasks)} items to judge, {missing_count} assumed perfect...")
    scores_and_cats: list[tuple[float, int | None]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(scorer, output, expected): cat for output, expected, cat in tasks}
        for future in concurrent.futures.as_completed(futures):
            cat = futures[future]
            score, _rationale = future.result()
            scores_and_cats.append((score, cat))

    # Add perfect-score items
    for cat in missing_cats:
        scores_and_cats.append((1.0, cat))

    # Aggregate
    all_scores = [s for s, _ in scores_and_cats]
    avg_score = sum(all_scores) / len(all_scores) if all_scores else 0.0

    # Per-category
    cat_scores: dict[str, list[float]] = {}
    for score, cat in scores_and_cats:
        if cat is not None:
            cat_scores.setdefault(str(cat), []).append(score)
    cat_avgs = {cat: sum(ss) / len(ss) for cat, ss in cat_scores.items()}

    print(f"  Result: llm_judge={avg_score:.3f}, categories={cat_avgs}")

    # Build patch
    if eval_key not in summary:
        summary[eval_key] = {}
    if "extra_metrics" not in summary[eval_key]:
        summary[eval_key]["extra_metrics"] = {}
    if program_hash not in summary[eval_key]["extra_metrics"]:
        summary[eval_key]["extra_metrics"][program_hash] = {}
    summary[eval_key]["extra_metrics"][program_hash]["llm_judge"] = round(avg_score, 4)

    if "category_scores" not in summary[eval_key]:
        summary[eval_key]["category_scores"] = {}
    # Store LLM judge category scores alongside existing ones (which are Token F1)
    summary[eval_key].setdefault("llm_judge_category_scores", {})
    summary[eval_key]["llm_judge_category_scores"][program_hash] = {
        cat: round(avg, 4) for cat, avg in cat_avgs.items()
    }

    return summary


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Re-score LoCoMo runs with LLM judge")
    parser.add_argument("--apply", action="store_true", help="Write changes to summary.json (default: dry-run)")
    parser.add_argument("--force", action="store_true", help="Overwrite existing llm_judge scores")
    parser.add_argument("--reeval", action="store_true", help="Re-evaluate corrupted runs before scoring")
    parser.add_argument("--runs", nargs="*", help="Specific run names to process (default: all locomo)")
    parser.add_argument("--outputs-dir", type=Path, default=OUTPUTS_DIR, help="Outputs directory")
    parser.add_argument("--model", default=JUDGE_MODEL, help=f"Judge model (default: {JUDGE_MODEL})")
    args = parser.parse_args()

    scorer = LLMJudgeScorer(model=args.model)
    runs = _find_locomo_runs(args.outputs_dir, args.runs)
    print(f"Found {len(runs)} locomo runs\n")

    for run_dir in runs:
        result = _rescore_run(run_dir, scorer, force=args.force, reeval=args.reeval)
        if result is not None:
            summary_path = run_dir / "summary.json"
            if args.apply:
                tmp = summary_path.with_suffix(".json.tmp")
                tmp.write_text(json.dumps(result, indent=2) + "\n")
                tmp.rename(summary_path)
                print(f"  WRITTEN {summary_path}\n")
            else:
                print(f"  DRY-RUN: would write to {summary_path}\n")

    if not args.apply:
        print("Dry-run complete. Use --apply to write changes.")


if __name__ == "__main__":
    main()
