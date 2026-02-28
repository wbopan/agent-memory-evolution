"""Entry point: python -m programmaticmemory.evolution

Runs evolution on the kv_memory benchmark for a quick end-to-end test.
"""

from __future__ import annotations

import argparse
import random

from programmaticmemory.benchmarks.kv_memory import load_kv_memory
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.logging.experiment_tracker import ExperimentTracker


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Memory Program evolution")
    parser.add_argument("--iterations", type=int, default=5, help="Max evolution iterations")
    parser.add_argument("--num-items", type=int, default=10, help="Number of benchmark items")
    parser.add_argument("--difficulty", choices=["simple", "compound"], default="simple")
    parser.add_argument("--dataset-type", choices=["A", "B"], default="A")
    parser.add_argument("--task-model", default="openai/gpt-4o-mini", help="Model for task agent")
    parser.add_argument("--reflect-model", default="openai/gpt-4o", help="Model for reflection")
    parser.add_argument("--toolkit-model", default="openai/gpt-4o-mini", help="Model for toolkit LLM")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--no-weave", action="store_true", help="Disable weave/wandb tracking")
    parser.add_argument("--weave-project", default="programmaticmemory", help="Weave project name")
    args = parser.parse_args()

    # Set seed
    random.seed(args.seed)

    # Load data
    train, val, _ = load_kv_memory(num_items=args.num_items, difficulty=args.difficulty)

    # Configure
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=ExactMatchScorer(),
        task_model=args.task_model,
        toolkit_config=toolkit_config,
    )
    reflector = Reflector(model=args.reflect_model)
    tracker = ExperimentTracker(use_weave=not args.no_weave, weave_project_name=args.weave_project)

    # Run
    with tracker:
        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            dataset_type=args.dataset_type,
            max_iterations=args.iterations,
            tracker=tracker,
        )
        state = loop.run()

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
