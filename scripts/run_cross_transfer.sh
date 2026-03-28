#!/usr/bin/env bash
# Cross-transfer experiment: evaluate each task's best evolved program on other tasks.
# Produces 6 new runs for the 3×4 cross-transfer table (off-diagonal, non-seed cells).
#
# Usage:
#   bash scripts/run_cross_transfer.sh
#
# Prerequisites: t1-locomo-ours, t1-alfworld-seen-ours, t1-pr-legal-ours must be completed.
# Results: jq '.test_evaluation' outputs/ct-*/summary.json

set -euo pipefail

# --- Models (same as run_experiments.sh) ---
TASK_MODEL="${TASK_MODEL:-azure/gpt-5.4-mini}"
REFLECT_MODEL="${REFLECT_MODEL:-azure/gpt-5.3-codex}"
TOOLKIT_MODEL="${TOOLKIT_MODEL:-azure/gpt-5.4-mini}"
EMBED_MODEL="${EMBEDDING_MODEL:-openrouter/baai/bge-m3}"
BATCH_CONCURRENCY="${BATCH_CONCURRENCY:-64}"
THINKING_EFFORT="${THINKING_EFFORT:-medium}"
MODELS="--task-model $TASK_MODEL --reflect-model $REFLECT_MODEL --toolkit-model $TOOLKIT_MODEL --embedding-model $EMBED_MODEL --batch-concurrency $BATCH_CONCURRENCY --task-lm-thinking-effort $THINKING_EFFORT"

# --- Dataset configs (same as run_experiments.sh) ---
COMMON_LOCOMO="--dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS"
COMMON_ALFWORLD="--dataset alfworld --test-size 50 --test-train-ratio 3 --no-weave $MODELS"
COMMON_PR_LEGAL="--dataset prbench --category legal --test-size 50 --test-train-ratio 3 --no-weave $MODELS"

# --- Best evolved programs ---
PROG_LOCOMO="outputs/t1-locomo-ours/programs/iter_2.py"
PROG_ALFWORLD="outputs/t1-alfworld-seen-ours/programs/iter_2.py"
PROG_PRLEGAL="outputs/t1-pr-legal-ours/programs/iter_4.py"

# Verify programs exist
for prog in "$PROG_LOCOMO" "$PROG_ALFWORLD" "$PROG_PRLEGAL"; do
    if [ ! -f "$prog" ]; then
        echo "ERROR: Best program not found: $prog"
        echo "Run the corresponding t1-*-ours experiment first."
        exit 1
    fi
done

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

echo "=============================================================="
echo "  CROSS-TRANSFER EXPERIMENT (6 runs)"
echo "=============================================================="

# --- Row 1: Eval on LoCoMo, using other tasks' programs ---
run "CT: Eval LoCoMo / ALFWorld program" \
    $COMMON_LOCOMO \
    --seed-program "$PROG_ALFWORLD" \
    --iterations 0 \
    --output-dir outputs/ct-locomo-alfworld-prog

run "CT: Eval LoCoMo / PRBench program" \
    $COMMON_LOCOMO \
    --seed-program "$PROG_PRLEGAL" \
    --iterations 0 \
    --output-dir outputs/ct-locomo-prlegal-prog

# --- Row 2: Eval on ALFWorld (seen), using other tasks' programs ---
run "CT: Eval ALFWorld / LoCoMo program" \
    $COMMON_ALFWORLD \
    --seed-program "$PROG_LOCOMO" \
    --iterations 0 \
    --output-dir outputs/ct-alfworld-locomo-prog \
    eval_split=seen

run "CT: Eval ALFWorld / PRBench program" \
    $COMMON_ALFWORLD \
    --seed-program "$PROG_PRLEGAL" \
    --iterations 0 \
    --output-dir outputs/ct-alfworld-prlegal-prog \
    eval_split=seen

# --- Row 3: Eval on PRBench (legal), using other tasks' programs ---
run "CT: Eval PRBench / LoCoMo program" \
    $COMMON_PR_LEGAL \
    --seed-program "$PROG_LOCOMO" \
    --iterations 0 \
    --output-dir outputs/ct-prlegal-locomo-prog

run "CT: Eval PRBench / ALFWorld program" \
    $COMMON_PR_LEGAL \
    --seed-program "$PROG_ALFWORLD" \
    --iterations 0 \
    --output-dir outputs/ct-prlegal-alfworld-prog

echo ""
echo "=============================================================="
echo "  CROSS-TRANSFER DONE. Results:"
echo "  jq '.test_evaluation' outputs/ct-*/summary.json"
echo "=============================================================="
