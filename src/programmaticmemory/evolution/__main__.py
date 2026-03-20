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
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator, set_batch_pool_size
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
    parser.add_argument("--toolkit-budget", type=int, default=1, help="LLM call budget per write/read (default: 1)")
    parser.add_argument(
        "--judge-model",
        default=None,
        help="Model for rubric-based scoring (HealthBench, PRBench). Defaults to --task-model if not set.",
    )
    parser.add_argument(
        "--task-lm-thinking-effort",
        choices=["low", "medium", "high"],
        default=None,
        help="Reasoning effort for task/toolkit LLM calls (default: None, no thinking)",
    )
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
        choices=["none", "full", "representative", "rotating", "split"],
        default="representative",
        help="Evaluation strategy: none (skip seed/iter eval, test only), full (every iter uses full data), "
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
    parser.add_argument(
        "--eval-static-size",
        type=int,
        default=25,
        help="Static val size for split strategy — used for scoring/selection (default: 25)",
    )
    parser.add_argument(
        "--eval-rotate-size",
        type=int,
        default=5,
        help="Rotate val sample size per iteration for split strategy — used for reflection (default: 5)",
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
    parser.add_argument(
        "--freeze-code",
        action="store_true",
        default=False,
        help="Freeze code structure during evolution (GEPA baseline: only instruction constants evolve)",
    )
    parser.add_argument(
        "--no-references",
        action="store_true",
        default=True,
        help="Disable cross-program reference context in reflection prompt (default: disabled)",
    )
    parser.add_argument(
        "--batch-concurrency",
        type=int,
        default=4,
        help="Max concurrent LLM calls in batch evaluation (default: 4)",
    )
    parser.add_argument(
        "--resume",
        type=Path,
        default=None,
        help="Resume an interrupted run from the given output directory (e.g. outputs/2026-03-20-09-46-42/)",
    )
    args, extra = parser.parse_known_args()

    if args.freeze_instructions and args.freeze_code:
        print("Error: --freeze-instructions and --freeze-code are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    if args.resume is not None:
        import base64
        import json as _json
        import pickle

        from programmaticmemory.evolution.checkpoint import deserialize_pool_entry
        from programmaticmemory.evolution.types import EvolutionRecord, EvolutionState, ProgramPool

        resume_dir = Path(args.resume)
        if not resume_dir.is_dir():
            print(f"Error: --resume directory does not exist: {resume_dir}", file=sys.stderr)
            sys.exit(1)
        config_path = resume_dir / "config.json"
        state_path = resume_dir / "state.json"
        if not config_path.exists():
            print(f"Error: --resume directory missing config.json: {resume_dir}", file=sys.stderr)
            sys.exit(1)
        if not state_path.exists():
            print(f"Error: --resume directory missing state.json: {resume_dir}", file=sys.stderr)
            sys.exit(1)

        # Load checkpoint
        checkpoint = RunOutputManager.load_checkpoint(resume_dir)

        # Load saved config and override args (but keep user-supplied --iterations)
        saved_config = _json.loads(config_path.read_text(encoding="utf-8"))
        # Detect if user explicitly passed --iterations (vs parser default)
        user_passed_iterations = "--iterations" in sys.argv
        for key, value in saved_config.items():
            if hasattr(args, key):
                setattr(args, key, value)
        if user_passed_iterations:
            # Re-parse just --iterations from CLI to get user's explicit value
            iter_parser = argparse.ArgumentParser()
            iter_parser.add_argument("--iterations", type=int)
            iter_args, _ = iter_parser.parse_known_args()
            if iter_args.iterations is not None:
                args.iterations = iter_args.iterations

        # Restore random state
        random.setstate(pickle.loads(base64.b64decode(checkpoint["random_state"])))
        set_batch_pool_size(args.batch_concurrency)

        # Enable disk cache
        from programmaticmemory.cache import configure_cache

        configure_cache("disk")

        # Load dataset
        dataset_kwargs = _parse_extra_kwargs(extra)
        # Extra kwargs may also have been saved in the config as individual keys;
        # for resume we rely on the user passing the same positional kwargs if any
        judge_model = args.judge_model if args.judge_model is not None else args.task_model
        dataset_kwargs.setdefault("judge_model", judge_model)
        dataset = load_dataset(args.dataset, category=args.category, **dataset_kwargs)

        # Split val/test with same params
        split_val_test(dataset, test_size=args.test_size, seed=args.seed)

        # Restore eval strategy
        eval_strategy_state = checkpoint.get("eval_strategy_state")
        if eval_strategy_state is not None:
            strategy_type = eval_strategy_state.get("type")
            if strategy_type == "SplitValidation":
                from programmaticmemory.evolution.strategies import SplitValidation

                eval_strat = SplitValidation.from_state(eval_strategy_state, dataset)
            elif strategy_type == "FixedRepresentative":
                from programmaticmemory.evolution.strategies import FixedRepresentative

                eval_strat = FixedRepresentative.from_state(eval_strategy_state, dataset)
            else:
                # Unknown type — recreate from args
                eval_strategy_state = None

        if eval_strategy_state is None:
            from programmaticmemory.evolution.strategies import FixedRepresentative, FullDataset, RotatingBatch

            if args.eval_strategy == "none":
                from programmaticmemory.evolution.strategies import NoEval

                eval_strat = NoEval(test_train_ratio=args.test_train_ratio)
            elif args.eval_strategy == "full":
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
            elif args.eval_strategy == "split":
                from programmaticmemory.evolution.strategies import SplitValidation

                eval_strat = SplitValidation(
                    dataset,
                    static_size=args.eval_static_size,
                    rotate_size=args.eval_rotate_size,
                    train_val_ratio=args.eval_train_ratio,
                    test_train_ratio=args.test_train_ratio,
                )
            else:
                eval_strat = FullDataset(test_train_ratio=args.test_train_ratio)

        # Build selection strategy
        if args.selection_strategy == "recency_decay":
            strategy = RecencyDecaySelection(decay_rate=args.selection_recency_decay_rate)
        elif args.selection_strategy == "max":
            strategy = MaxSelection()
        else:
            strategy = SoftmaxSelection(temperature=args.selection_softmax_temperature)

        # Reconstruct pool from saved programs
        programs_dir = resume_dir / "programs"
        pool_entries_data = checkpoint.get("pool", [])
        resumed_pool = ProgramPool(strategy=strategy)
        hash_to_program: dict = {}
        for entry_data in pool_entries_data:
            name = entry_data["name"]
            prog_file = programs_dir / f"{name}.py"
            if not prog_file.exists():
                continue
            raw = prog_file.read_text(encoding="utf-8")
            # Strip header comment (first line starting with "# ")
            lines = raw.split("\n")
            if lines and lines[0].startswith("# "):
                source = "\n".join(lines[1:]).lstrip("\n")
            else:
                source = raw
            entry = deserialize_pool_entry(entry_data, source)
            resumed_pool.entries.append(entry)
            hash_to_program[entry.program.hash] = entry.program

        # Reconstruct EvolutionState history from checkpoint
        score_history = checkpoint.get("score_history", [])
        history: list[EvolutionRecord] = []
        for rec in score_history:
            prog_hash = rec.get("program_hash")
            program = hash_to_program.get(prog_hash, resumed_pool.best.program)
            history.append(
                EvolutionRecord(
                    iteration=rec["iteration"],
                    program=program,
                    score=rec["score"],
                    parent_hash=rec.get("parent_hash"),
                )
            )

        last_iteration = checkpoint.get("last_completed_iteration", 0)
        best_score = checkpoint.get("best_score", resumed_pool.best.score)

        resumed_state = EvolutionState(
            pool=resumed_pool,
            best_score=best_score,
            history=history,
            total_iterations=last_iteration,
        )

        # Set up logger + output manager
        from programmaticmemory.logging.logger import RichLogger, get_logger, set_logger

        output_manager = None
        if not args.no_output:
            output_manager = RunOutputManager.from_existing(resume_dir)
            set_logger(RichLogger(log_file=output_manager.get_log_path()))

        logger = get_logger()
        logger.log(
            f"RESUME: dataset={args.dataset}, last_iter={last_iteration}, best={best_score:.3f}, "
            f"pool={len(resumed_pool)}, target_iter={args.iterations}",
            header="CONFIG",
        )

        # Build evaluator / reflector / tracker
        scorer = dataset.scorer or ExactMatchScorer()
        toolkit_config = ToolkitConfig(
            llm_model=args.toolkit_model,
            reasoning_effort=args.task_lm_thinking_effort,
            llm_call_budget=args.toolkit_budget,
        )
        evaluator = MemoryEvaluator(
            scorer=scorer,
            task_model=args.task_model,
            toolkit_config=toolkit_config,
            val_scorer=dataset.val_scorer,
            reasoning_effort=args.task_lm_thinking_effort,
        )
        prompt_config = ReflectionPromptConfig(
            max_failed_cases=args.reflection_max_failed_cases,
            max_train_examples=args.reflection_max_train_examples,
            max_memory_log_chars=args.reflection_max_memory_log_chars,
        )
        reflector = Reflector(
            model=args.reflect_model, prompt_config=prompt_config, max_fix_attempts=args.max_fix_attempts
        )
        tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

        with tracker:
            loop = EvolutionLoop(
                evaluator=evaluator,
                reflector=reflector,
                dataset=dataset,
                initial_programs=[],
                max_iterations=args.iterations,
                strategy=strategy,
                tracker=tracker,
                output_manager=output_manager,
                eval_strategy=eval_strat,
                freeze_instructions=args.freeze_instructions,
                freeze_code=args.freeze_code,
                use_references=not args.no_references,
                start_iteration=last_iteration,
                resumed_pool=resumed_pool,
                resumed_state=resumed_state,
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
            from programmaticmemory.logging.logger import get_logger as _get_logger

            _get_logger().close()

        print(f"\n{'=' * 60}")
        print("Evolution complete!")
        print(f"Best score: {state.best_score:.3f}")
        print(f"Iterations: {state.total_iterations}")
        print(f"Best program (gen {state.best_program.generation}, hash {state.best_program.hash}):")
        print(f"{'=' * 60}")
        print(state.best_program.source_code)
        return

    random.seed(args.seed)
    set_batch_pool_size(args.batch_concurrency)

    # Enable disk cache so repeated runs hit cache
    from programmaticmemory.cache import configure_cache

    configure_cache("disk")

    # Load dataset (includes scorer, etc.)
    dataset_kwargs = _parse_extra_kwargs(extra)
    judge_model = args.judge_model if args.judge_model is not None else args.task_model
    dataset_kwargs.setdefault("judge_model", judge_model)
    dataset = load_dataset(args.dataset, category=args.category, **dataset_kwargs)

    # Split val into evolution-val + held-out test
    split_val_test(dataset, test_size=args.test_size, seed=args.seed)

    # Build eval strategy
    from programmaticmemory.evolution.strategies import FixedRepresentative, FullDataset, RotatingBatch

    if args.eval_strategy == "none":
        from programmaticmemory.evolution.strategies import NoEval

        eval_strat = NoEval(test_train_ratio=args.test_train_ratio)
    elif args.eval_strategy == "full":
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
    elif args.eval_strategy == "split":
        from programmaticmemory.evolution.strategies import SplitValidation

        eval_strat = SplitValidation(
            dataset,
            static_size=args.eval_static_size,
            rotate_size=args.eval_rotate_size,
            train_val_ratio=args.eval_train_ratio,
            test_train_ratio=args.test_train_ratio,
        )

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
    toolkit_config = ToolkitConfig(
        llm_model=args.toolkit_model, reasoning_effort=args.task_lm_thinking_effort, llm_call_budget=args.toolkit_budget
    )
    evaluator = MemoryEvaluator(
        scorer=scorer,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
        val_scorer=dataset.val_scorer,
        reasoning_effort=args.task_lm_thinking_effort,
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
    seed_commit_messages: list[str | None] = []
    for f in seed_files:
        source = f.read_text()
        result = smoke_test(source)
        if not result.success:
            print(f"Error: invalid seed program {f.name}: {result.error}", file=sys.stderr)
            sys.exit(1)
        initial_programs.append(KBProgram(source_code=source))
        # Extract COMMIT_MESSAGE constant if present
        commit_msg = None
        try:
            ns: dict = {}
            exec(compile(source, f.name, "exec"), ns)
            commit_msg = ns.get("COMMIT_MESSAGE")
        except Exception:
            pass
        seed_commit_messages.append(commit_msg)
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
            freeze_code=args.freeze_code,
            use_references=not args.no_references,
            seed_commit_messages=seed_commit_messages,
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
