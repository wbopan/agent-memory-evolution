#!/usr/bin/env bash
# PR-finance eseed=3 and eseed=4
set -euo pipefail
cd /home/wenbo/repos/agent-memory-evolution
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

for ESEED in 3 4; do
    run "Stability: PR Finance / eseed=$ESEED" \
      --dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS \
      $EVOL_BASE --eval-static-size 50 --evolution-seed $ESEED \
      --output-dir outputs/stability-pr-finance-eseed${ESEED}
done

echo ""
echo "ALL STABILITY RUNS COMPLETE"
