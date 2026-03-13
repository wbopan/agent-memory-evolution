# Experiment Readiness: CLI Flags, Baselines, and Multi-Metric Reporting

**Date:** 2026-03-13
**Goal:** Enable running all experiments for Table 1 (main results) and Table 2 (ablation study) without code changes.

## Scope

Six changes needed to run all paper experiments:

| # | Change | For |
|---|--------|-----|
| 1 | `--freeze-instructions` CLI flag | Table 2: −Instruction constants |
| 2 | `--max-fix-attempts N` CLI flag | Table 2: −Compile-fix loop |
| 3 | No-memory baseline KBProgram | Table 1: No Memory row |
| 4 | `extra_scorers` on Dataset | Table 1: LoCoMo EM + F1 |
| 5 | `seeds/single/` directory | Table 2: −Multi-seed |
| 6 | Table 1 LaTeX: drop ALFWorld Progress column | Table 1 layout |

## 1. `--freeze-instructions` Flag

**Purpose:** Ablation that prevents evolution from modifying the four instruction constants (`INSTRUCTION_KNOWLEDGE_ITEM`, `INSTRUCTION_QUERY`, `INSTRUCTION_RESPONSE`, `ALWAYS_ON_KNOWLEDGE`), isolating the contribution of prompt optimization vs memory design.

**Approach:** Post-mutation restoration. After `Reflector.reflect_and_mutate()` returns a child program, replace the child's instruction constants with the parent's originals. This is cleaner than modifying the reflection prompt because:
- The LLM can still reason about instructions (avoids confusing it)
- Implementation is a simple string replacement in `sandbox.py`'s `compile_kb_program`
- No changes to prompts or reflector internals

**Changes:**
- `__main__.py`: Add `--freeze-instructions` flag (default: `False`)
- `loop.py`: After `reflector.reflect_and_mutate()` returns a child, if `freeze_instructions=True`, call `freeze_instruction_constants(parent.source_code, child.source_code)` to produce a new source with the parent's constants restored
- `sandbox.py`: Add `freeze_instruction_constants(parent_source: str, child_source: str) -> str` — compiles both, extracts the 4 constants from parent, replaces them in child source via AST or regex

**Semantics:**
- `--freeze-instructions` (flag present) → constants frozen to seed values
- (flag absent) → normal behavior, constants evolve freely

## 2. `--max-fix-attempts N` CLI Flag

**Purpose:** Expose the existing `Reflector.max_fix_attempts` parameter (currently hardcoded default of 3). Setting to 0 disables the compile-fix loop for the ablation.

**Changes:**
- `__main__.py`: Add `--max-fix-attempts` arg (type=int, default=3)
- `__main__.py`: Pass to `Reflector(model=..., max_fix_attempts=args.max_fix_attempts, ...)`

**That's it.** The Reflector already supports this parameter; it's just not exposed via CLI.

## 3. No-Memory Baseline KBProgram

**Purpose:** A baseline that stores nothing and retrieves nothing, establishing the lower bound.

**File:** `src/programmaticmemory/baselines/no_memory.py`

**Content:** A valid KBProgram where:
- `KnowledgeBase.write()` is a no-op (pass)
- `KnowledgeBase.read()` returns `""`
- `KnowledgeItem` and `Query` have minimal fields (single `text: str`)
- All four instruction constants are set to minimal valid strings

**Usage:**
```bash
uv run python -m programmaticmemory.evolution --dataset locomo --baseline src/programmaticmemory/baselines/no_memory.py --no-weave
```

## 4. `extra_scorers` on Dataset

**Purpose:** Report additional metrics (e.g., EM alongside F1 for LoCoMo) without changing the evolution selection signal.

**Approach:** Post-evaluation scoring. After test eval produces `EvalResult` (with `per_case_outputs` and the corresponding test items), apply each extra scorer to compute additional metrics.

**Changes:**
- `types.py`: Add `extra_scorers: dict[str, Scorer] = field(default_factory=dict)` to `Dataset`
- `locomo.py`: Set `extra_scorers={"em": ExactMatchScorer()}` in the returned Dataset
- `loop.py`: After test eval (and final eval), if `dataset.extra_scorers` is non-empty, iterate over `per_case_outputs` × test items' `expected_answer`, compute each extra scorer, add to summary
- `EvolutionState`: Add `test_metrics: dict[str, dict[str, float]]` (maps scorer_name → {program_hash → score})
- Summary JSON output: Include `extra_metrics: {"em": 0.xx, ...}` alongside the primary score

**Key constraint:** Extra scorers are **report-only**. They never influence evolution selection, pool ranking, or final candidate selection.

**Edge case:** `ALFWorldValScorer` is a `ValScorer` (not `Scorer`) — it runs episodes, not string comparison. ALFWorld only needs Success (already the primary metric), so no extra_scorers needed there.

## 5. `seeds/single/` Directory

**Purpose:** Single-seed ablation for Table 2.

**Action:** Create `seeds/single/` containing only `llm_summarizer.py` (copied or symlinked from `seeds/`). This is the most generic seed — it summarizes and retrieves via LLM, a reasonable default starting point.

**Usage:**
```bash
uv run python -m programmaticmemory.evolution --seed-dir seeds/single ...
```

## 6. Table 1 LaTeX Update

**Purpose:** Remove ALFWorld Progress column (not supported by evaluator).

**Change in `main.tex`:**
- Remove the `Progress` column from Table 1
- ALFWorld columns become just `Success`
- Adjust `\multicolumn` spans accordingly

**New Table 1 layout:**
```
Method | LoCoMo EM | LoCoMo F1 | ALFWorld Success | Avg.
```

## Constraints

- No changes to `evaluator.py` — all multi-metric logic is post-hoc in `loop.py`
- No changes to `Reflector` internals — freeze-instructions is applied after mutation
- No changes to `EvalStrategy` protocol
- Backward compatible — all new flags have defaults matching current behavior
- `extra_scorers` only applies to string-based `Scorer` protocol, not `ValScorer`

## Files Changed

| File | Changes |
|------|---------|
| `evolution/__main__.py` | Add `--freeze-instructions`, `--max-fix-attempts` CLI args |
| `evolution/loop.py` | Freeze-instructions post-mutation, extra_scorers post-eval |
| `evolution/sandbox.py` | Add `freeze_instruction_constants()` helper |
| `evolution/types.py` | Add `extra_scorers` to Dataset, `test_metrics` to EvolutionState |
| `benchmarks/locomo.py` | Add `extra_scorers={"em": ExactMatchScorer()}` |
| `baselines/no_memory.py` | New file: empty KBProgram baseline |
| `seeds/single/` | New directory with single seed file |
| `Repos/paper/main.tex` | Remove ALFWorld Progress column from Table 1 |
