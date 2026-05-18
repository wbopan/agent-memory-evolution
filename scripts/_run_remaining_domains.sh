#!/usr/bin/env bash
# One-off: sequentially evolve + test-eval the remaining STATE-Bench domains
# (shopping_assistant, travel) with the same config as the customer_support
# codex run. After each domain's 20-iteration evolution, test-eval the top-3
# programs + all seeds on the held-out test set. Notify on each milestone.
set -u
export PATH="/home/v-wenbopan/.local/bin:/home/linuxbrew/.linuxbrew/bin:$PATH"
NOTIFY=/home/v-wenbopan/.local/bin/notify-send
WT=/home/v-wenbopan/repos/mstar/.claude/worktrees/trusting-hugle-bff8c1
cd "$WT" || { echo "worktree missing: $WT"; exit 1; }
mkdir -p outputs

EVO_ARGS="--iterations 20 \
--task-model azure/gpt-5.1 \
--reflect-model azure/responses/gpt-5.3-codex \
--toolkit-model azure/gpt-5.4-mini \
--task-lm-thinking-effort medium \
--azure-api-base https://mcg-oai-eastus2-resource.services.ai.azure.com \
--azure-api-version 2025-03-01-preview \
--embedding-model openrouter/baai/bge-m3 \
--batch-concurrency 16 \
--eval-train-ratio 2 --eval-static-size 50 --eval-rotate-size 5 \
--no-references --no-weave"

for domain in shopping_assistant travel; do
  TS=$(date +%Y%m%d_%H%M%S)
  OUTDIR="outputs/state_bench-${domain}-codex-${TS}"
  echo "$OUTDIR" > "/tmp/state_bench_${domain}_outdir.txt"
  echo "[pipeline $(date -u +%H:%M:%S)] evolution START: $domain -> $OUTDIR"
  # shellcheck disable=SC2086
  uv run python -m mstar.evolution --dataset state_bench --category "$domain" \
    $EVO_ARGS --output-dir "$OUTDIR" val_size=70 > "${OUTDIR}.log" 2>&1
  rc=$?
  echo "[pipeline $(date -u +%H:%M:%S)] evolution DONE: $domain rc=$rc"
  "$NOTIFY" "state_bench: $domain evolution done (rc=$rc)" "Starting test-eval (top-3 + all seeds)." 2>/dev/null || true

  echo "[pipeline $(date -u +%H:%M:%S)] test-eval START: $domain"
  uv run python scripts/_test_eval_top_programs.py "$OUTDIR" 3 > "${OUTDIR}.testeval.log" 2>&1
  trc=$?
  echo "[pipeline $(date -u +%H:%M:%S)] test-eval DONE: $domain rc=$trc"
  "$NOTIFY" "state_bench: $domain test-eval done (rc=$trc)" \
    "$(tail -n 8 "${OUTDIR}.testeval.log" 2>/dev/null | tr '\n' ' ')" 2>/dev/null || true
done

echo "[pipeline $(date -u +%H:%M:%S)] ALL DONE"
"$NOTIFY" -u critical "state_bench: shopping_assistant + travel ALL DONE" \
  "Both domains evolved + test-evaluated. See /tmp/state_bench_pipeline.log" 2>/dev/null || true
