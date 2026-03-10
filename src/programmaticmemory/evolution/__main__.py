"""Entry point: python -m programmaticmemory.evolution

Runs evolution on a chosen dataset. Benchmark-specific kwargs are passed as
positional `key=value` args (e.g. `num_items=10 difficulty=simple`).
"""

from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import ReflectionPromptConfig
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.sandbox import smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import KBProgram, RecencyDecaySelection, SoftmaxSelection
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
        description="Run Knowledge Base Program evolution",
        epilog="Benchmark-specific args: pass as key=value after flags (e.g. num_items=10 difficulty=simple)",
    )
    parser.add_argument("--dataset", default="kv_memory", help="Dataset name (default: kv_memory)")
    parser.add_argument("--iterations", type=int, default=3, help="Max evolution iterations")
    parser.add_argument(
        "--category",
        default=None,
        help="Filter dataset to a specific category/domain (locomo: conversation index, alfworld: task type)",
    )
    parser.add_argument("--train-size", type=int, default=None, help="Limit train set size")
    parser.add_argument("--val-size", type=int, default=None, help="Limit val set size")
    parser.add_argument("--task-model", default="openrouter/deepseek/deepseek-v3.2", help="Model for task agent")
    parser.add_argument("--reflect-model", default="openrouter/openai/gpt-5.3-codex", help="Model for reflection")
    parser.add_argument("--toolkit-model", default="openrouter/deepseek/deepseek-v3.2", help="Model for toolkit LLM")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-weave", action="store_true", help="Disable weave/wandb tracking")
    parser.add_argument("--no-output", action="store_true", help="Disable local output directory")
    parser.add_argument("--weave-project", default="programmaticmemory", help="Weave project name")
    parser.add_argument(
        "--reflection-max-failed-cases", type=int, default=3, help="Max failed cases in reflection prompt (default: 3)"
    )
    parser.add_argument(
        "--reflection-max-train-examples",
        type=int,
        default=1,
        help="Max training examples in reflection prompt (default: 1)",
    )
    parser.add_argument(
        "--reflection-max-memory-log-chars",
        type=int,
        default=0,
        help="Max chars for memory logs in reflection prompt, 0 to exclude (default: 0)",
    )
    parser.add_argument(
        "--selection-strategy",
        choices=["softmax", "recency_decay"],
        default="softmax",
        help="Parent selection strategy (default: softmax)",
    )
    parser.add_argument(
        "--selection-softmax-temperature",
        type=float,
        default=0.15,
        help="Softmax temperature for parent selection (default: 0.15, lower = more greedy)",
    )
    parser.add_argument(
        "--selection-recency-decay-rate",
        type=float,
        default=0.8,
        help="Decay rate per generation for recency_decay selection (default: 0.8)",
    )
    # Default seed-dir: <repo>/seeds/
    _default_seed_dir = Path(__file__).resolve().parents[3] / "seeds"
    parser.add_argument(
        "--seed-dir",
        type=Path,
        default=_default_seed_dir,
        help=f"Directory of .py seed programs to use as initial population (default: {_default_seed_dir})",
    )
    parser.add_argument(
        "--num-batches",
        type=int,
        default=0,
        help="Number of co-selected eval batches (0 = disabled, default: 0)",
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        default=0,
        help="Which batch to use (0-indexed, default: 0)",
    )
    args, extra = parser.parse_known_args()

    random.seed(args.seed)

    # Enable disk cache so repeated runs hit cache
    from programmaticmemory.cache import configure_cache

    configure_cache("disk")

    # Load dataset (includes scorer, etc.)
    dataset_kwargs = _parse_extra_kwargs(extra)
    dataset = load_dataset(
        args.dataset, category=args.category, train_size=args.train_size, val_size=args.val_size, **dataset_kwargs
    )

    # Apply co-selected batching if requested
    _batch_info = None
    if args.num_batches > 0:
        if args.batch_index >= args.num_batches:
            print(
                f"Error: --batch-index {args.batch_index} must be < --num-batches {args.num_batches}",
                file=sys.stderr,
            )
            sys.exit(1)
        from programmaticmemory.evolution.batching import build_eval_batches

        batches = build_eval_batches(
            dataset.train,
            dataset.val,
            num_batches=args.num_batches,
        )
        batch = batches[args.batch_index]
        _batch_info = {
            "index": args.batch_index,
            "total": args.num_batches,
            "train_size": len(batch.train_indices),
            "val_size": len(batch.val_indices),
            "coverage": batch.coverage,
        }
        dataset.train = [dataset.train[i] for i in batch.train_indices]
        dataset.val = [dataset.val[i] for i in batch.val_indices]

    from programmaticmemory.logging.logger import RichLogger, get_logger, set_logger

    # Set up logger with file tee before constructing evaluator/reflector
    # (they cache get_logger() in __init__, so the logger must be final by then)
    output_manager = None
    if not args.no_output:
        output_manager = RunOutputManager(base_dir="outputs", config=vars(args))
        set_logger(RichLogger(log_file=output_manager.get_log_path()))

    logger = get_logger()
    logger.log(
        f"Dataset={args.dataset}, train={len(dataset.train)}, val={len(dataset.val)}, "
        f"task_model={args.task_model}, reflect_model={args.reflect_model}",
        header="CONFIG",
    )
    if args.category:
        logger.log(f"Category: {args.category}", header="CONFIG")
    elif dataset.available_categories:
        logger.log(f"Available categories: {', '.join(dataset.available_categories)}", header="CONFIG")
    if _batch_info:
        logger.log(
            f"Using batch {_batch_info['index']}/{_batch_info['total']}: "
            f"train={_batch_info['train_size']}, val={_batch_info['val_size']}, "
            f"coverage={_batch_info['coverage']:.4f}",
            header="CONFIG",
        )
    if output_manager:
        logger.log(f"Output directory: {output_manager.run_dir}", header="CONFIG")

    # Configure
    scorer = dataset.scorer or ExactMatchScorer()
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=scorer,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
        val_scorer=dataset.val_scorer,
    )
    prompt_config = ReflectionPromptConfig(
        max_failed_cases=args.reflection_max_failed_cases,
        max_train_examples=args.reflection_max_train_examples,
        max_memory_log_chars=args.reflection_max_memory_log_chars,
    )
    reflector = Reflector(model=args.reflect_model, prompt_config=prompt_config)
    tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

    # Load seed programs
    if not args.seed_dir.is_dir():
        print(f"Error: --seed-dir is not a directory: {args.seed_dir}", file=sys.stderr)
        sys.exit(1)
    seed_files = sorted(args.seed_dir.glob("*.py"))
    if not seed_files:
        print(f"Error: no .py files found in --seed-dir: {args.seed_dir}", file=sys.stderr)
        sys.exit(1)
    initial_programs = []
    for f in seed_files:
        source = f.read_text()
        result = smoke_test(source)
        if not result.success:
            print(f"Error: invalid seed program {f.name}: {result.error}", file=sys.stderr)
            sys.exit(1)
        initial_programs.append(KBProgram(source_code=source))
        logger.log(f"Loaded seed: {f.name}", header="CONFIG")

    # Build selection strategy
    if args.selection_strategy == "recency_decay":
        strategy = RecencyDecaySelection(decay_rate=args.selection_recency_decay_rate)
    else:
        strategy = SoftmaxSelection(temperature=args.selection_softmax_temperature)

    # Run
    with tracker:
        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=initial_programs,
            max_iterations=args.iterations,
            strategy=strategy,
            tracker=tracker,
            output_manager=output_manager,
        )
        state = loop.run()

    if output_manager:
        output_manager.close()
        logger.close()

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
