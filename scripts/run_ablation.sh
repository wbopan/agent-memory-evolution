#!/usr/bin/env bash
# Run Table 2 (ablation study): each variant removes one design choice from the full system.
# All 4 variants × LoCoMo only.
# Each run has a unique --output-dir, so re-running the script auto-resumes interrupted runs.
#
# Usage:
#   bash scripts/run_ablation.sh              # all 4 variants
#   bash scripts/run_ablation.sh freeze-inst  # one specific variant
#
# Variants:
#   freeze-inst   — freeze instruction constants (only code evolves)
#   freeze-code   — freeze code structure (only instructions evolve)
#   linear        — linear evolution (--selection-strategy max, no population diversity)
#   no-diversity  — linear + single seed (no population diversity at all)
#
# Full system scores come from Table 1 (run_experiments.sh) — not re-run here.

set -euo pipefail

MODELS="--task-model openrouter/deepseek/deepseek-v3.2 --reflect-model openrouter/openai/gpt-5.3-codex --toolkit-model openrouter/deepseek/deepseek-v3.2"
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
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

run_freeze_inst()  { run "T2: LoCoMo / - Instruction constants" $COMMON_LOCOMO $EVOLUTION --freeze-instructions --output-dir outputs/t2-locomo-freeze-inst; }
run_freeze_code()  { run "T2: LoCoMo / - Code structure"        $COMMON_LOCOMO $EVOLUTION --freeze-code          --output-dir outputs/t2-locomo-freeze-code; }
run_linear()       { run "T2: LoCoMo / - Population (linear)"   $COMMON_LOCOMO $EVOLUTION --selection-strategy max --output-dir outputs/t2-locomo-linear; }
run_no_diversity() { run "T2: LoCoMo / - Population diversity"  $COMMON_LOCOMO $EVOLUTION --selection-strategy max --seed-program seeds/single/empty.py --output-dir outputs/t2-locomo-no-diversity; }

run_all() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY (4 variants × LoCoMo)"
    echo "=============================================================="
    run_freeze_inst
    run_freeze_code
    run_linear
    run_no_diversity
}

case "${1:-all}" in
    freeze-inst)  run_freeze_inst ;;
    freeze-code)  run_freeze_code ;;
    linear)       run_linear ;;
    no-diversity) run_no_diversity ;;
    all)          run_all ;;
    *)
        echo "Usage: $0 [freeze-inst|freeze-code|linear|no-diversity|all]"
        exit 1
        ;;
esac

echo ""
echo "=============================================================="
echo "  DONE. Check outputs/t2-*/summary.json for results."
echo "=============================================================="
