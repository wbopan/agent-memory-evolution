# Pluggable Val Scorer + ALFWorld Environment Evaluation

**Date**: 2026-03-03
**Status**: Approved

## Problem

ALFWorld evaluation currently uses a static key-element recall proxy (`task_desc → "microwave"/"fridge"` via `ExactMatchScorer`). Standard ALFWorld evaluation requires **real environment interaction** with binary task success. The current evaluator's val phase is hardcoded to "LLM generates answer → string-compare scorer", making it impossible to support interactive environments.

## Design

Split `_evaluate_val` into KB retrieval (shared) + downstream scoring (pluggable). Benchmarks that need custom val logic provide a `ValScorer` implementation.

### Core Principle

The evaluator's val phase does two things:
1. **KB retrieval** — query generation → KB read → get retrieved string (universal)
2. **Downstream scoring** — use retrieved info to produce a score (benchmark-specific)

Only (2) needs to be pluggable. (1) stays shared.

## Changes

### 1. `types.py` — Add `DataItem.metadata` and `ValScorer` protocol

```python
@dataclass
class DataItem:
    raw_text: str
    question: str
    expected_answer: str
    metadata: dict = field(default_factory=dict)  # benchmark-specific data

class ValScorer(Protocol):
    """Pluggable val scoring strategy. Replaces default LLM answer + string compare."""
    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
    ) -> list[tuple[str, float]]:
        """Returns (output_string, score) pairs aligned with items."""
        ...

@dataclass
class Dataset:
    # ... existing fields ...
    val_scorer: ValScorer | None = None  # NEW
```

### 2. `evaluator.py` — Refactor `_evaluate_val`

Split into three methods:

- `_retrieve_for_val(kb, query_cls, query_schema, val_data, logs, ...)` — existing Round 1 code (query generation batch → parse → serial KB reads). Returns `list[_QuerySlot | None]`.
- `_default_answer_and_score(slots, val_data, logs, toolkit, ...)` — existing Round 2 code (batch LLM answer generation + scorer). Returns scores/outputs/cases.
- `_evaluate_val(...)` — orchestrator: calls `_retrieve_for_val`, then dispatches to `val_scorer.score_batch` or `_default_answer_and_score`.

`MemoryEvaluator.__init__` accepts `val_scorer: ValScorer | None = None` (passed from `Dataset`).

### 3. `benchmarks/alfworld.py` — Rewrite

**Data loading:**
- Download `train` split expert trajectories (action/observation sequences from `traj_data.json`)
- Train items: trajectory text as `raw_text` → feeds into existing offline pipeline
- Val items: `valid_unseen` tasks with `metadata={"game_file": str(pddl_path), "task_type": ...}`

**`ALFWorldValScorer`:**
- `score_batch()`: for each val item, initialize TextWorld env from `metadata["game_file"]`, inject retrieved KB tips into action prompt, run up to 50 steps
- Per-step loop: build prompt (goal + tips + history + admissible actions) → LLM selects action → env.step → accumulate history
- Return `(trajectory_summary, 1.0 if success else 0.0)`

**Dependency:** `alfworld` package as optional dependency.

### 4. Existing benchmarks — Zero changes

kv_memory, locomo, mini_locomo, tau_bench, nyt_connections all have `val_scorer=None` and use the default path.

## Data Flow

```
Train phase (unchanged):
  Expert trajectories as raw_text
  → offline pipeline: LLM generates Observation → kb.write()
  → KB populated with procedural knowledge

Val phase (refactored):
  For each val item:
    1. [shared] Generate Query from task objective → kb.read() → retrieved tips
    2. [pluggable] ALFWorldValScorer:
       - Init env from metadata["game_file"]
       - Build action prompt with retrieved tips
       - Loop: LLM picks action → env.step → accumulate history
       - Return (trajectory, env_reward)
```

## Files Changed

| File | Change |
|---|---|
| `evolution/types.py` | Add `DataItem.metadata`, `ValScorer` protocol, `Dataset.val_scorer` |
| `evolution/evaluator.py` | Split `_evaluate_val` into retrieve + score; accept `val_scorer` param |
| `benchmarks/alfworld.py` | Rewrite: expert trajectory loading + `ALFWorldValScorer` |
| `evolution/__main__.py` | Pass `dataset.val_scorer` to evaluator |
| `pyproject.toml` | Add `alfworld` as optional dependency |

## What's NOT Changed

- `_evaluate_offline` / `_evaluate_online` train pipelines — untouched
- `Reflector`, `EvolutionLoop`, `Toolkit`, `sandbox.py` — untouched
- All existing benchmarks — untouched
- Prompt templates — untouched
- Test infrastructure — existing tests unaffected (new tests added for ALFWorld)
