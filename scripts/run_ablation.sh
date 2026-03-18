#!/usr/bin/env bash
# Run Table 2 (ablation study): each variant removes one design choice from the full system.
# Benchmark: LoCoMo only.
# Each run writes to outputs/<timestamp>/ with summary.json.
#
# Usage:
#   bash scripts/run_ablation.sh              # run all 4 ablation experiments
#   bash scripts/run_ablation.sh freeze-inst  # run one specific variant
#
# Variants:
#   freeze-inst   — freeze instruction constants (only code evolves)
#   freeze-code   — freeze code structure (only instructions evolve)
#   linear        — linear evolution (--selection-strategy max, no population diversity)
#   no-diversity   — linear + single seed (no population diversity at all)
#
# Full system scores come from Table 1 (run_experiments.sh) — not re-run here.

set -euo pipefail

# --- Shared config (identical to Table 1 "Ours") ---
MODELS="--task-model openrouter/deepseek/deepseek-v3.2 --reflect-model openrouter/openai/gpt-5.3-codex --toolkit-model openrouter/deepseek/deepseek-v3.2"
COMMON="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
EVOLUTION="--eval-strategy split --eval-rotate-size 5 --eval-static-size 50 --eval-train-ratio 2 --iterations 20"

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

# --- Ablation 1: Freeze instruction constants ---
run_freeze_inst() {
    run "T2: LoCoMo / - Instruction constants" \
        $COMMON $EVOLUTION --freeze-instructions
}

# --- Ablation 2: Freeze code structure (only instructions evolve) ---
run_freeze_code() {
    run "T2: LoCoMo / - Code structure" \
        $COMMON $EVOLUTION --freeze-code
}

# --- Ablation 3: Linear only (greedy parent selection) ---
run_linear() {
    run "T2: LoCoMo / - Population (linear only)" \
        $COMMON $EVOLUTION --selection-strategy max
}

# # --- Ablation 4: Single seed (disabled for now) ---
# run_single_seed() {
#     run "T2: LoCoMo / - Multi-seed (single seed)" \
#         $COMMON $EVOLUTION --seed-program seeds/single/
# }

# --- Ablation 4: No diversity (linear + single seed) ---
run_no_diversity() {
    run "T2: LoCoMo / - Population diversity" \
        $COMMON $EVOLUTION --selection-strategy max --seed-program seeds/single/empty.py
}

run_all() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY (4 variants, LoCoMo)"
    echo "=============================================================="
    run_freeze_inst
    run_freeze_code
    run_linear
    run_no_diversity
}

# --- Dispatch ---
case "${1:-all}" in
    freeze-inst) run_freeze_inst ;;
    freeze-code) run_freeze_code ;;
    linear)      run_linear ;;
    # single-seed) run_single_seed ;;
    no-diversity) run_no_diversity ;;
    all)         run_all ;;
    *)
        echo "Usage: $0 [freeze-inst|freeze-code|linear|no-diversity|all]"
        exit 1
        ;;
esac

echo ""
echo "=============================================================="
echo "  DONE. Check outputs/*/ for results."
echo "  Scores: jq '.test_evaluation' outputs/*/summary.json"
echo "=============================================================="
