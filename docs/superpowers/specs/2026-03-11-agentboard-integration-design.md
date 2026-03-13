# AgentBoard Benchmark Integration Design

## Overview

Integrate three AgentBoard environments (ScienceWorld, BabyAI, PDDL) as a unified `agentboard` benchmark dataset, enabling evolution experiments on interactive multi-turn tasks alongside the existing ALFWorld integration.

## Motivation

The paper needs systematic experiments on interactive agent benchmarks. AgentBoard (NeurIPS 2024 Oral) is the primary interactive evaluation suite used by Evo-Memory. We need ScienceWorld, BabyAI, and PDDL to complement the existing ALFWorld benchmark.

Difficulty spectrum (from Evo-Memory, Gemini 2.5 Flash, Success Rate):
- PDDL: 0.22 (hardest)
- ScienceWorld: 0.58
- BabyAI: 0.61 (easiest, but included for coverage)
- AlfWorld: 0.66 (already integrated separately)

## File Structure

```
benchmarks/
  agentboard.py              # @register_dataset, data loading, AgentBoardValScorer
  _scienceworld_wrapper.py   # ScienceWorld text interface
  _babyai_wrapper.py         # BabyAI grid-to-text (ported from AgentBoard)
  _pddl_wrapper.py           # PDDL planning wrapper
```

## Common Wrapper Interface

Each environment wrapper exposes the same protocol:

```python
class EnvWrapper:
    def reset(self) -> str:
        """Reset environment, return initial text observation."""

    def step(self, action: str) -> tuple[str, float, bool]:
        """Execute text action, return (observation, progress_rate, done)."""

    def get_valid_actions(self) -> list[str]:
        """Return list of currently valid text actions."""

    def close(self) -> None:
        """Clean up resources."""
```

## Environment Wrappers

### ScienceWorld (`_scienceworld_wrapper.py`)

- Package: `scienceworld` (pip, requires Java/JVM)
- API: `ScienceWorldEnv` with text observations and text actions natively
- Train data: task descriptions from `env.get_task_description()` + gold action sequences as `raw_text`
- Val data: task instances with `metadata = {"task_name": str, "variation_idx": int}`
- Valid actions: `env.get_valid_action_object_combinations()` returns flat list of text strings
- Progress rate: `info['score'] / 100.0` (built-in 0-100 scale)
- Splits: `env.get_variations_train()`, `env.get_variations_dev()`, `env.get_variations_test()`
- 30 task types, ~6000+ total variations
- Simplifications: use `""` (no simplification) for faithful benchmarking

### BabyAI (`_babyai_wrapper.py`)

- Package: `minigrid>=2.3.0` (pip, pure Python)
- API: gymnasium-based with grid observations (`Box(0,255,(7,7,3),uint8)`) — needs text conversion
- Grid-to-text conversion: port AgentBoard's `postprocess_obs` logic
  - Parse 7x7x3 partial view grid
  - Compute absolute positions of visible objects relative to agent
  - Generate natural language: "There is a red ball 1 3 steps in front of you..."
- Text action space (from AgentBoard):
  - Always: `turn left`, `turn right`
  - Conditional: `move forward`, `pickup <obj>`, `drop`, `toggle`
  - High-level: `go to <obj>` (BFS pathfinding), `go through <door>`
- Progress rate: binary (1.0 on success, 0.0 on failure)
- Levels: 40 BabyAI levels registered in gymnasium, select a subset matching AgentBoard's config
- Train data: mission descriptions + level info as `raw_text`
- Val data: environment instances with `metadata = {"env_id": str, "seed": int}`

### PDDL (`_pddl_wrapper.py`)

- Package: `pddlgym` (pip)
- API: gymnasium-based with structured state/action
- Domains from AgentBoard: barman (20), blockworld (10), gripper (20), tyreworld (10)
- Text conversion: render PDDL state predicates as natural language, actions as operator strings
- Valid actions: enumerate grounded operators from current state
- Progress rate: `len(satisfied_goals) / len(total_goals)` (goal literal matching)
- Train data: domain descriptions + example problem solutions as `raw_text`
- Val data: problem instances with `metadata = {"domain": str, "problem_idx": int}`

## Main Benchmark File (`agentboard.py`)

### Registration

```python
@register_dataset("agentboard")
def load_agentboard(*, category: str | None = None, ...) -> Dataset:
```

- `category` selects environment: `"scienceworld"`, `"babyai"`, `"pddl"`
- If `category` is None, raise error listing available categories

### AgentBoardValScorer

Same pattern as `ALFWorldValScorer`:

```python
class AgentBoardValScorer:
    def score_batch(self, items, retrieved, task_model, instruction_response, always_on_knowledge):
        # Run episodes in ProcessPoolExecutor
        # Each episode: observe → LLM selects action (using KB tips) → step → repeat
        # Return list of (transcript, progress_rate)
```

Episode runner is a module-level function (picklable for ProcessPoolExecutor):

```python
def _run_episode(env_type, env_config, objective, tips, task_model, max_steps, always_on_knowledge):
    # Create wrapper based on env_type
    # Run observe-act loop with LLM action selection
    # Return (transcript, progress_rate)
```

### Action Selection

Reuse the same LLM prompting pattern as ALFWorld's `_select_action`:
- System context: environment description, goal, always-on knowledge
- Retrieved KB tips
- Trajectory history (truncated to last N steps)
- Valid actions list
- LLM outputs one action verbatim

## Dependencies

```toml
[project.optional-dependencies]
agentboard = ["scienceworld", "minigrid>=2.3.0", "pddlgym"]
```

## Integration Points

- Update `benchmarks/__init__.py` to import `agentboard`
- Update `CLAUDE.md` with new benchmark documentation
- Add pytest marker: `@pytest.mark.agentboard`
- CLI usage: `uv run python -m programmaticmemory.evolution --dataset agentboard --category scienceworld --iterations 10`

## What Stays Separate

- `alfworld.py` remains its own benchmark — different dependency chain (TextWorld + alfworld), already working, already has tests
- The `agentboard` dataset covers the three new environments only
