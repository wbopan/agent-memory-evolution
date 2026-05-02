#!/usr/bin/env bash
# Stability experiments: seeds 3 and 4 for all 3 benchmarks (6 runs)
set -euo pipefail
cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory
export PYTHONPATH="${PWD}/src${PYTHONPATH:+:$PYTHONPATH}"

TASK_MODEL=azure/gpt-5.4-mini
REFLECT_MODEL=azure/gpt-5.3-codex
TOOLKIT_MODEL=azure/gpt-5.4-mini
EMBED_MODEL=openrouter/baai/bge-m3
MODELS="--task-model $TASK_MODEL --reflect-model $REFLECT_MODEL --toolkit-model $TOOLKIT_MODEL --embedding-model $EMBED_MODEL --batch-concurrency 64 --task-lm-thinking-effort medium"
EVOL_BASE="--eval-train-ratio 2 --iterations 20 --eval-rotate-size 5 --no-references"

run() {
    local label="$1"; shift
    echo ""
    echo "================================================================"
    echo "  $label"
    echo "================================================================"
    uv run python -m programmaticmemory.evolution "$@"
}

# LoCoMo eseed=3,4
for ESEED in 3 4; do
    run "Stability: LoCoMo / eseed=$ESEED" \
      --dataset locomo --test-size 100 --test-train-ratio 3 --no-weave $MODELS \
      $EVOL_BASE --eval-static-size 60 --evolution-seed $ESEED \
      --output-dir outputs/stability-locomo-eseed${ESEED}
done

# HB Data eseed=3,4
for ESEED in 3 4; do
    run "Stability: HB Data / eseed=$ESEED" \
      --dataset healthbench --category health_data_tasks --test-size 100 --test-train-ratio 3 --no-weave $MODELS \
      $EVOL_BASE --eval-static-size 60 --evolution-seed $ESEED \
      --output-dir outputs/stability-hb-data-eseed${ESEED}
done

# PR Finance eseed=3,4
for ESEED in 3 4; do
    run "Stability: PR Finance / eseed=$ESEED" \
      --dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS \
      $EVOL_BASE --eval-static-size 50 --evolution-seed $ESEED \
      --output-dir outputs/stability-pr-finance-eseed${ESEED}
done

echo ""
echo "ALL SEED 3+4 STABILITY RUNS COMPLETE"
