#!/usr/bin/env bash
# Smoke test for run_experiments.sh — covers all paths with minimal data.
# Validates the full pipeline before handing off to collaborators.
#
# Usage:
#   bash scripts/run_smoke_experiments.sh              # all (table1 + baselines)
#   bash scripts/run_smoke_experiments.sh table1       # main results only
#   bash scripts/run_smoke_experiments.sh baselines    # ALMA baselines only

set -euo pipefail

MODELS="--task-model openrouter/deepseek/deepseek-v3.2 --reflect-model openrouter/openai/gpt-5.3-codex --toolkit-model openrouter/deepseek/deepseek-v3.2"
SMOKE="--test-size 3 --test-train-ratio 1 --batch-concurrency 2 --no-weave $MODELS"
COMMON_LOCOMO="--dataset locomo $SMOKE"
COMMON_ALFWORLD="--dataset alfworld $SMOKE"
COMMON_HB_DATA="--dataset healthbench --category health_data_tasks $SMOKE"
COMMON_HB_EMERG="--dataset healthbench --category emergency_referrals $SMOKE"
COMMON_PR_LEGAL="--dataset prbench --category legal $SMOKE"
COMMON_PR_FIN="--dataset prbench --category finance $SMOKE"
EVOLUTION="--eval-strategy split --eval-rotate-size 1 --eval-static-size 2 --eval-train-ratio 1 --iterations 1 --max-fix-attempts 1"

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

run_table1() {
    echo "=============================================================="
    echo "  SMOKE TABLE 1 — MAIN RESULTS"
    echo "=============================================================="

    # --- LoCoMo ---
    run "T1: LoCoMo / No Memory" \
        $COMMON_LOCOMO \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none

    run "T1: LoCoMo / Vanilla RAG" \
        $COMMON_LOCOMO \
        --seed-program seeds/vector_search.py \
        --iterations 0 --eval-strategy none

    run "T1: LoCoMo / Ours (evolution)" \
        $COMMON_LOCOMO \
        $EVOLUTION

    # --- ALFWorld (both splits) ---
    for SPLIT in unseen seen; do
        run "T1: ALFWorld $SPLIT / No Memory" \
            $COMMON_ALFWORLD \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy none \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Vanilla RAG" \
            $COMMON_ALFWORLD \
            --seed-program seeds/vector_search.py \
            --iterations 0 --eval-strategy none \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Ours (evolution)" \
            $COMMON_ALFWORLD \
            $EVOLUTION \
            eval_split=$SPLIT
    done

    # --- HealthBench (2 categories) + PRBench (2 categories) ---
    for COMMON_LABEL in \
        "$COMMON_HB_DATA:HB-data_tasks" \
        "$COMMON_HB_EMERG:HB-emergency" \
        "$COMMON_PR_LEGAL:PR-legal" \
        "$COMMON_PR_FIN:PR-finance"; do
        COMMON_DS="${COMMON_LABEL%%:*}"
        DS_LABEL="${COMMON_LABEL##*:}"

        run "T1: $DS_LABEL / No Memory" \
            $COMMON_DS \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy none

        run "T1: $DS_LABEL / Vanilla RAG" \
            $COMMON_DS \
            --seed-program seeds/vector_search.py \
            --iterations 0 --eval-strategy none

        run "T1: $DS_LABEL / Ours (evolution)" \
            $COMMON_DS \
            $EVOLUTION
    done
}

# ALMA baselines: 5 baselines × 7 benchmark settings = 35 runs
BASELINES=(
    "trajectory_retrieval:Trajectory Retrieval:"
    "reasoning_bank:ReasoningBank:"
    "dynamic_cheatsheet:Dynamic Cheatsheet:"
    "g_memory:G-Memory:"
    "mem0:Mem0:--toolkit-budget 10"
)

run_baselines() {
    echo "=============================================================="
    echo "  SMOKE TABLE 1 — ALMA BASELINES"
    echo "=============================================================="

    for entry in "${BASELINES[@]}"; do
        IFS=: read -r file label extra <<< "$entry"

        # --- LoCoMo ---
        run "BL: LoCoMo / $label" \
            $COMMON_LOCOMO \
            --seed-program src/programmaticmemory/baselines/${file}.py \
            --iterations 0 --eval-strategy none $extra

        # --- ALFWorld (both splits) ---
        for SPLIT in unseen seen; do
            run "BL: ALFWorld $SPLIT / $label" \
                $COMMON_ALFWORLD \
                --seed-program src/programmaticmemory/baselines/${file}.py \
                --iterations 0 --eval-strategy none \
                eval_split=$SPLIT $extra
        done

        # --- HealthBench + PRBench (4 categories) ---
        for COMMON_LABEL in \
            "$COMMON_HB_DATA:HB-data_tasks" \
            "$COMMON_HB_EMERG:HB-emergency" \
            "$COMMON_PR_LEGAL:PR-legal" \
            "$COMMON_PR_FIN:PR-finance"; do
            COMMON_DS="${COMMON_LABEL%%:*}"
            DS_LABEL="${COMMON_LABEL##*:}"

            run "BL: $DS_LABEL / $label" \
                $COMMON_DS \
                --seed-program src/programmaticmemory/baselines/${file}.py \
                --iterations 0 --eval-strategy none $extra
        done
    done
}

# Dispatch
case "${1:-all}" in
    table1)     run_table1 ;;
    baselines)  run_baselines ;;
    all)        run_table1; run_baselines ;;
    *)          echo "Usage: $0 [table1|baselines|all]"; exit 1 ;;
esac

echo ""
echo "=============================================================="
echo "  SMOKE DONE. All paths validated."
echo "  Check outputs/*/ for results."
echo "=============================================================="
