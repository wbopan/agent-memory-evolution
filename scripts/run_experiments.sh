#!/usr/bin/env bash
# Run Table 1 (main results): No Memory, Vanilla RAG, Ours × LoCoMo + ALFWorld.
# ALFWorld runs on both eval splits (seen + unseen).
# Each run writes to outputs/<timestamp>/ with summary.json containing per-category scores.
#
# Usage:
#   bash scripts/run_experiments.sh
#
# Results: check outputs/*/summary.json for scores.

set -euo pipefail

MODELS="--task-model openrouter/deepseek/deepseek-v3.2 --reflect-model openrouter/openai/gpt-5.3-codex --toolkit-model openrouter/deepseek/deepseek-v3.2"
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_ALFWORLD="--dataset alfworld --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
EVOLUTION_LOCOMO="--eval-strategy split --eval-rotate-size 5 --eval-static-size 50 --eval-train-ratio 2"
EVOLUTION_ALFWORLD="--eval-strategy split --eval-rotate-size 5 --eval-static-size 50 --eval-train-ratio 2"

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

    # --- LoCoMo ---
    run "T1: LoCoMo / No Memory" \
        $COMMON_LOCOMO \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none

    run "T1: LoCoMo / Vanilla RAG" \
        $COMMON_LOCOMO \
        --seed-program seeds/llm_summarizer.py \
        --iterations 0 --eval-strategy none

    run "T1: LoCoMo / Ours (evolution)" \
        $COMMON_LOCOMO \
        $EVOLUTION_LOCOMO \
        --iterations 20

    # --- ALFWorld (both splits) ---
    for SPLIT in unseen seen; do
        run "T1: ALFWorld $SPLIT / No Memory" \
            $COMMON_ALFWORLD \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy none \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Vanilla RAG" \
            $COMMON_ALFWORLD \
            --seed-program seeds/llm_summarizer.py \
            --iterations 0 --eval-strategy none \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Ours (evolution)" \
            $COMMON_ALFWORLD \
            $EVOLUTION_ALFWORLD \
            --iterations 20 \
            eval_split=$SPLIT
    done
}

# Dispatch based on argument
case "${1:-all}" in
    table1) run_table1 ;;
    all)    run_table1 ;;
    *)      echo "Usage: $0 [table1|all]"; exit 1 ;;
esac

echo ""
echo "=============================================================="
echo "  ALL DONE. Check outputs/*/ for results."
echo "  Per-category scores: jq '.test_evaluation' outputs/*/summary.json"
echo "=============================================================="
