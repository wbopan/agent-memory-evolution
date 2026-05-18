"""Evaluate the top-N val-scoring programs of a finished evolution run, plus
every seed program, on the held-out official test set.

Experiment question: does a fully-evolved memory (KB) program actually improve
held-out test performance, or does it merely track val noise? The top-N pool
programs (by evolution score) and all seeds are each re-evaluated on
dataset.test with the exact train set they were evolved against — including all
seeds lets the seed test average be compared against the evolved programs.

Usage:
    uv run python scripts/_test_eval_top_programs.py <output_dir> [top_n]

<output_dir> is a state_bench run dir containing config.json, state.json and
programs/. top_n defaults to 3 (seeds are always added on top).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# The codex run was launched with the benchmark kwarg val_size=70 (train=30 /
# val=70). config.json does not record benchmark kwargs, so it is pinned here:
# the test set is the official 50 either way, but train MUST be the same 30
# items the programs were evolved against.
_RUN_VAL_SIZE = 70


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    out_dir = Path(sys.argv[1])
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 3

    config = json.loads((out_dir / "config.json").read_text())
    state = json.loads((out_dir / "state.json").read_text())

    # Configure mstar exactly like __main__ does.
    from mstar.cache import configure_cache
    from mstar.evolution.azure_config import configure_azure_auth

    configure_cache("disk")
    configure_azure_auth(
        models=[config["task_model"], config["toolkit_model"]],
        azure_api_base=config["azure_api_base"],
        azure_api_version=config["azure_api_version"],
    )

    from mstar.datasets import load_dataset
    from mstar.evolution.evaluator import MemoryEvaluator, set_batch_pool_size
    from mstar.evolution.toolkit import ToolkitConfig
    from mstar.evolution.types import KBProgram

    set_batch_pool_size(config["batch_concurrency"])

    # Reproduce the run's exact split (deterministic by seed + val_size).
    dataset = load_dataset(
        config["dataset"],
        category=config["category"],
        seed=config["seed"],
        val_size=_RUN_VAL_SIZE,
    )
    print(f"dataset: train={len(dataset.train)} val={len(dataset.val)} test={len(dataset.test)}")

    # Rank pool entries by evolution score, take top-N.
    pool = state["pool"]
    ranked = sorted(
        pool,
        key=lambda e: (e.get("eval_result") or {}).get("score") or 0.0,
        reverse=True,
    )
    selected: list[dict] = list(ranked[:top_n])
    selected_names = {e["name"] for e in selected}
    # Always also include every seed program, so the seed test average is known.
    for e in pool:
        if e["name"].startswith("seed_") and e["name"] not in selected_names:
            selected.append(e)
            selected_names.add(e["name"])
    print(f"selecting top-{top_n} by evolution score + all seeds ({len(selected)} programs):")
    for e in selected:
        print(f"  {e['name']:14s} evo_score={(e.get('eval_result') or {}).get('score')}  gen={e.get('program_generation')}")

    evaluator = MemoryEvaluator(
        compare_fn=dataset.compare_fn,
        task_model=config["task_model"],
        toolkit_config=ToolkitConfig(
            llm_model=config["toolkit_model"],
            reasoning_effort=None,
            llm_call_budget=config["toolkit_budget"],
        ),
        val_scorer=dataset.val_scorer,
        reasoning_effort=config["task_lm_thinking_effort"],
    )

    results: list[tuple[str, int, float, float]] = []
    for entry in selected:
        name = entry["name"]
        gen = entry.get("program_generation", -1)
        evo_score = (entry.get("eval_result") or {}).get("score") or 0.0
        code = (out_dir / "programs" / f"{name}.py").read_text()
        program = KBProgram(source_code=code)
        print(f"\n=== test-eval {name} (gen={gen}, evo_score={evo_score:.3f}) ===")
        t0 = time.monotonic()
        result = evaluator.evaluate(program, dataset.train, dataset.test)
        dt = time.monotonic() - t0
        print(f"  test_score={result.score:.3f}  ({dt:.0f}s)")
        results.append((name, gen, evo_score, result.score))

    print("\n" + "=" * 56)
    print(f"{'program':16s} {'gen':>4s} {'evo(val)':>9s} {'test':>7s}")
    print("-" * 56)
    for name, gen, evo, test in results:
        tag = "seed" if gen == 0 else "iter"
        print(f"{name:16s} {gen:>4d} {evo:>9.3f} {test:>7.3f}  [{tag}]")
    best_seed = max((t for n, g, e, t in results if g == 0), default=None)
    best_eved = max((t for n, g, e, t in results if g > 0), default=None)
    if best_seed is not None and best_eved is not None:
        delta = best_eved - best_seed
        verdict = "evolved BEATS seed" if delta > 0 else ("TIE" if delta == 0 else "evolved BELOW seed")
        print("-" * 56)
        print(f"best seed test={best_seed:.3f}  best evolved test={best_eved:.3f}  -> {verdict} ({delta:+.3f})")


if __name__ == "__main__":
    main()
