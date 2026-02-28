"""Entry point: python -m programmaticmemory.evolution

Runs evolution on the kv_memory benchmark for a quick end-to-end test.
"""

from __future__ import annotations

import argparse

from programmaticmemory.benchmarks.kv_memory import load_kv_memory
from programmaticmemory.evolution.evaluator import ExactMatchScorer, MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.toolkit import ToolkitConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Memory Program evolution")
    parser.add_argument("--iterations", type=int, default=5, help="Max evolution iterations")
    parser.add_argument("--num-items", type=int, default=10, help="Number of benchmark items")
    parser.add_argument("--difficulty", choices=["simple", "compound"], default="simple")
    parser.add_argument("--dataset-type", choices=["A", "B"], default="A")
    parser.add_argument("--task-model", default="openai/gpt-4o-mini", help="Model for task agent")
    parser.add_argument("--reflect-model", default="openai/gpt-4o", help="Model for reflection")
    parser.add_argument("--toolkit-model", default="openai/gpt-4o-mini", help="Model for toolkit LLM")
    args = parser.parse_args()

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

    # Run
    loop = EvolutionLoop(
        evaluator=evaluator,
        reflector=reflector,
        train_data=train,
        val_data=val,
        dataset_type=args.dataset_type,
        max_iterations=args.iterations,
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
