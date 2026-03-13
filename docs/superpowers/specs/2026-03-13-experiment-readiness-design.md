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
- No changes to prompts or reflector internals

**Implementation:** Use `compile_kb_program()` to extract the 4 constants from the parent, then regex-replace the corresponding module-level assignments in the child source. The constants are simple string assignments (`CONSTANT_NAME = "..."` or `CONSTANT_NAME = """..."""`). Use a regex pattern that matches `^CONSTANT_NAME\s*=\s*(?:"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|"{3}[\s\S]*?"{3}|'{3}[\s\S]*?'{3})` for each constant name, and replace with `CONSTANT_NAME = <repr(parent_value)>`.

After freezing, re-validate the child via `compile_kb_program()` + `smoke_test()`. If validation fails, discard the child (return None from that iteration). This is unlikely in practice since only string constants change, but ensures safety.

**Changes:**
- `__main__.py`: Add `--freeze-instructions` flag (default: `False`), pass to `EvolutionLoop`
- `loop.py`: After `reflector.reflect_and_mutate()` returns a child, if `freeze_instructions=True`, call `freeze_instruction_constants(parent.source_code, child.source_code)` → new source. Re-validate; if invalid, skip iteration.
- `sandbox.py`: Add `freeze_instruction_constants(parent_source: str, child_source: str) -> str` — compiles parent to extract the 4 constant values, regex-replaces them in child source

**Semantics:**
- `--freeze-instructions` (flag present) → constants frozen to seed values throughout evolution
- (flag absent) → normal behavior, constants evolve freely

## 2. `--max-fix-attempts N` CLI Flag

**Purpose:** Expose the existing `Reflector.max_fix_attempts` parameter (currently hardcoded default of 3). Setting to 0 disables the compile-fix loop for the ablation.

**Changes:**
- `__main__.py`: Add `--max-fix-attempts` arg (type=int, default=3)
- `__main__.py`: Pass to `Reflector(model=..., max_fix_attempts=args.max_fix_attempts, ...)`

**That's it.** The Reflector already supports this parameter; it's just not exposed via CLI.

**Note:** `max_fix_attempts=0` also disables the runtime-violation fix loop in `loop.py` (which uses the same parameter). This is acceptable for the ablation — both compile-fix and runtime-fix are part of the same "fix loop" mechanism.

## 3. No-Memory Baseline KBProgram

**Purpose:** A baseline that stores nothing and retrieves nothing, establishing the lower bound.

**File:** `src/programmaticmemory/baselines/no_memory.py`

Create this file as a new valid KBProgram where:
- `KnowledgeBase.write()` is a no-op (`pass`)
- `KnowledgeBase.read()` returns `""`
- `KnowledgeItem` and `Query` have minimal fields (single `text: str`)
- All four instruction constants are set to minimal valid strings

**Note:** The no-memory baseline still incurs LLM calls during training (the task agent generates KnowledgeItem JSON even though write discards it). This is intentional — it isolates the memory contribution, not the LLM cost.

**Usage:**
```bash
uv run python -m programmaticmemory.evolution --dataset locomo --baseline src/programmaticmemory/baselines/no_memory.py --no-weave
```

## 4. `extra_scorers` on Dataset

**Purpose:** Report additional metrics (e.g., EM alongside F1 for LoCoMo) without changing the evolution selection signal.

**Approach:** Post-evaluation scoring. After test eval (or final eval) produces an `EvalResult`, retain the full result (not just the score float). Apply each extra scorer to `per_case_outputs` × test items' `expected_answer` to compute additional metrics.

**Changes:**
- `types.py`: Add `extra_scorers: dict[str, Scorer] = field(default_factory=dict)` to `Dataset`
- `locomo.py`: Set `extra_scorers={"em": ExactMatchScorer()}` in the returned Dataset
- `loop.py`: In test eval and final eval blocks, retain the full `EvalResult` (currently only `test_result.score` is stored). After scoring, compute extra metrics:
  ```python
  extra = {}
  for name, scorer in self.dataset.extra_scorers.items():
      scores = [scorer(out, item.expected_answer)
                for out, item in zip(test_result.per_case_outputs, test_items)]
      extra[name] = sum(scores) / len(scores) if scores else 0.0
  ```
- Summary JSON: Add `extra_metrics` dict alongside existing score fields:
  ```json
  "test_evaluation": {
      "scores": {"abc123": 0.45},
      "extra_metrics": {"abc123": {"em": 0.32}}
  }
  ```

**Key constraint:** Extra scorers are **report-only**. They never influence evolution selection, pool ranking, or final candidate selection.

**Edge case:** `ALFWorldValScorer` is a `ValScorer` (not `Scorer`) — it runs episodes, not string comparison. ALFWorld only needs Success (already the primary metric), so no `extra_scorers` needed there. Extra scorers only work with the string-based `Scorer` protocol.

## 5. `seeds/single/` Directory

**Purpose:** Single-seed ablation for Table 2.

**Action:** Create `seeds/single/` containing a **copy** of `seeds/llm_summarizer.py` (not a symlink). This is the most generic seed — it summarizes and retrieves via LLM, a reasonable default starting point.

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
| `evolution/loop.py` | Freeze-instructions post-mutation, extra_scorers post-eval, retain full EvalResult |
| `evolution/sandbox.py` | Add `freeze_instruction_constants()` helper |
| `evolution/types.py` | Add `extra_scorers` to Dataset |
| `benchmarks/locomo.py` | Add `extra_scorers={"em": ExactMatchScorer()}` |
| `baselines/no_memory.py` | New file: empty KBProgram baseline |
| `seeds/single/llm_summarizer.py` | Copy of seeds/llm_summarizer.py |
| `Repos/paper/main.tex` | Remove ALFWorld Progress column from Table 1 |
