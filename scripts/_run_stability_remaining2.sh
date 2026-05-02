#!/usr/bin/env bash
# Resume HB-data eseed=1 from checkpoint, then run remaining experiments
set -euo pipefail
cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory

# Workaround: .pth editable install not being processed by Python in this venv.
# Explicitly add src/ to PYTHONPATH so `python -m programmaticmemory.evolution` works.
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

# 1. Resume HB Data eseed=1 (crashed at iter 18/20)
run "Stability: HB Data / eseed=1 (resume)" \
  --resume outputs/stability-hb-data-eseed1

# 2. HB Data eseed=2
run "Stability: HB Data / eseed=2" \
  --dataset healthbench --category health_data_tasks --test-size 100 --test-train-ratio 3 --no-weave $MODELS \
  $EVOL_BASE --eval-static-size 60 --evolution-seed 2 \
  --output-dir outputs/stability-hb-data-eseed2

# 3. PR Finance eseed=1
run "Stability: PR Finance / eseed=1" \
  --dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS \
  $EVOL_BASE --eval-static-size 50 --evolution-seed 1 \
  --output-dir outputs/stability-pr-finance-eseed1

# 4. PR Finance eseed=2
run "Stability: PR Finance / eseed=2" \
  --dataset prbench --category finance --test-size 50 --test-train-ratio 3 --no-weave $MODELS \
  $EVOL_BASE --eval-static-size 50 --evolution-seed 2 \
  --output-dir outputs/stability-pr-finance-eseed2

echo ""
echo "ALL REMAINING STABILITY RUNS COMPLETE"
