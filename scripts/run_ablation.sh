#!/usr/bin/env bash
# Run Table 2 (ablation study): each variant removes one design choice from the full system.
# All 4 variants × LoCoMo only.
#
# Usage:
#   bash scripts/run_ablation.sh              # run all 4 ablation experiments × all datasets
#   bash scripts/run_ablation.sh freeze-inst  # run one specific variant × all datasets
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
EVOLUTION="--eval-strategy split --eval-rotate-size 5 --eval-static-size 50 --eval-train-ratio 2 --iterations 20"

# Ablation runs only on LoCoMo.
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"

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

run_variant() {
    local variant_label="$1"
    local variant_flags="$2"
    run "T2: LoCoMo / $variant_label" \
        $COMMON_LOCOMO $EVOLUTION $variant_flags
}

run_freeze_inst()  { run_variant "- Instruction constants" "--freeze-instructions"; }
run_freeze_code()  { run_variant "- Code structure" "--freeze-code"; }
run_linear()       { run_variant "- Population (linear only)" "--selection-strategy max"; }
run_no_diversity() { run_variant "- Population diversity" "--selection-strategy max --seed-program seeds/single/empty.py"; }

run_all() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY (4 variants × LoCoMo)"
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
