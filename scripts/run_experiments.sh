#!/usr/bin/env bash
# Run Table 1 (main results + baselines) and Table 2 (ablation) across all dataset settings.
# Each run has a unique --output-dir, so re-running the script auto-resumes interrupted runs.
#
# Usage:
#   bash scripts/run_experiments.sh              # all (table1 + baselines + ablation)
#   bash scripts/run_experiments.sh table1       # main results only
#   bash scripts/run_experiments.sh baselines    # ALMA baselines only
#   bash scripts/run_experiments.sh ablation     # ablation study only (Table 2)
#
# Override models via environment variables:
#   TASK_MODEL=deepseek/deepseek-v3.2 REFLECT_MODEL=openai/gpt-5.3-codex TOOLKIT_MODEL=deepseek/deepseek-v3.2 bash scripts/run_experiments.sh
#
# Results: jq '.test_evaluation' outputs/t1-*/summary.json outputs/bl-*/summary.json outputs/t2-*/summary.json

set -euo pipefail

# Model IDs — override via env vars. Default uses Azure provider prefix.
TASK_MODEL="${TASK_MODEL:-azure/gpt-5.4-mini}"
REFLECT_MODEL="${REFLECT_MODEL:-azure/gpt-5.3-codex}"
TOOLKIT_MODEL="${TOOLKIT_MODEL:-azure/gpt-5.4-mini}"
EMBED_MODEL="${EMBEDDING_MODEL:-openrouter/baai/bge-m3}"
BATCH_CONCURRENCY="${BATCH_CONCURRENCY:-64}"
THINKING_EFFORT="${THINKING_EFFORT:-medium}"
MODELS="--task-model $TASK_MODEL --reflect-model $REFLECT_MODEL --toolkit-model $TOOLKIT_MODEL --embedding-model $EMBED_MODEL --batch-concurrency $BATCH_CONCURRENCY --task-lm-thinking-effort $THINKING_EFFORT"
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_ALFWORLD="--dataset alfworld --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_HB_DATA="--dataset healthbench --category health_data_tasks --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_HB_EMERG="--dataset healthbench --category emergency_referrals --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_PR_LEGAL="--dataset prbench --category legal --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_PR_FIN="--dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
# Per-dataset evolution configs.  Two tiers:
#   Large (HB/LoCoMo): test=100, static=60, rotate_pool≥60
#   Small (PR/ALFWorld): test=50, static=50, rotate_pool≥50 (best effort)
# eval-rotate-size omitted → default=5 (sufficient for reflection; rubric benchmarks are expensive per item)
EVOL_BASE="--eval-strategy split --eval-train-ratio 2 --iterations 20"
EVOL_LOCOMO="$EVOL_BASE --eval-static-size 60"       # val=1986, test=100, evo=1886, rotate_pool=1826
EVOL_ALF_UNSEEN="$EVOL_BASE --eval-static-size 50"   # val=150,  test=50,  evo=100,  rotate_pool=50
EVOL_ALF_SEEN="$EVOL_BASE --eval-static-size 32"     # val=102,  test=50,  evo=52,   rotate_pool=20
EVOL_HB_DATA="$EVOL_BASE --eval-static-size 60"      # val=220,  test=100, evo=120,  rotate_pool=60
EVOL_HB_EMERG="$EVOL_BASE --eval-static-size 60"     # val=222,  test=100, evo=122,  rotate_pool=62
EVOL_PR_LEGAL="$EVOL_BASE --eval-static-size 50"     # val=150,  test=50,  evo=100,  rotate_pool=50
EVOL_PR_FIN="$EVOL_BASE --eval-static-size 50"       # val=150,  test=50,  evo=100,  rotate_pool=50

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

_run_ds_group() {
    local COMMON_DS="$1" DS_SLUG="$2" EVOL_DS="$3"
    run "T1: $DS_SLUG / No Memory" \
        $COMMON_DS \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-${DS_SLUG}-no-memory
    run "T1: $DS_SLUG / Vanilla RAG" \
        $COMMON_DS \
        --seed-program src/programmaticmemory/seeds/vector_search.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-${DS_SLUG}-vanilla-rag
    run "T1: $DS_SLUG / Ours (evolution)" \
        $COMMON_DS \
        $EVOL_DS \
        --output-dir outputs/t1-${DS_SLUG}-ours
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
        --seed-program src/programmaticmemory/seeds/vector_search.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-locomo-vanilla-rag

    run "T1: LoCoMo / Ours (evolution)" \
        $COMMON_LOCOMO \
        $EVOL_LOCOMO \
        --output-dir outputs/t1-locomo-ours

    # --- ALFWorld unseen ---
    run "T1: ALFWorld unseen / No Memory" \
        $COMMON_ALFWORLD \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-alfworld-unseen-no-memory \
        eval_split=unseen
    run "T1: ALFWorld unseen / Vanilla RAG" \
        $COMMON_ALFWORLD \
        --seed-program src/programmaticmemory/seeds/vector_search.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-alfworld-unseen-vanilla-rag \
        eval_split=unseen
    run "T1: ALFWorld unseen / Ours (evolution)" \
        $COMMON_ALFWORLD \
        $EVOL_ALF_UNSEEN \
        --output-dir outputs/t1-alfworld-unseen-ours \
        eval_split=unseen

    # --- ALFWorld seen ---
    run "T1: ALFWorld seen / No Memory" \
        $COMMON_ALFWORLD \
        --seed-program src/programmaticmemory/baselines/no_memory.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-alfworld-seen-no-memory \
        eval_split=seen
    run "T1: ALFWorld seen / Vanilla RAG" \
        $COMMON_ALFWORLD \
        --seed-program src/programmaticmemory/seeds/vector_search.py \
        --iterations 0 --eval-strategy none \
        --output-dir outputs/t1-alfworld-seen-vanilla-rag \
        eval_split=seen
    run "T1: ALFWorld seen / Ours (evolution)" \
        $COMMON_ALFWORLD \
        $EVOL_ALF_SEEN \
        --output-dir outputs/t1-alfworld-seen-ours \
        eval_split=seen

    # --- HealthBench (2 categories) + PRBench (2 categories) ---
    _run_ds_group "$COMMON_HB_DATA"  "hb-data-tasks" "$EVOL_HB_DATA"
    _run_ds_group "$COMMON_HB_EMERG" "hb-emergency"  "$EVOL_HB_EMERG"
    _run_ds_group "$COMMON_PR_LEGAL" "pr-legal"       "$EVOL_PR_LEGAL"
    _run_ds_group "$COMMON_PR_FIN"   "pr-finance"     "$EVOL_PR_FIN"
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

# Table 2 — Ablation study: 4 variants × LoCoMo only.
# Full system scores come from Table 1 (t1-locomo-ours) — not re-run here.
#
# Variants:
#   freeze-inst   — freeze instruction constants (only code evolves)
#   freeze-code   — freeze code structure (only instructions evolve)
#   linear        — linear evolution (--selection-strategy max, no population diversity)
#   no-diversity  — linear + single seed (no population diversity at all)

run_ablation() {
    echo "=============================================================="
    echo "  TABLE 2 — ABLATION STUDY (4 variants × LoCoMo)"
    echo "=============================================================="

    run "T2: LoCoMo / - Instruction constants" \
        $COMMON_LOCOMO $EVOL_LOCOMO \
        --freeze-instructions \
        --output-dir outputs/t2-locomo-freeze-inst

    run "T2: LoCoMo / - Code structure" \
        $COMMON_LOCOMO $EVOL_LOCOMO \
        --freeze-code \
        --output-dir outputs/t2-locomo-freeze-code

    run "T2: LoCoMo / - Population (linear)" \
        $COMMON_LOCOMO $EVOL_LOCOMO \
        --selection-strategy max \
        --output-dir outputs/t2-locomo-linear

    run "T2: LoCoMo / - Population diversity" \
        $COMMON_LOCOMO $EVOL_LOCOMO \
        --selection-strategy max --seed-program src/programmaticmemory/seeds/llm_summarizer.py \
        --output-dir outputs/t2-locomo-no-diversity
}

# Dispatch
case "${1:-all}" in
    table1)     run_table1 ;;
    baselines)  run_baselines ;;
    ablation)   run_ablation ;;
    all)        run_table1; run_baselines; run_ablation ;;
    *)          echo "Usage: $0 [table1|baselines|ablation|all]"; exit 1 ;;
esac

echo ""
echo "=============================================================="
echo "  ALL DONE. Check outputs/t1-*/summary.json, outputs/bl-*/summary.json, outputs/t2-*/summary.json"
echo "=============================================================="
