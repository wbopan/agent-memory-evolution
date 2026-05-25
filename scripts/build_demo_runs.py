#!/usr/bin/env python3
"""Build demo run data (evolution tree + per-node source) from evolution outputs/.

For each experiment we reconstruct the FINAL on-disk program set and its lineage:

* The final pool summary in run.log maps name -> (hash, generation, score) for
  the programs that actually remain on disk (filenames get overwritten during a
  run, so only the last pool dump is authoritative).
* We replay run.log's "Selected parent (hash=, score=)" / "Saved program (...) ->
  programs/NAME.py" events; the LAST save for each NAME gives that final
  program's parent. Parents are resolved to a node by hash, falling back to the
  logged parent score (overwritten parents share their slot's final score).

Emitted as an ES module the demo merges into TREE_DATA / CODE_MAP.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUTPUTS = REPO / "outputs"

# experiment -> (task key, node-id prefix, human label, metric)
TASKS = {
    "t1-hb-emergency-ours": ("health", "hb", "HealthBench", "Rubric score"),
    "t1-pr-legal-ours": ("prbench", "pr", "PRBench", "Pairwise win"),
}

POOL_RE = re.compile(
    r"^\s*([0-9a-f]{16})\s+score=([\d.]+)\s+P=[\d.%]+\s+gen=(\d+)\s+programs/(\w+)\.py"
)
SEL_RE = re.compile(r"Selected parent \(hash=([0-9a-f]{16}), score=([\d.]+)\)")
SAVE_RE = re.compile(r"Saved program \((\w+)\).*?programs/(\w+)\.py")


def node_id(prefix: str, name: str) -> str:
    if name.startswith("seed_"):
        return f"{prefix}-s{name.split('_')[1]}"
    if name.startswith("iter_"):
        return f"{prefix}-{name.split('_')[1]}"
    return f"{prefix}-{name}"


def short_label(name: str) -> str:
    if name.startswith("seed_"):
        return f"Seed {name.split('_')[1]}"
    if name.startswith("iter_"):
        return f"Iteration {name.split('_')[1]}"
    return name


def feature_tag(source: str) -> str:
    s = source.lower()
    table = [
        ("rerank", "rerank"),
        ("bm25", "lexical recall"),
        ("chroma", "vector recall"),
        ("embedding", "vector recall"),
        ("n_results", "vector recall"),
        ("rubric", "rubric tuned"),
        ("counter(", "frequency signal"),
        ("graph", "graph memory"),
        ("summar", "summarize"),
        ("sqlite", "sql store"),
        ("def write", "write/read edit"),
    ]
    for needle, tag in table:
        if needle in s:
            return tag
    return "prompt edit"


def strip_header(text: str) -> str:
    lines = text.split("\n")
    i = 0
    if lines and lines[0].startswith("#"):
        i = 1
    while i < len(lines) and lines[i].strip() == "":
        i += 1
    return "\n".join(lines[i:])


def parse_experiment(exp_dir: Path):
    log = (exp_dir / "run.log").read_text().splitlines()

    # 1) final pool table -> name -> (hash, gen, score)
    pool_idx = max(i for i, ln in enumerate(log) if "Pool (" in ln)
    pool: dict[str, tuple[str, int, float]] = {}
    for ln in log[pool_idx + 1:]:
        m = POOL_RE.match(ln)
        if not m:
            break
        pool[m.group(4)] = (m.group(1), int(m.group(3)), float(m.group(2)))
    hash_to_name = {h: n for n, (h, _, _) in pool.items()}

    # 2) replay save events; last write per name wins
    cur_parent = None  # (hash, score)
    last_event: dict[str, dict] = {}
    for ln in log:
        ms = SEL_RE.search(ln)
        if ms:
            cur_parent = (ms.group(1), float(ms.group(2)))
            continue
        msave = SAVE_RE.search(ln)
        if msave:
            status, name = msave.group(1), msave.group(2)
            if status == "seed":
                last_event[name] = {"status": status, "parent": None}
            else:
                last_event[name] = {"status": status, "parent": cur_parent}

    def resolve_parent(prefix, parent, child_gen):
        if not parent:
            return None
        ph, ps = parent
        if ph in hash_to_name:
            cand = hash_to_name[ph]
            if pool[cand][1] < child_gen:  # parent must be an earlier generation
                return node_id(prefix, cand)
        # overwritten/edited parent: the genealogical parent sits exactly one
        # generation up; among those pick the closest logged parent score.
        target = child_gen - 1
        cands = [(abs(score - ps), name) for name, (_, gen, score) in pool.items() if gen == target]
        if not cands:  # fall back to any earlier generation
            cands = [(abs(score - ps), name) for name, (_, gen, score) in pool.items() if gen < child_gen]
        if cands:
            cands.sort()
            return node_id(prefix, cands[0][1])
        return None

    return pool, last_event, resolve_parent


def build():
    extra_tree = {}
    extra_code = {}
    for exp, (key, prefix, label, metric) in TASKS.items():
        exp_dir = OUTPUTS / exp
        if not exp_dir.exists():
            print(f"skip missing {exp}", file=sys.stderr)
            continue
        pool, last_event, resolve_parent = parse_experiment(exp_dir)
        nodes = []
        for name, (h, gen, score) in pool.items():
            nid = node_id(prefix, name)
            src = strip_header((exp_dir / "programs" / f"{name}.py").read_text())
            ev = last_event.get(name, {"status": "seed" if name.startswith("seed_") else "?", "parent": None})
            parent_id = resolve_parent(prefix, ev["parent"], gen)
            nodes.append(
                {
                    "id": nid,
                    "iter": gen,
                    "parent": parent_id,
                    "score": round(score, 4),
                    "label": short_label(name),
                    "tag": "seed program" if name.startswith("seed_") else feature_tag(src),
                }
            )
            extra_code[nid] = src
        # order seeds first then by generation, score
        nodes.sort(key=lambda n: (n["iter"], -n["score"]))
        extra_tree[key] = {"label": label, "metric": metric, "nodes": nodes}
        best = max(nodes, key=lambda n: n["score"])
        print(f"{key}: {len(nodes)} programs, best {best['score']:.3f} ({best['id']}), "
              f"roots {sum(1 for n in nodes if not n['parent'])}", file=sys.stderr)
    return extra_tree, extra_code


def main():
    extra_tree, extra_code = build()
    out = REPO / "web" / "src" / "viz" / "extra_runs.js"
    parts = [
        "// AUTO-GENERATED by scripts/build_demo_runs.py — do not edit by hand.",
        "// Real evolved programs (tree + source) for HealthBench and PRBench,",
        "// reconstructed from outputs/<experiment>/{run.log,summary.json,programs/}.",
        "export const EXTRA_TREE = " + json.dumps(extra_tree, indent=2, ensure_ascii=False) + ";",
        "export const EXTRA_CODE = " + json.dumps(extra_code, indent=2, ensure_ascii=False) + ";",
        "",
    ]
    out.write_text("\n".join(parts))
    print(f"wrote {out} ({out.stat().st_size} bytes)", file=sys.stderr)


if __name__ == "__main__":
    main()
