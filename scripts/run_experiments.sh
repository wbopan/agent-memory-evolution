#!/usr/bin/env bash
# Run all experiments for Table 1 (main results) and Table 2 (ablation study).
# Each run writes to outputs/<timestamp>/ with summary.json containing per-category scores.
#
# Every evolution run uses --category: Engram evolves a per-task memory system,
# so each category gets its own evolution trajectory.
#
# Usage:
#   bash scripts/run_experiments.sh          # run all
#   bash scripts/run_experiments.sh table1   # Table 1 only
#   bash scripts/run_experiments.sh table2   # Table 2 only
#
# Results: check outputs/*/summary.json for scores.

set -euo pipefail

# LoCoMo categories (conversation indices)
LOCOMO_CATS=(0 1 2 3 4 5 6 7 8 9)

# ALFWorld categories (task types) — skip pick_and_place_with_movable_recep (only 3 train / 1 val)
ALFWORLD_CATS=(
    look_at_obj_in_light
    pick_and_place_simple
    pick_clean_then_place_in_recep
    pick_cool_then_place_in_recep
    pick_heat_then_place_in_recep
    pick_two_obj_and_place
)

MODELS="--task-model chatgpt/gpt-5.4 --reflect-model chatgpt/gpt-5.4 --toolkit-model chatgpt/gpt-5.4"
COMMON_LOCOMO="--dataset locomo --test-size 50 --test-train-ratio -1 --no-weave $MODELS"
COMMON_ALFWORLD="--dataset alfworld --test-size 10 --test-train-ratio 5 --no-weave $MODELS"
EVOLUTION_LOCOMO="--eval-strategy split --eval-rotate-size 5 --eval-static-size 25 --eval-train-ratio -1"
EVOLUTION_ALFWORLD="--eval-strategy split --eval-rotate-size 5 --eval-static-size 15 --eval-train-ratio 3"

run() {
    local label="$1"
    shift
    echo ""
    echo "================================================================"
    echo "  $label"
    echo "================================================================"
    echo "  Command: uv run python -m programmaticmemory.evolution $*"
    echo ""
    uv run python -m programmaticmemory.evolution "$@"
}

run_table1() {
    echo "=============================================================="
    echo "  TABLE 1 — MAIN RESULTS"
    echo "=============================================================="

    # --- LoCoMo (per-category) ---
    for cat in "${LOCOMO_CATS[@]}"; do
        run "T1: LoCoMo cat=$cat / No Memory" \
            $COMMON_LOCOMO --category "$cat" \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy full

        run "T1: LoCoMo cat=$cat / Vanilla RAG" \
            $COMMON_LOCOMO --category "$cat" \
            --seed-program seeds/llm_summarizer.py \
            --iterations 0 --eval-strategy full

        run "T1: LoCoMo cat=$cat / Ours (evolution)" \
            $COMMON_LOCOMO --category "$cat" \
            $EVOLUTION_LOCOMO \
            --iterations 20
    done

    # --- ALFWorld (per-category) ---
    for cat in "${ALFWORLD_CATS[@]}"; do
        run "T1: ALFWorld cat=$cat / No Memory" \
            $COMMON_ALFWORLD --category "$cat" \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy full

        run "T1: ALFWorld cat=$cat / Vanilla RAG" \
            $COMMON_ALFWORLD --category "$cat" \
            --seed-program seeds/llm_summarizer.py \
            --iterations 0 --eval-strategy full

        run "T1: ALFWorld cat=$cat / Ours (evolution)" \
            $COMMON_ALFWORLD --category "$cat" \
            $EVOLUTION_ALFWORLD \
            --iterations 20
    done
}

run_table2() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY"
    echo "  Note: 'Full system' = reuse Table 1 'Ours' results"
    echo "=============================================================="

    # --- LoCoMo ablations (per-category) ---
    for cat in "${LOCOMO_CATS[@]}"; do
        run "T2: LoCoMo cat=$cat / - Instruction constants" \
            $COMMON_LOCOMO --category "$cat" $EVOLUTION_LOCOMO \
            --iterations 20 --freeze-instructions

        run "T2: LoCoMo cat=$cat / - Population (linear only)" \
            $COMMON_LOCOMO --category "$cat" $EVOLUTION_LOCOMO \
            --iterations 20 --selection-strategy max

        run "T2: LoCoMo cat=$cat / - Compile-fix loop" \
            $COMMON_LOCOMO --category "$cat" $EVOLUTION_LOCOMO \
            --iterations 20 --max-fix-attempts 0

        run "T2: LoCoMo cat=$cat / - Multi-seed (single seed)" \
            $COMMON_LOCOMO --category "$cat" $EVOLUTION_LOCOMO \
            --iterations 20 --seed-program seeds/single/
    done

    # --- ALFWorld ablations (per-category) ---
    for cat in "${ALFWORLD_CATS[@]}"; do
        run "T2: ALFWorld cat=$cat / - Instruction constants" \
            $COMMON_ALFWORLD --category "$cat" $EVOLUTION_ALFWORLD \
            --iterations 20 --freeze-instructions

        run "T2: ALFWorld cat=$cat / - Population (linear only)" \
            $COMMON_ALFWORLD --category "$cat" $EVOLUTION_ALFWORLD \
            --iterations 20 --selection-strategy max

        run "T2: ALFWorld cat=$cat / - Compile-fix loop" \
            $COMMON_ALFWORLD --category "$cat" $EVOLUTION_ALFWORLD \
            --iterations 20 --max-fix-attempts 0

        run "T2: ALFWorld cat=$cat / - Multi-seed (single seed)" \
            $COMMON_ALFWORLD --category "$cat" $EVOLUTION_ALFWORLD \
            --iterations 20 --seed-program seeds/single/
    done
}

# Dispatch based on argument
case "${1:-all}" in
    table1) run_table1 ;;
    table2) run_table2 ;;
    all)    run_table1; run_table2 ;;
    *)      echo "Usage: $0 [table1|table2|all]"; exit 1 ;;
esac

echo ""
echo "=============================================================="
echo "  ALL DONE. Check outputs/*/ for results."
echo "  Per-category scores: jq '.test_evaluation' outputs/*/summary.json"
echo "=============================================================="
