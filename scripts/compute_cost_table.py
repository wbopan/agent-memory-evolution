#!/usr/bin/env python3
"""Compute cost table from LLM call logs in outputs/t1-* runs.

Scans llm_calls/ directories, classifies calls by role (task-agent, reflector,
judge, toolkit), aggregates tokens and cost, and outputs a LaTeX table for
the paper appendix.

Pricing (per 1M tokens, as of 2026-03):
  gpt-5.4-mini:  $0.75 input, $4.50 output
  gpt-5.3-codex: $1.75 input, $14.00 output
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── Pricing ──────────────────────────────────────────────────────────────────
# (model_substring → (input_per_1M, output_per_1M))
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "gpt-5.4-mini": (0.75, 4.50),
    "gpt-5.3-codex": (1.75, 14.00),
    "gpt-5.4": (6.00, 36.00),
    "deepseek-v3": (0.30, 0.88),  # openrouter deepseek
    "gpt-5.1-codex-mini": (0.25, 2.00),
}

SMOKE_TEST_MODELS = {"smoke-test/noop"}


def _price_per_token(model: str) -> tuple[float, float]:
    """Return (input_price, output_price) per token for a model."""
    for substr, (inp, out) in MODEL_PRICING.items():
        if substr in model:
            return inp / 1_000_000, out / 1_000_000
    return 0.0, 0.0


# ── Call classification ──────────────────────────────────────────────────────


def classify_call(filename: str, record: dict) -> str:
    """Classify an LLM call into a role.

    Returns one of: 'reflector', 'judge', 'task_agent', 'toolkit', 'smoke_test'.
    """
    # Reflector calls are saved as reflect_*.json
    if Path(filename).name.startswith("reflect_"):
        return "reflector"

    model = record.get("model", "")
    if model in SMOKE_TEST_MODELS:
        return "smoke_test"

    # Get first user message content
    user_msg = ""
    for m in record.get("messages", []):
        if m.get("role") == "user":
            user_msg = m.get("content", "")
            break

    msg_head = user_msg[:400]

    # Judge / rubric scoring calls (HealthBench, PRBench)
    if "rubric" in msg_head.lower() and "score" in msg_head.lower():
        return "judge"

    # Task agent: write (extraction) — recognizable by instruction-derived prompts
    if re.search(r"Extract\b.*\b(fact|knowledge|memory|reusable|durable|entity|event)", msg_head, re.I):
        return "task_agent"

    # Task agent: read (query generation)
    if re.search(r"(retrieval|query)\b.*\b(plan|query|request|generation)", msg_head, re.I):
        return "task_agent"

    # Task agent: answer generation (QA)
    if "Question:" in msg_head[:100] and ("Snippet" in user_msg[:600] or "Answer" in msg_head[:200]):
        return "task_agent"

    # Task agent: response generation patterns (various benchmarks)
    if re.search(r"(respond|answer|reply)\b.*(patient|user|question)", msg_head, re.I):
        return "task_agent"

    # Everything else is toolkit (KB program's llm_completion calls)
    return "toolkit"


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class CallStats:
    count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, record: dict) -> None:
        self.count += 1
        usage = record.get("usage", {})
        pt = usage.get("prompt_tokens") or 0
        ct = usage.get("completion_tokens") or 0
        self.prompt_tokens += pt
        self.completion_tokens += ct
        model = record.get("model", "")
        inp_price, out_price = _price_per_token(model)
        self.cost_usd += pt * inp_price + ct * out_price


@dataclass
class RunCost:
    name: str
    dataset: str
    config: str
    iterations: int = 0
    roles: dict[str, CallStats] = field(default_factory=lambda: defaultdict(CallStats))
    first_ts: datetime | None = None
    last_ts: datetime | None = None
    models_seen: set[str] = field(default_factory=set)

    @property
    def total(self) -> CallStats:
        t = CallStats()
        for s in self.roles.values():
            t.count += s.count
            t.prompt_tokens += s.prompt_tokens
            t.completion_tokens += s.completion_tokens
            t.cost_usd += s.cost_usd
        return t

    @property
    def wall_clock_hours(self) -> float | None:
        if self.first_ts and self.last_ts:
            return (self.last_ts - self.first_ts).total_seconds() / 3600
        return None


# ── Scan a run ───────────────────────────────────────────────────────────────


def scan_run(run_dir: Path) -> RunCost:
    name = run_dir.name
    # Parse dataset and config from name: t1-<dataset>-<config>
    parts = name.split("-", 2)
    if len(parts) >= 3:
        dataset = parts[1]
        config = parts[2]
    else:
        dataset = name
        config = "unknown"

    # Some multi-word datasets
    # t1-hb-emergency-ours → dataset=hb-emergency, config=ours
    # t1-alfworld-unseen-ours → dataset=alfworld-unseen, config=ours
    # t1-pr-legal-ours → dataset=pr-legal, config=ours
    known_datasets = [
        "hb-emergency",
        "hb-data-tasks",
        "alfworld-seen",
        "alfworld-unseen",
        "pr-legal",
        "pr-finance",
        "locomo",
    ]
    for ds in known_datasets:
        if name.startswith(f"t1-{ds}-"):
            dataset = ds
            config = name[len(f"t1-{ds}-") :]
            break

    cfg_path = run_dir / "config.json"
    iterations = 0
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        iterations = cfg.get("iterations", 0)

    rc = RunCost(name=name, dataset=dataset, config=config, iterations=iterations)

    llm_dir = run_dir / "llm_calls"
    if not llm_dir.exists():
        return rc

    for json_path in sorted(llm_dir.rglob("*.json")):
        if json_path.name == "failed_cases.json":
            continue
        try:
            record = json.loads(json_path.read_text())
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if not isinstance(record, dict):
            continue

        role = classify_call(str(json_path), record)
        if role == "smoke_test":
            continue

        rc.roles[role].add(record)
        rc.models_seen.add(record.get("model", "unknown"))

        ts_str = record.get("timestamp")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str)
                if rc.first_ts is None or ts < rc.first_ts:
                    rc.first_ts = ts
                if rc.last_ts is None or ts > rc.last_ts:
                    rc.last_ts = ts
            except ValueError:
                pass

    return rc


# ── Formatting ───────────────────────────────────────────────────────────────

DATASET_DISPLAY = {
    "locomo": "LoCoMo",
    "hb-emergency": "HB-Emergency",
    "hb-data-tasks": "HB-DataTasks",
    "alfworld-seen": "ALFWorld-Seen",
    "alfworld-unseen": "ALFWorld-Unseen",
    "pr-legal": "PR-Legal",
    "pr-finance": "PR-Finance",
}

CONFIG_DISPLAY = {
    "ours": r"\method{}",
    "no-memory": "No Memory",
    "vanilla-rag": "Vanilla RAG",
}


def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}K"
    return str(n)


def fmt_cost(c: float) -> str:
    if c < 0.01:
        return f"\\${c:.3f}"
    return f"\\${c:.2f}"


def fmt_hours(h: float | None) -> str:
    if h is None:
        return "---"
    if h < 1:
        return f"{h * 60:.0f}m"
    return f"{h:.1f}h"


def print_table(runs: list[RunCost]) -> None:
    """Print a human-readable summary table."""
    print(
        f"{'Run':<35} {'Calls':>7} {'Task':>6} {'Reflect':>7} {'Judge':>6} {'TKit':>5}  "
        f"{'In Tok':>8} {'Out Tok':>8} {'Cost':>8} {'Time':>6}"
    )
    print("-" * 120)
    for rc in runs:
        t = rc.total
        ta = rc.roles.get("task_agent", CallStats())
        rf = rc.roles.get("reflector", CallStats())
        jd = rc.roles.get("judge", CallStats())
        tk = rc.roles.get("toolkit", CallStats())
        print(
            f"{rc.name:<35} {t.count:>7} {ta.count:>6} {rf.count:>7} {jd.count:>6} {tk.count:>5}  "
            f"{fmt_tokens(t.prompt_tokens):>8} {fmt_tokens(t.completion_tokens):>8} "
            f"{fmt_cost(t.cost_usd):>8} {fmt_hours(rc.wall_clock_hours):>6}"
        )


def print_latex(runs: list[RunCost]) -> None:
    """Print LaTeX table for the paper appendix."""
    # Group by dataset
    by_dataset: dict[str, list[RunCost]] = defaultdict(list)
    for rc in runs:
        by_dataset[rc.dataset].append(rc)

    print(r"""
\begin{table}[h]
\centering
\caption{Computational cost of evolution and baselines across benchmarks. Calls are broken down by role: task agent (write/read/answer), reflector, rubric judge, and toolkit (KB program LLM calls). Cost is estimated from Azure OpenAI pricing for \texttt{gpt-5.4-mini} (\$0.75/\$4.50 per 1M in/out tokens) and \texttt{gpt-5.3-codex} (\$1.75/\$14.00 per 1M in/out tokens).}
\label{tab:cost}
\small
\begin{tabular}{ll rrrrr rr r r}
\toprule
\textbf{Benchmark} & \textbf{Config} & \textbf{Iter.} & \multicolumn{4}{c}{\textbf{API Calls}} & \textbf{Input} & \textbf{Output} & \textbf{Cost} & \textbf{Time} \\
\cmidrule(lr){4-7}
 & & & Task & Refl. & Judge & TKit & Tokens & Tokens & (USD) & \\
\midrule""")

    ds_order = ["locomo", "alfworld-unseen", "alfworld-seen", "hb-emergency", "hb-data-tasks", "pr-legal", "pr-finance"]
    config_order = ["ours", "vanilla-rag", "no-memory"]

    for ds in ds_order:
        if ds not in by_dataset:
            continue
        ds_runs = by_dataset[ds]
        ds_runs.sort(key=lambda r: config_order.index(r.config) if r.config in config_order else 99)

        for i, rc in enumerate(ds_runs):
            t = rc.total
            ta = rc.roles.get("task_agent", CallStats())
            rf = rc.roles.get("reflector", CallStats())
            jd = rc.roles.get("judge", CallStats())
            tk = rc.roles.get("toolkit", CallStats())
            ds_label = DATASET_DISPLAY.get(ds, ds) if i == 0 else ""
            cfg_label = CONFIG_DISPLAY.get(rc.config, rc.config)
            time_str = fmt_hours(rc.wall_clock_hours)

            print(
                f"{ds_label} & {cfg_label} & {rc.iterations} "
                f"& {ta.count} & {rf.count} & {jd.count} & {tk.count} "
                f"& {fmt_tokens(t.prompt_tokens)} & {fmt_tokens(t.completion_tokens)} "
                f"& {fmt_cost(t.cost_usd)} & {time_str} \\\\"
            )

        print(r"\midrule")

    print(r"""\bottomrule
\end{tabular}
\end{table}""")


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> None:
    outputs_dir = Path(__file__).resolve().parent.parent / "outputs"

    # Scan all t1-* runs
    run_dirs = sorted(outputs_dir.glob("t1-*"))
    run_dirs = [d for d in run_dirs if d.is_dir()]

    if not run_dirs:
        print("No t1-* output directories found.", file=sys.stderr)
        sys.exit(1)

    runs = [scan_run(d) for d in run_dirs]

    print("=" * 120)
    print("COST SUMMARY")
    print("=" * 120)
    print_table(runs)
    print()
    print_latex(runs)


if __name__ == "__main__":
    main()
