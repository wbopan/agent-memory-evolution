"""Test-eval the 3 seed programs on a given STATE-Bench domain's test set.

Used to backfill customer_support seed test scores: that run's worktree (and
its gitignored outputs/) was cleaned up before seed_1/seed_2 were test-evaluated.
Seed programs are deterministic and domain-agnostic (only a cosmetic score
header differs between runs), so they can be taken from any surviving run dir.

Note: agent rollouts are non-deterministic, so this is a fresh sample — re-run
all three seeds together as one consistent batch; do not mix with older numbers.

Usage:
    uv run python scripts/_test_eval_seeds.py <domain> <seed_source_run_dir>
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

_VAL_SIZE = 70  # matches the codex runs (train=30 / val=70); test set is the official 50


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    domain = sys.argv[1]
    src_run = Path(sys.argv[2])
    config = json.loads((src_run / "config.json").read_text())

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
    dataset = load_dataset("state_bench", category=domain, seed=config["seed"], val_size=_VAL_SIZE)
    print(f"{domain}: train={len(dataset.train)} val={len(dataset.val)} test={len(dataset.test)}")

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

    results: list[tuple[str, float]] = []
    for name in ("seed_0", "seed_1", "seed_2"):
        code = (src_run / "programs" / f"{name}.py").read_text()
        program = KBProgram(source_code=code)
        print(f"\n=== test-eval {name} on {domain} ===")
        t0 = time.monotonic()
        result = evaluator.evaluate(program, dataset.train, dataset.test)
        print(f"  test_score={result.score:.3f}  ({time.monotonic() - t0:.0f}s)")
        results.append((name, result.score))

    print("\n" + "=" * 40)
    print(f"{domain} seed test scores:")
    for name, score in results:
        print(f"  {name}: {score:.3f}")
    print(f"  average: {sum(s for _, s in results) / len(results):.3f}")


if __name__ == "__main__":
    main()
