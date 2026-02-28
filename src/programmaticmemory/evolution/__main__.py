"""Entry point: python -m programmaticmemory.evolution

Runs evolution on a chosen dataset. Benchmark-specific kwargs are passed as
positional `key=value` args (e.g. `num_items=10 difficulty=simple`).
"""

from __future__ import annotations

import argparse
import random
import sys

from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.logging.experiment_tracker import ExperimentTracker
from programmaticmemory.logging.run_output import RunOutputManager


def _parse_extra_kwargs(extra: list[str]) -> dict:
    """Parse `key=value` positional args into a dict with auto-coerced types."""
    kwargs: dict = {}
    for arg in extra:
        if "=" not in arg:
            print(f"Error: unrecognized argument: {arg}", file=sys.stderr)
            print("Benchmark-specific args must be key=value (e.g. num_items=10)", file=sys.stderr)
            sys.exit(1)
        key, value = arg.split("=", 1)
        # Auto-coerce: int > float > str
        for coerce in (int, float):
            try:
                value = coerce(value)
                break
            except ValueError:
                continue
        kwargs[key] = value
    return kwargs


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Memory Program evolution",
        epilog="Benchmark-specific args: pass as key=value after flags (e.g. num_items=10 difficulty=simple)",
    )
    parser.add_argument("--dataset", default="kv_memory", help="Dataset name (default: kv_memory)")
    parser.add_argument("--iterations", type=int, default=3, help="Max evolution iterations")
    parser.add_argument("--train-size", type=int, default=None, help="Limit train set size")
    parser.add_argument("--val-size", type=int, default=None, help="Limit val set size")
    parser.add_argument("--task-model", default="openrouter/deepseek/deepseek-v3.2", help="Model for task agent")
    parser.add_argument("--reflect-model", default="openrouter/deepseek/deepseek-v3.2", help="Model for reflection")
    parser.add_argument("--toolkit-model", default="openrouter/deepseek/deepseek-v3.2", help="Model for toolkit LLM")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-weave", action="store_true", help="Disable weave/wandb tracking")
    parser.add_argument("--no-output", action="store_true", help="Disable local output directory")
    parser.add_argument("--weave-project", default="programmaticmemory", help="Weave project name")
    args, extra = parser.parse_known_args()

    random.seed(args.seed)

    # Load dataset (includes scorer, eval_mode, etc.)
    dataset_kwargs = _parse_extra_kwargs(extra)
    dataset = load_dataset(args.dataset, train_size=args.train_size, val_size=args.val_size, **dataset_kwargs)

    # Configure
    scorer = dataset.scorer or ExactMatchScorer()
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=scorer,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
    )
    reflector = Reflector(model=args.reflect_model)
    tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

    # Local output directory
    output_manager = None
    if not args.no_output:
        output_manager = RunOutputManager(base_dir="outputs", config=vars(args))
        # Tee logger output to run.log
        from programmaticmemory.logging.logger import RichLogger, set_logger

        set_logger(RichLogger(log_file=output_manager.get_log_path()))

    # Run
    with tracker:
        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=args.iterations,
            tracker=tracker,
            output_manager=output_manager,
        )
        state = loop.run()

    if output_manager:
        output_manager.close()
        from programmaticmemory.logging.logger import get_logger

        get_logger().close()

    # Output
    print(f"\n{'=' * 60}")
    print("Evolution complete!")
    print(f"Best score: {state.best_score:.3f}")
    print(f"Iterations: {state.total_iterations}")
    print(f"Best program (gen {state.best_program.generation}, hash {state.best_program.hash}):")
    print(f"{'=' * 60}")
    print(state.best_program.source_code)


if __name__ == "__main__":
    main()
