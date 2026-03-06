# Population-Based Parent Selection

## Problem

The evolution loop uses serial (1+1) greedy descent: one parent, one child per iteration, always advancing to the latest child. This limits exploration — a single bad mutation can derail the lineage, and there's no way to revisit previously promising programs.

## Design

Replace the single-lineage chain with a population pool and softmax-weighted parent selection.

### Pool Data Structure

New types in `types.py`:

```python
@dataclass
class PoolEntry:
    program: KBProgram
    eval_result: EvalResult
    score: float

class ProgramPool:
    """Unbounded pool of evaluated programs with softmax parent selection."""

    entries: list[PoolEntry]
    temperature: float  # default 0.15

    def add(self, program: KBProgram, eval_result: EvalResult) -> None
    def sample_parent(self) -> PoolEntry  # softmax-weighted

    @property
    def best(self) -> PoolEntry  # highest score
```

Selection distribution: `P(i) = exp(score_i / temperature) / Z`.

With temperature=0.15 and scores [0.6, 0.4, 0.4, 0.2]: P(0.6)=63%, P(0.4)=16.5% each, P(0.2)=4.3%. Mostly exploits strong programs, occasionally explores weak ones.

### Loop Changes

`EvolutionLoop.__init__` changes:
- `initial_program: KBProgram` → `initial_programs: list[KBProgram]`, defaults to `[KBProgram(source_code=INITIAL_KB_PROGRAM)]`
- `drop_degraded_program: bool` removed
- New parameter: `temperature: float = 0.15`

Loop flow:
1. Evaluate all seed programs, add each to pool
2. For each iteration:
   a. `pool.sample_parent()` → select parent (softmax-weighted)
   b. Reflect on parent using its stored `eval_result` → child
   c. Evaluate child
   d. Runtime fix loop (unchanged)
   e. Add child to pool unconditionally (even if score is worse)
3. Return `pool.best` as final result

### State Changes

```python
@dataclass
class EvolutionState:
    pool: ProgramPool          # replaces best_program/current_program/current_score
    best_score: float          # convenience, same as pool.best.score
    history: list[EvolutionRecord]
    total_iterations: int

@dataclass
class EvolutionRecord:
    iteration: int
    program: KBProgram
    score: float
    parent_hash: str | None    # which parent was selected
    # 'accepted' removed — all children added to pool
```

### CLI Changes

- `--temperature 0.15` (new flag, default 0.15)

### What Doesn't Change

- Evaluator, Reflector, sandbox, benchmarks — all untouched
- Runtime fix loop stays the same
- Reflection still receives the parent's stored eval_result
- Pool is unbounded (expected <20 programs total)
- One child per iteration (no parallelism)

## Temperature Calibration

Scores are in [0, 1]. Temperature 0.15 was chosen to satisfy:
- Program with score 0.6 selected ~60% of the time among [0.6, 0.4, 0.4, 0.2]
- Program with score 0.2 selected <5% of the time

Selection uses `math.exp` — no numpy dependency needed.
