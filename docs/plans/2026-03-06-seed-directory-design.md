# Seed Directory for Initial Programs

**Date**: 2026-03-06

## Problem

The evolution CLI always starts with a single hardcoded seed (`INITIAL_KB_PROGRAM` from `prompts.py`). `EvolutionLoop` already supports `initial_programs: list[KBProgram]`, but the CLI doesn't expose it. Users can't easily add custom initial programs.

## Design

### Directory Convention

- `seeds/` at repo root, committed to git
- All `*.py` files in the directory are treated as seed programs
- Ships with `baseline.py` (the current `INITIAL_KB_PROGRAM`) as a working example

### CLI Flag

```bash
# Use custom seeds from a directory
uv run python -m programmaticmemory.evolution --seed-dir seeds/ --iterations 5

# Default behavior (no flag): uses built-in INITIAL_KB_PROGRAM as before
uv run python -m programmaticmemory.evolution --iterations 5
```

`--seed-dir <path>`: load all `*.py` files from the directory, sorted by filename, as initial programs. Mutually exclusive with the default single-seed behavior.

### Loading Logic (in `__main__.py`)

1. Glob `*.py` in the directory, sorted by name
2. Read each file's content → `KBProgram(source_code=content)`
3. Validate each via `compile_kb_program()` — fail fast with clear error on invalid seeds
4. Pass the list to `EvolutionLoop(initial_programs=programs)`

### Validation

Each seed file must be a valid Knowledge Base Program (3 classes + 4 constants). `compile_kb_program()` already checks this. On failure, print the filename and error, then exit.

## Changes

| File | Change |
|------|--------|
| `seeds/baseline.py` | New — copy of `INITIAL_KB_PROGRAM` |
| `src/.../evolution/__main__.py` | Add `--seed-dir` flag + loading logic (~15 lines) |

No changes to `loop.py`, `types.py`, or any other module.
