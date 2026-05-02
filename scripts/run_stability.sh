#!/usr/bin/env bash
# Stability experiments: 3 benchmarks × 3 evolution seeds (42, 1, 2).
# Seed 42 results are reused from t1-*-ours runs; this script runs seeds 1 and 2.
#
# Usage:
#   bash scripts/run_stability.sh          # all 6 runs
#   bash scripts/run_stability.sh locomo   # LoCoMo only (2 runs)
#   bash scripts/run_stability.sh hb-data  # HealthBench Data only
#   bash scripts/run_stability.sh pr-fin   # PRBench Finance only

set -euo pipefail

# Model IDs — same as run_experiments.sh
TASK_MODEL="${TASK_MODEL:-azure/gpt-5.4-mini}"
REFLECT_MODEL="${REFLECT_MODEL:-azure/gpt-5.3-codex}"
TOOLKIT_MODEL="${TOOLKIT_MODEL:-azure/gpt-5.4-mini}"
EMBED_MODEL="${EMBEDDING_MODEL:-openrouter/baai/bge-m3}"
BATCH_CONCURRENCY="${BATCH_CONCURRENCY:-64}"
THINKING_EFFORT="${THINKING_EFFORT:-medium}"
MODELS="--task-model $TASK_MODEL --reflect-model $REFLECT_MODEL --toolkit-model $TOOLKIT_MODEL --embedding-model $EMBED_MODEL --batch-concurrency $BATCH_CONCURRENCY --task-lm-thinking-effort $THINKING_EFFORT"
EVOL_BASE="--eval-train-ratio 2 --iterations 20 --eval-rotate-size 5 --no-references"

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

run_locomo() {
    for ESEED in 1 2; do
        run "Stability: LoCoMo / eseed=$ESEED" \
            --dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS \
            $EVOL_BASE --eval-static-size 60 \
            --evolution-seed $ESEED \
            --output-dir outputs/stability-locomo-eseed${ESEED}
    done
}

run_hb_data() {
    for ESEED in 1 2; do
        run "Stability: HB Data Tasks / eseed=$ESEED" \
            --dataset healthbench --category health_data_tasks --test-size 100 --test-train-ratio 3 --no-weave $MODELS \
            $EVOL_BASE --eval-static-size 60 \
            --evolution-seed $ESEED \
            --output-dir outputs/stability-hb-data-eseed${ESEED}
    done
}

run_pr_fin() {
    for ESEED in 1 2; do
        run "Stability: PR Finance / eseed=$ESEED" \
            --dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS \
            $EVOL_BASE --eval-static-size 50 \
            --evolution-seed $ESEED \
            --output-dir outputs/stability-pr-finance-eseed${ESEED}
    done
}

case "${1:-all}" in
    locomo)  run_locomo ;;
    hb-data) run_hb_data ;;
    pr-fin)  run_pr_fin ;;
    all)     run_locomo; run_hb_data; run_pr_fin ;;
    *)       echo "Usage: $0 [locomo|hb-data|pr-fin|all]"; exit 1 ;;
esac

echo ""
echo "================================================================"
echo "  STABILITY RUNS DONE."
echo "  Seed 42 results: reuse from outputs/t1-{locomo,hb-data-tasks,pr-finance}-ours/"
echo "  New results: outputs/stability-{locomo,hb-data,pr-finance}-eseed{1,2}/"
echo "================================================================"
