#!/usr/bin/env bash
# Run Table 1 (main results + baselines) across all 7 dataset settings.
# Each run has a unique --output-dir, so re-running the script auto-resumes interrupted runs.
#
# Usage:
#   bash scripts/run_experiments.sh              # all (table1 + baselines)
#   bash scripts/run_experiments.sh table1       # main results only
#   bash scripts/run_experiments.sh baselines    # ALMA baselines only
#
# Override models via environment variables:
#   TASK_MODEL=deepseek/deepseek-v3.2 REFLECT_MODEL=openai/gpt-5.3-codex TOOLKIT_MODEL=deepseek/deepseek-v3.2 bash scripts/run_experiments.sh
#
# Results: jq '.test_evaluation' outputs/t1-*/summary.json outputs/bl-*/summary.json

set -euo pipefail

# Model IDs — override via env vars. Default uses OpenRouter provider prefix.
TASK_MODEL="${TASK_MODEL:-openrouter/deepseek/deepseek-v3.2}"
REFLECT_MODEL="${REFLECT_MODEL:-openrouter/openai/gpt-5.3-codex}"
TOOLKIT_MODEL="${TOOLKIT_MODEL:-openrouter/deepseek/deepseek-v3.2}"
EMBED_MODEL="${EMBEDDING_MODEL:-openrouter/baai/bge-m3}"
MODELS="--task-model $TASK_MODEL --reflect-model $REFLECT_MODEL --toolkit-model $TOOLKIT_MODEL --embedding-model $EMBED_MODEL"
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_ALFWORLD="--dataset alfworld --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_HB_DATA="--dataset healthbench --category health_data_tasks --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_HB_EMERG="--dataset healthbench --category emergency_referrals --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_PR_LEGAL="--dataset prbench --category legal --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_PR_FIN="--dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
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

run_table1() {
    echo "=============================================================="
    echo "  TABLE 1 — MAIN RESULTS"
    echo "=============================================================="

    # --- LoCoMo ---
    run "T1: LoCoMo / No Memory" \
        $COMMON_LOCOMO \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-locomo-no-memory

    run "T1: LoCoMo / Vanilla RAG" \
        $COMMON_LOCOMO \
        --seed-program seeds/vector_search.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-locomo-vanilla-rag

    run "T1: LoCoMo / Ours (evolution)" \
        $COMMON_LOCOMO \
        $EVOLUTION \
        --output-dir outputs/t1-locomo-ours

    # --- ALFWorld (both splits) ---
    for SPLIT in unseen seen; do
        run "T1: ALFWorld $SPLIT / No Memory" \
            $COMMON_ALFWORLD \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy none \
            --output-dir outputs/t1-alfworld-${SPLIT}-no-memory \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Vanilla RAG" \
            $COMMON_ALFWORLD \
            --seed-program seeds/vector_search.py \
            --iterations 0 --eval-strategy none \
            --output-dir outputs/t1-alfworld-${SPLIT}-vanilla-rag \
            eval_split=$SPLIT

        run "T1: ALFWorld $SPLIT / Ours (evolution)" \
            $COMMON_ALFWORLD \
            $EVOLUTION \
            --output-dir outputs/t1-alfworld-${SPLIT}-ours \
            eval_split=$SPLIT
    done

    # --- HealthBench (2 categories) + PRBench (2 categories) ---
    for COMMON_LABEL in \
        "$COMMON_HB_DATA:hb-data-tasks" \
        "$COMMON_HB_EMERG:hb-emergency" \
        "$COMMON_PR_LEGAL:pr-legal" \
        "$COMMON_PR_FIN:pr-finance"; do
        COMMON_DS="${COMMON_LABEL%%:*}"
        DS_SLUG="${COMMON_LABEL##*:}"

        run "T1: $DS_SLUG / No Memory" \
            $COMMON_DS \
            --seed-program src/programmaticmemory/baselines/no_memory.py \
            --iterations 0 --eval-strategy none \
            --output-dir outputs/t1-${DS_SLUG}-no-memory

        run "T1: $DS_SLUG / Vanilla RAG" \
            $COMMON_DS \
            --seed-program seeds/vector_search.py \
            --iterations 0 --eval-strategy none \
            --output-dir outputs/t1-${DS_SLUG}-vanilla-rag

        run "T1: $DS_SLUG / Ours (evolution)" \
            $COMMON_DS \
            $EVOLUTION \
            --output-dir outputs/t1-${DS_SLUG}-ours
    done
}

# ALMA baselines: 5 baselines × 7 benchmark settings = 35 runs.
BASELINES=(
    "trajectory_retrieval:traj-retr:"
    "reasoning_bank:reason-bank:"
    "dynamic_cheatsheet:dyn-cheat:"
    "g_memory:g-memory:"
    "mem0:mem0:--toolkit-budget 10"
)

run_baselines() {
    echo "=============================================================="
    echo "  TABLE 1 — ALMA BASELINES"
    echo "=============================================================="

    for entry in "${BASELINES[@]}"; do
        IFS=: read -r file slug extra <<< "$entry"

        # --- LoCoMo ---
        run "BL: LoCoMo / $slug" \
            $COMMON_LOCOMO \
            --seed-program src/programmaticmemory/baselines/${file}.py \
            --iterations 0 --eval-strategy none \
            --output-dir outputs/bl-locomo-${slug} $extra

        # --- ALFWorld (both splits) ---
        for SPLIT in unseen seen; do
            run "BL: ALFWorld $SPLIT / $slug" \
                $COMMON_ALFWORLD \
                --seed-program src/programmaticmemory/baselines/${file}.py \
                --iterations 0 --eval-strategy none \
                --output-dir outputs/bl-alfworld-${SPLIT}-${slug} \
                eval_split=$SPLIT $extra
        done

        # --- HealthBench + PRBench (4 categories) ---
        for COMMON_LABEL in \
            "$COMMON_HB_DATA:hb-data-tasks" \
            "$COMMON_HB_EMERG:hb-emergency" \
            "$COMMON_PR_LEGAL:pr-legal" \
            "$COMMON_PR_FIN:pr-finance"; do
            COMMON_DS="${COMMON_LABEL%%:*}"
            DS_SLUG="${COMMON_LABEL##*:}"

            run "BL: $DS_SLUG / $slug" \
                $COMMON_DS \
                --seed-program src/programmaticmemory/baselines/${file}.py \
                --iterations 0 --eval-strategy none \
                --output-dir outputs/bl-${DS_SLUG}-${slug} $extra
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
echo "  ALL DONE. Check outputs/t1-*/summary.json and outputs/bl-*/summary.json"
echo "=============================================================="
