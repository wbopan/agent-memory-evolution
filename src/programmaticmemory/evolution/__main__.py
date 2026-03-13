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
from programmaticmemory.evolution.types import (
    Dataset,
    KBProgram,
    MaxSelection,
    RecencyDecaySelection,
    SoftmaxSelection,
)
from programmaticmemory.logging.experiment_tracker import ExperimentTracker
from programmaticmemory.logging.run_output import RunOutputManager


def split_val_test(dataset: Dataset, test_size: int, seed: int) -> None:
    """Split dataset.val into evolution-val and held-out test, mutating in place.

    Args:
        dataset: Dataset to mutate (sets dataset.val and dataset.test).
        test_size: -1 = copy full val as test (backward compat), 0 = no test,
                   N > 0 = hold out last N items after seeded shuffle.
        seed: Random seed for deterministic splitting.
    """
    if test_size < -1:
        print(f"Error: --test-size must be >= -1, got {test_size}", file=sys.stderr)
        sys.exit(1)
    if test_size == -1:
        dataset.test = list(dataset.val)
        return
    if test_size == 0:
        dataset.test = []
        return
    # test_size > 0
    if test_size >= len(dataset.val):
        print(
            f"Error: --test-size ({test_size}) must be < len(val) ({len(dataset.val)}), "
            f"would leave evolution-val empty",
            file=sys.stderr,
        )
        sys.exit(1)
    # Copy first — some benchmarks (kv_memory) share the same list object for train and val
    val_copy = list(dataset.val)
    rng = random.Random(seed)
    rng.shuffle(val_copy)
    dataset.val = val_copy[:-test_size]
    dataset.test = val_copy[-test_size:]


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
        choices=["softmax", "recency_decay", "max"],
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
    parser.add_argument(
        "--eval-strategy",
        choices=["full", "representative", "rotating"],
        default="representative",
        help="Evaluation strategy: full (every iter uses full data), "
        "representative (clustering-based fixed subset), rotating (batch rotation) (default: representative)",
    )
    parser.add_argument(
        "--eval-val-size",
        type=int,
        default=30,
        help="Val subset size for representative/rotating strategies (default: 30)",
    )
    parser.add_argument(
        "--eval-train-ratio",
        type=int,
        default=5,
        help="Train items per val item for representative/rotating strategies (default: 5)",
    )
    parser.add_argument(
        "--eval-top-k",
        type=int,
        default=3,
        help="Number of candidates for final revalidation in rotating strategy (default: 3)",
    )
    # Default seed-program: <repo>/seeds/
    _default_seed_program = Path(__file__).resolve().parents[3] / "seeds"
    parser.add_argument(
        "--seed-program",
        type=Path,
        default=_default_seed_program,
        help=f"Directory of .py seed programs or a single .py file (default: {_default_seed_program})",
    )
    parser.add_argument(
        "--test-size",
        type=int,
        default=-1,
        help="Held-out test split size: -1 = copy full val (default), 0 = skip final eval, N > 0 = hold out N items",
    )
    parser.add_argument(
        "--test-train-ratio",
        type=int,
        default=-1,
        help="Train items per test item for final/test evaluation: -1 = all train (default), N > 0 = facility location subset",
    )
    parser.add_argument(
        "--max-fix-attempts",
        type=int,
        default=3,
        help="Max compile-fix attempts per reflection (default: 3). Set to 0 to disable fix loop.",
    )
    parser.add_argument(
        "--freeze-instructions",
        action="store_true",
        default=False,
        help="Freeze instruction constants during evolution (ablation: only memory design evolves)",
    )
    args, extra = parser.parse_known_args()

    random.seed(args.seed)

    # Enable disk cache so repeated runs hit cache
    from programmaticmemory.cache import configure_cache

    configure_cache("disk")

    # Load dataset (includes scorer, etc.)
    dataset_kwargs = _parse_extra_kwargs(extra)
    dataset = load_dataset(args.dataset, category=args.category, **dataset_kwargs)

    # Split val into evolution-val + held-out test
    split_val_test(dataset, test_size=args.test_size, seed=args.seed)

    # Build eval strategy
    from programmaticmemory.evolution.strategies import FixedRepresentative, FullDataset, RotatingBatch

    if args.eval_strategy == "full":
        eval_strat = FullDataset(test_train_ratio=args.test_train_ratio)
    elif args.eval_strategy == "representative":
        eval_strat = FixedRepresentative(
            dataset,
            val_size=args.eval_val_size,
            train_val_ratio=args.eval_train_ratio,
            test_train_ratio=args.test_train_ratio,
        )
    elif args.eval_strategy == "rotating":
        from programmaticmemory.evolution.batching import build_eval_batches

        batches_list = build_eval_batches(
            dataset.train,
            dataset.val,
            num_batches=max(1, len(dataset.val) // args.eval_val_size),
            batch_train_val_ratio=args.eval_train_ratio,
        )
        eval_strat = RotatingBatch(batches_list, top_k=args.eval_top_k, test_train_ratio=args.test_train_ratio)

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
        f"test={len(dataset.test)}, task_model={args.task_model}, reflect_model={args.reflect_model}",
        header="CONFIG",
    )
    if args.category:
        logger.log(f"Category: {args.category}", header="CONFIG")
    elif dataset.available_categories:
        logger.log(f"Available categories: {', '.join(dataset.available_categories)}", header="CONFIG")
    logger.log(f"Eval strategy: {eval_strat.__class__.__name__}", header="CONFIG")
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
    reflector = Reflector(model=args.reflect_model, prompt_config=prompt_config, max_fix_attempts=args.max_fix_attempts)
    tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

    # Load seed programs (--seed-program accepts a directory or a single .py file)
    seed_path = args.seed_program
    if seed_path.is_file():
        seed_files = [seed_path]
    elif seed_path.is_dir():
        seed_files = sorted(seed_path.glob("*.py"))
        if not seed_files:
            print(f"Error: no .py files found in --seed-program: {seed_path}", file=sys.stderr)
            sys.exit(1)
    else:
        print(f"Error: --seed-program path not found: {seed_path}", file=sys.stderr)
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
    elif args.selection_strategy == "max":
        strategy = MaxSelection()
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
            eval_strategy=eval_strat,
            freeze_instructions=args.freeze_instructions,
        )
        state = loop.run()

    if state.final_scores:
        label = "held-out test set" if args.test_size > 0 else "full dataset"
        print(f"\nFinal evaluation ({label}):")
        for prog_hash, score in state.final_scores.items():
            print(f"  {prog_hash}: {score:.3f}")

    if state.test_scores:
        print("\nTest evaluation (held-out test set):")
        for prog_hash, score in state.test_scores.items():
            print(f"  {prog_hash}: {score:.3f}")

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
