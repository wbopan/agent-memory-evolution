#!/usr/bin/env bash
# Run all experiments for Table 1 (main results) and Table 2 (ablation study).
# Each run writes to outputs/<timestamp>/ with summary.json containing per-category scores.
#
# Usage:
#   bash scripts/run_experiments.sh          # run all
#   bash scripts/run_experiments.sh table1   # Table 1 only
#   bash scripts/run_experiments.sh table2   # Table 2 only
#
# Results: check outputs/*/summary.json for scores, including:
#   - test_evaluation.scores (overall)
#   - test_evaluation.category_scores (per-category breakdown)
#   - test_evaluation.extra_metrics (EM for LoCoMo)

set -euo pipefail

COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio -1 --no-weave"
COMMON_ALFWORLD="--dataset alfworld --test-size 50 --test-train-ratio -1 --no-weave"
EVOLUTION_LOCOMO="--eval-strategy representative --eval-val-size 30 --eval-train-ratio -1"
EVOLUTION_ALFWORLD="--eval-strategy representative --eval-val-size 20 --eval-train-ratio 5"

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
    echo "  TABLE 1 — MAIN RESULTS (6 runs)"
    echo "=============================================================="

    # --- LoCoMo ---
    run "T1: LoCoMo / No Memory" \
        $COMMON_LOCOMO \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy full

    run "T1: LoCoMo / Vanilla RAG" \
        $COMMON_LOCOMO \
        --seed-program seeds/llm_summarizer.py \
        --iterations 0 --eval-strategy full

    run "T1: LoCoMo / Ours (evolution)" \
        $COMMON_LOCOMO \
        $EVOLUTION_LOCOMO \
        --iterations 20

    # --- ALFWorld ---
    run "T1: ALFWorld / No Memory" \
        $COMMON_ALFWORLD \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy full

    run "T1: ALFWorld / Vanilla RAG" \
        $COMMON_ALFWORLD \
        --seed-program seeds/llm_summarizer.py \
        --iterations 0 --eval-strategy full

    run "T1: ALFWorld / Ours (evolution)" \
        $COMMON_ALFWORLD \
        $EVOLUTION_ALFWORLD \
        --iterations 20
}

run_table2() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY (8 runs)"
    echo "  Note: 'Full system' = reuse Table 1 'Ours' results"
    echo "=============================================================="

    # --- LoCoMo ablations ---
    run "T2: LoCoMo / - Instruction constants" \
        $COMMON_LOCOMO $EVOLUTION_LOCOMO \
        --iterations 20 --freeze-instructions

    run "T2: LoCoMo / - Population (linear only)" \
        $COMMON_LOCOMO $EVOLUTION_LOCOMO \
        --iterations 20 --selection-strategy max

    run "T2: LoCoMo / - Compile-fix loop" \
        $COMMON_LOCOMO $EVOLUTION_LOCOMO \
        --iterations 20 --max-fix-attempts 0

    run "T2: LoCoMo / - Multi-seed (single seed)" \
        $COMMON_LOCOMO $EVOLUTION_LOCOMO \
        --iterations 20 --seed-program seeds/single/

    # --- ALFWorld ablations ---
    run "T2: ALFWorld / - Instruction constants" \
        $COMMON_ALFWORLD $EVOLUTION_ALFWORLD \
        --iterations 20 --freeze-instructions

    run "T2: ALFWorld / - Population (linear only)" \
        $COMMON_ALFWORLD $EVOLUTION_ALFWORLD \
        --iterations 20 --selection-strategy max

    run "T2: ALFWorld / - Compile-fix loop" \
        $COMMON_ALFWORLD $EVOLUTION_ALFWORLD \
        --iterations 20 --max-fix-attempts 0

    run "T2: ALFWorld / - Multi-seed (single seed)" \
        $COMMON_ALFWORLD $EVOLUTION_ALFWORLD \
        --iterations 20 --seed-program seeds/single/
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
echo "  Per-category scores: jq '.test_evaluation.category_scores' outputs/*/summary.json"
echo "=============================================================="
