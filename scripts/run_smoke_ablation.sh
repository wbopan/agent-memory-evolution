#!/usr/bin/env bash
# Smoke test for run_ablation.sh — covers all 4 ablation variants × LoCoMo.
#
# Usage:
#   bash scripts/run_smoke_ablation.sh              # all 4 variants
#   bash scripts/run_smoke_ablation.sh freeze-inst   # one specific variant

set -euo pipefail

MODELS="--task-model openrouter/deepseek/deepseek-v3.2 --reflect-model openrouter/openai/gpt-5.3-codex --toolkit-model openrouter/deepseek/deepseek-v3.2"
SMOKE="--test-size 3 --test-train-ratio 1 --batch-concurrency 2 --no-weave $MODELS"
EVOLUTION="--eval-strategy split --eval-rotate-size 1 --eval-static-size 2 --eval-train-ratio 1 --iterations 1 --max-fix-attempts 1"

# Ablation runs only on LoCoMo.
COMMON_LOCOMO="--dataset locomo $SMOKE"

run() {
    local label="$1"
    shift
    echo ""
    echo "================================================================"
    echo "  SMOKE: $label"
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
    echo "  SMOKE TABLE 2 — ABLATION (4 variants × LoCoMo)"
    echo "=============================================================="
    run_freeze_inst
    run_freeze_code
    run_linear
    run_no_diversity
}

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
echo "  SMOKE DONE. All ablation paths validated."
echo "  Scores: jq '.test_evaluation' outputs/*/summary.json"
echo "=============================================================="
