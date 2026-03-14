# Lineage Log: Outcome Feedback for the Reflector

**Date**: 2026-03-14
**Status**: Approved

## Problem

The reflector has a systematic blind spot: it removes `toolkit.llm_completion()` from programs because it assumes LLM calls are expensive and hallucination-prone, when in fact the LLM summarizer is the highest-scoring approach. Every child of the LLM-summarizer seed (seed_1, score 0.289) removes the LLM call and scores worse (0.171, 0.149, 0.187, 0.251). The reflector never sees these outcomes, so it repeats the same mistake.

The root cause is **missing outcome feedback**: the reflector operates in a vacuum — it sees the current program and failed cases, but never learns whether its past mutations helped or hurt.

## Solution

Add a **lineage log** to the reflection prompt — a git-log-style history showing every ancestor and child of the current program with their commit messages, function changes, and score deltas. This gives the reflector direct evidence of what worked and what didn't.

## Design

### 1. Commit Message Output Format

Extend `PATCH_FORMAT_SPEC` in `prompts.py` to require a commit message block before the patch:

```
*** Commit Message
Title: <one-line summary>
- <diagnosis/reason>
- <what changed>

*** Begin Patch
...
*** End Patch
```

The commit message is part of the reflector's output, parsed alongside the patch.

**Seed commit messages**: Each seed `.py` file includes a module-level `COMMIT_MESSAGE` string constant describing the seed's strategy. These are loaded at startup and stored as the seed's commit message. If absent, falls back to "Initial seed program".

Seed commit messages:
- `llm_summarizer.py`: `"Title: LLM query-focused summarizer\n- Stores raw text, uses toolkit.llm_completion() in read() for query-focused summarization"`
- `vector_search.py`: `"Title: ChromaDB vector search with QA pairs\n- Extracts question-answer pairs, retrieves via semantic similarity"`
- `experience_learner.py`: `"Title: Lesson-fact dual storage with full recall\n- Extracts lessons and facts separately, returns all on read()"`

### 2. Commit Message Parsing

Add `_extract_commit_message(text: str) -> str | None` to `reflector.py`:

- Extract text between `*** Commit Message` and the next `***` marker (i.e., `*** Begin Patch`)
- Return the raw text block (title + bullet lines), or `None` if not found
- Non-fatal: if no commit message is found, the mutation proceeds with `commit_message=None`

### 3. Storage

Add `commit_message: str | None = None` field to `PoolEntry` in `types.py`.

In `loop.py`, after a successful reflection:
- Parse commit message from reflector output
- Pass it to `pool.add(..., commit_message=commit_message)`

This requires `reflect_and_mutate` to return the commit message alongside the `KBProgram`. Introduce a result dataclass:

```python
@dataclass
class ReflectionResult:
    program: KBProgram
    commit_message: str | None = None
```

Callers check `result is None` for failure, then access `result.program` and `result.commit_message`.

### 4. Function Diff Detection

Add `diff_functions(parent_source: str, child_source: str) -> tuple[list[str], list[str]]` as a module-level utility in `types.py` (or a new `diff.py`):

- `ast.parse()` both sources
- Extract top-level function names + class method names (e.g., `KnowledgeBase.read`, `_helper`)
- Return `(added, removed)` — names present in child but not parent, and vice versa
- Graceful on parse failure: return `([], [])` if either source fails to parse

### 5. Lineage Log Builder

Add `build_lineage_log(pool: ProgramPool, entry: PoolEntry) -> str` to `prompts.py` (or `types.py`):

**Algorithm:**
1. Walk `parent_hash` chain upward from `entry` to collect all ancestors (ordered root → entry)
2. Find all direct children of `entry` in the pool (entries whose `parent_hash == entry.program.hash`)
3. Format as a git-log-style text block

**Format for each commit entry:**
```
commit <program_hash> (<name>) score=<score> (Δ<delta>)[ ← REGRESSION]
  <commit_message or "Initial seed program">
  [+ added_func_1(), added_func_2()]
  [- removed_func_1()]
```

- The commit ID is the program's content hash (`KBProgram.hash`, 16-char hex)
- `Δ` is computed relative to the parent entry in the chain
- `← REGRESSION` marker if score < parent score
- `* current` marker on the entry being reflected
- Children shown after the current entry with their deltas

**Example output:**
```
<lineage_log>
commit 35abdb59d95d810a (seed_1) score=0.289
  Title: LLM query-focused summarizer
  - Stores raw text, uses toolkit.llm_completion() in read() for query-focused summarization

commit 69491885a5859af0 (iter_1) score=0.171 (Δ-0.118) ← REGRESSION
  Title: Replace LLM summarizer with deterministic retrieval
  - Replaced llm_completion with token overlap scoring
  + entity_filter(), token_overlap_score()
  - llm_completion() in read()

* current: 35abdb59d95d810a (seed_1) score=0.289  ← you are improving this

commit 83d78448578df627 (iter_8) score=0.251 (Δ-0.038) ← REGRESSION
  Title: Add structured schema with SQLite storage
  - Added people/topics fields and SQLite storage
  + _init_db(), _store_structured()
  - raw_texts list
</lineage_log>
```

### 6. Integration into Reflection Prompt

Add `lineage_log: str | None = None` parameter to `build_reflection_user_prompt()`. Insert the lineage log section after `<evaluation_score>`, before train/fail sections:

```
The following is the evolution history of the current program's lineage. \
Each entry shows what was changed, what functions were added/removed, and the resulting score. \
Pay close attention to REGRESSION markers — these indicate changes that hurt performance. \
Do NOT repeat changes that previously caused regressions.

<lineage_log>
...
</lineage_log>
```

### 7. Integration into Loop

In `loop.py`, before calling `reflect_and_mutate`:

```python
lineage_log = build_lineage_log(pool, parent_entry)
```

Pass `lineage_log` through to the prompt builder. The `use_references` flag and reference logic remain but are disabled by default.

### 8. Token Budget

Each commit entry is ~150-200 chars. A 15-iteration lineage is ~2.5K chars — well within budget and far smaller than the reference programs approach (20K+).

## Files Changed

| File | Change |
|------|--------|
| `types.py` | Add `commit_message` to `PoolEntry`, update `ProgramPool.add()` to accept `commit_message`, add `diff_functions()` utility |
| `prompts.py` | Extend `PATCH_FORMAT_SPEC` with commit message format, add `build_lineage_log()`, add `lineage_log` param to `build_reflection_user_prompt()` |
| `reflector.py` | Add `_extract_commit_message()`, return `ReflectionResult` from `reflect_and_mutate()` |
| `loop.py` | Build lineage log, pass to reflector, store commit message in pool |
| `__main__.py` | `--no-references` default already changed to `True` (prerequisite, done), load `COMMIT_MESSAGE` from seed files |
| `seeds/*.py` | Add `COMMIT_MESSAGE` constant to each seed file |

## Acceptance Criteria

### AC1: LLM Integration Test — Commit Message Generation + Parsing

**Test**: `test_commit_message_generation` in `test_llm_integration.py`

Sends a reflection prompt (with the new commit message format requirement) to GPT 5.3 CodeX. Verifies:

1. The LLM output contains a `*** Commit Message` block
2. `_extract_commit_message()` parses a non-empty commit message from the output
3. The commit message contains a `Title:` line
4. The `*** Begin Patch` / `*** End Patch` is still present and parseable
5. `apply_patch()` succeeds and `compile_kb_program()` validates the result
6. Snapshot captures `{prompt, output, commit_message, patched_code}`

This proves the LLM can produce the new format and the parser handles it correctly.

### AC2: Snapshot Test — Lineage Log in Reflection Prompt

**Test**: `test_lineage_log_in_reflection_prompt` in `test_prompts.py`

Constructs a mock lineage with 5 entries:
- seed (score 0.289, commit message: "LLM query-focused summarizer")
- iter_1 child of seed (score 0.171, commit message about removing LLM, regression)
- iter_4 child of seed (score 0.310, commit message about improving prompts)
- iter_8 child of iter_4 (score 0.280, commit message, regression)
- iter_12 child of iter_4 (score 0.355, commit message, improvement)

Calls `build_lineage_log(pool, seed_entry)` and `build_reflection_user_prompt(..., lineage_log=log)`. Snapshot captures the full prompt.

**Subagent verification**: After building the prompt, dispatches a subagent (Claude) that receives ONLY the rendered prompt text and must answer:
1. Which commit is the current program being improved? (answer: seed)
2. Which commits are its direct children? (answer: iter_1, iter_4)
3. Which commits are regressions? (answer: iter_1, iter_8)
4. What pattern do the regressions share? (answer: both diverged from LLM-based approaches or made structural changes that hurt)
5. What should the reflector avoid? (answer: removing llm_completion / the pattern that caused iter_1's regression)

The subagent's answers are asserted programmatically (e.g., "iter_1" appears in the regressions answer, "seed" appears in the current program answer).

## Out of Scope

- Multi-strategy reflection (prompt-only, memory-only, crossover) — separate future design
- Truncating lineage logs beyond a max length — defer until we see token budgets in practice
- Storing commit messages in output files — nice-to-have, not required for this spec
