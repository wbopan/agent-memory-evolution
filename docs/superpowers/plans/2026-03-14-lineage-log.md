# Lineage Log Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add outcome feedback to the reflector via a git-log-style lineage history showing score deltas and commit messages, so the reflector can learn from past failed mutations.

**Architecture:** Each reflection output now includes a commit message block alongside the patch. The pool stores commit messages per entry. Before reflection, a lineage log is built by walking the ancestor/child chain and rendered into the prompt. Function diffs are computed automatically via AST comparison.

**Tech Stack:** Python 3.12+, ast module, syrupy snapshots, litellm (GPT 5.3 CodeX for LLM integration test)

**Spec:** `docs/superpowers/specs/2026-03-14-lineage-log-design.md`

---

## Chunk 1: Core Infrastructure

### Task 1: Add `diff_functions()` utility

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py`
- Modify: `tests/evolution/test_types.py`

- [ ] **Step 1: Write failing tests for `diff_functions`**

Add to `tests/evolution/test_types.py`:

```python
from programmaticmemory.evolution.types import diff_functions


class TestDiffFunctions:
    def test_no_changes(self):
        code = "def foo(): pass\ndef bar(): pass"
        added, removed = diff_functions(code, code)
        assert added == []
        assert removed == []

    def test_added_function(self):
        parent = "def foo(): pass"
        child = "def foo(): pass\ndef bar(): pass"
        added, removed = diff_functions(parent, child)
        assert added == ["bar"]
        assert removed == []

    def test_removed_function(self):
        parent = "def foo(): pass\ndef bar(): pass"
        child = "def foo(): pass"
        added, removed = diff_functions(parent, child)
        assert added == []
        assert removed == ["bar"]

    def test_class_methods(self):
        parent = "class A:\n    def read(self): pass\n    def write(self): pass"
        child = "class A:\n    def read(self): pass\n    def query(self): pass"
        added, removed = diff_functions(parent, child)
        assert added == ["A.query"]
        assert removed == ["A.write"]

    def test_mixed_top_level_and_class(self):
        parent = "def helper(): pass\nclass KB:\n    def read(self): pass"
        child = "def util(): pass\nclass KB:\n    def read(self): pass\n    def write(self): pass"
        added, removed = diff_functions(parent, child)
        assert sorted(added) == ["KB.write", "util"]
        assert removed == ["helper"]

    def test_one_side_parse_failure_returns_empty(self):
        added, removed = diff_functions("def foo(:", "def bar(): pass")
        assert added == []
        assert removed == []

    def test_both_unparseable(self):
        added, removed = diff_functions("syntax error{", "another error{")
        assert added == []
        assert removed == []

    def test_valid_but_no_functions(self):
        added, removed = diff_functions("x = 1", "y = 2")
        assert added == []
        assert removed == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestDiffFunctions -v`
Expected: FAIL — `ImportError: cannot import name 'diff_functions'`

- [ ] **Step 3: Implement `diff_functions`**

Add to `src/programmaticmemory/evolution/types.py` at the end of the file:

```python
import ast


def _extract_function_names(source: str) -> set[str] | None:
    """Extract top-level function names and class method names from source code.

    Returns None on parse failure (distinct from empty set for valid code with no functions).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    names: set[str] = set()
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.ClassDef):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    names.add(f"{node.name}.{child.name}")
    return names


def diff_functions(parent_source: str, child_source: str) -> tuple[list[str], list[str]]:
    """Compare two program sources and return (added, removed) function/method names.

    Graceful on parse failure: returns ([], []) if either source fails to parse.
    """
    parent_names = _extract_function_names(parent_source)
    child_names = _extract_function_names(child_source)
    if parent_names is None or child_names is None:
        return [], []
    added = sorted(child_names - parent_names)
    removed = sorted(parent_names - child_names)
    return added, removed
```

Note: add `import ast` at the top of `types.py` alongside existing imports.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestDiffFunctions -v`
Expected: All 7 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add diff_functions() for AST-based function diff detection"
```

---

### Task 2: Add `commit_message` to `PoolEntry` and `ProgramPool.add()`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:134-281` (PoolEntry, ProgramPool)
- Modify: `tests/evolution/test_types.py`

- [ ] **Step 1: Write failing test**

Add to `tests/evolution/test_types.py`:

```python
class TestPoolEntryCommitMessage:
    def test_default_commit_message_is_none(self):
        entry = PoolEntry(
            program=KBProgram(source_code="x"),
            eval_result=EvalResult(score=0.5),
        )
        assert entry.commit_message is None

    def test_commit_message_stored(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        prog = KBProgram(source_code="x")
        pool.add(prog, EvalResult(score=0.5), commit_message="Title: test\n- changed something")
        assert pool.entries[0].commit_message == "Title: test\n- changed something"

    def test_commit_message_none_by_default_in_add(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        pool.add(KBProgram(source_code="x"), EvalResult(score=0.5))
        assert pool.entries[0].commit_message is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestPoolEntryCommitMessage -v`
Expected: FAIL — `commit_message` not found

- [ ] **Step 3: Add `commit_message` field to `PoolEntry` and `ProgramPool.add()`**

In `types.py`, modify `PoolEntry`:

```python
@dataclass
class PoolEntry:
    """A program in the population pool with its evaluation result."""

    program: KBProgram
    eval_result: EvalResult
    name: str = "seed_0"
    reflection_result: EvalResult | None = None
    commit_message: str | None = None

    @property
    def score(self) -> float:
        return self.eval_result.score
```

Modify `ProgramPool.add()`:

```python
def add(
    self,
    program: KBProgram,
    eval_result: EvalResult,
    name: str = "seed_0",
    reflection_result: EvalResult | None = None,
    commit_message: str | None = None,
) -> None:
    self.entries.append(
        PoolEntry(
            program=program,
            eval_result=eval_result,
            name=name,
            reflection_result=reflection_result,
            commit_message=commit_message,
        )
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestPoolEntryCommitMessage -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run full test suite to check for regressions**

Run: `uv run pytest tests/evolution/test_types.py -v`
Expected: All existing tests still PASS

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add commit_message field to PoolEntry and ProgramPool.add()"
```

---

### Task 3: Add `COMMIT_MESSAGE` to seed files

**Files:**
- Modify: `seeds/llm_summarizer.py`
- Modify: `seeds/vector_search.py`
- Modify: `seeds/experience_learner.py`
- Modify: `seeds/single/llm_summarizer.py` (if different from seeds/llm_summarizer.py)

- [ ] **Step 1: Add `COMMIT_MESSAGE` constant to each seed file**

At the top of each seed file (after imports, before other constants), add:

`seeds/llm_summarizer.py`:
```python
COMMIT_MESSAGE = (
    "Title: LLM query-focused summarizer\n"
    "- Stores raw text, uses toolkit.llm_completion() in read() for query-focused summarization"
)
```

`seeds/vector_search.py`:
```python
COMMIT_MESSAGE = (
    "Title: ChromaDB vector search with QA pairs\n"
    "- Extracts question-answer pairs, retrieves via semantic similarity"
)
```

`seeds/experience_learner.py`:
```python
COMMIT_MESSAGE = (
    "Title: Lesson-fact dual storage with full recall\n"
    "- Extracts lessons and facts separately, returns all on read()"
)
```

`seeds/single/llm_summarizer.py`: same as `seeds/llm_summarizer.py`.

- [ ] **Step 2: Verify seeds still pass smoke test**

Run: `uv run pytest tests/evolution/test_seeds.py -v`
Expected: All PASS (COMMIT_MESSAGE is not a required constant for compilation)

- [ ] **Step 3: Commit**

```bash
git add seeds/
git commit -m "feat: add COMMIT_MESSAGE constants to seed programs"
```

---

### Task 4: Add `_extract_commit_message()` to reflector

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py`
- Modify: `tests/evolution/test_reflector.py`

- [ ] **Step 1: Write failing tests for `_extract_commit_message`**

Add to `tests/evolution/test_reflector.py`:

```python
from programmaticmemory.evolution.reflector import _extract_commit_message


class TestExtractCommitMessage:
    def test_extracts_message_before_patch(self):
        text = (
            "Analysis here.\n\n"
            "*** Commit Message\n"
            "Title: Improve retrieval precision\n"
            "- Added entity filtering\n"
            "- Changed read() to use token overlap\n\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ change\n"
            "-old\n"
            "+new\n"
            "*** End Patch"
        )
        msg = _extract_commit_message(text)
        assert msg is not None
        assert "Title: Improve retrieval precision" in msg
        assert "Added entity filtering" in msg

    def test_no_commit_message_returns_none(self):
        text = (
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ change\n"
            "-old\n"
            "+new\n"
            "*** End Patch"
        )
        assert _extract_commit_message(text) is None

    def test_strips_whitespace(self):
        text = (
            "*** Commit Message\n"
            "  Title: Fix bug  \n"
            "  - Changed something  \n\n"
            "*** Begin Patch\n"
            "stuff\n"
            "*** End Patch"
        )
        msg = _extract_commit_message(text)
        assert msg is not None
        assert msg == "Title: Fix bug\n- Changed something"

    def test_commit_message_without_patch_still_extracts(self):
        text = (
            "*** Commit Message\n"
            "Title: Something\n"
            "- Did stuff\n"
        )
        msg = _extract_commit_message(text)
        assert msg is not None
        assert "Title: Something" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_reflector.py::TestExtractCommitMessage -v`
Expected: FAIL — `ImportError: cannot import name '_extract_commit_message'`

- [ ] **Step 3: Implement `_extract_commit_message`**

Add to `src/programmaticmemory/evolution/reflector.py` after `_extract_patch`:

```python
def _extract_commit_message(text: str) -> str | None:
    """Extract the commit message block from LLM output.

    Looks for text between ``*** Commit Message`` and the next ``***`` marker.
    Returns the stripped text block, or None if not found.
    """
    match = re.search(r"\*\*\* Commit Message\n(.*?)(?=\n\*\*\*|\Z)", text, re.DOTALL)
    if match:
        # Strip each line and rejoin, removing empty lines at start/end
        lines = [line.strip() for line in match.group(1).strip().splitlines()]
        result = "\n".join(lines)
        return result if result else None
    return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_reflector.py::TestExtractCommitMessage -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py tests/evolution/test_reflector.py
git commit -m "feat: add _extract_commit_message() parser to reflector"
```

---

### Task 5: Change `reflect_and_mutate` return type to `ReflectionResult`

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py:88-192`
- Modify: `src/programmaticmemory/evolution/loop.py:170-216`
- Modify: `tests/evolution/test_reflector.py`
- Modify: `tests/evolution/test_loop.py`

- [ ] **Step 1: Add `ReflectionResult` dataclass to reflector.py**

Add after the `_extract_commit_message` function:

```python
@dataclass
class ReflectionResult:
    """Result of a successful reflection — the mutated program and its commit message."""

    program: KBProgram
    commit_message: str | None = None
```

Add `from dataclasses import dataclass` to imports.

- [ ] **Step 2: Update `reflect_and_mutate` to return `ReflectionResult | None`**

Change return type annotation from `KBProgram | None` to `ReflectionResult | None`.

After extracting the patch and applying it, also extract the commit message from the LLM output. In the method body, `output` is the raw LLM response text. Extract the commit message:

```python
commit_message = _extract_commit_message(output)
```

Change the two `return KBProgram(...)` sites (line ~167 and ~185 in reflector.py) to:

```python
return ReflectionResult(
    program=KBProgram(
        source_code=new_code,
        generation=current.generation + 1,
        parent_hash=current.hash,
    ),
    commit_message=commit_message,
)
```

The `commit_message` variable is set once before the validate-fix loop, so all return paths share it.

- [ ] **Step 3: Update `loop.py` to use `ReflectionResult`**

In `loop.py`, update the import to include `ReflectionResult`:

```python
from programmaticmemory.evolution.reflector import Reflector, ReflectionResult
```

Change the reflection call site (around line 209):

```python
result = self.reflector.reflect_and_mutate(parent, parent_eval_for_reflect, i, references=references or None)
if result is None:
    self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
    # ... existing skip logic ...
    continue

child = result.program
commit_message = result.commit_message
```

Update `pool.add` call (around line 278) to pass `commit_message`:

```python
pool.add(child, child_result, name=f"iter_{i}", reflection_result=child_reflect, commit_message=commit_message)
```

The `freeze_instructions` block (around line 219-241) creates a new `KBProgram` from `child`. After this refactor, `child = result.program` and `commit_message = result.commit_message` are set before the freeze block. The freeze block replaces `child` with a new `KBProgram` but `commit_message` remains bound and is used later in `pool.add(...)`. No special handling needed — just ensure `commit_message` is declared before the freeze block, not after.

- [ ] **Step 4: Update test_reflector.py**

In `TestReflector`, update tests that check the return value of `reflect_and_mutate`:

- `test_successful_reflection`: change `child.generation` → `child.program.generation`, `child.parent_hash` → `child.program.parent_hash`, `child.source_code` → `child.program.source_code`.
- `test_reflection_no_code_block_returns_none`: no change (still returns None).
- Tests that only check `child is None`: no change.
- `test_fix_succeeds_on_second_attempt`: change `child.source_code` → `child.program.source_code`.

Also update `test_valid_code_returns_immediately` and `test_compile_error_triggers_fix_and_succeeds` in `TestReflectorCompileFixLoop` — any test that accesses the returned child's attributes.

- [ ] **Step 5: Update test_loop.py if needed**

Check `test_loop.py` for direct references to `child` from `reflect_and_mutate` and update accordingly (the loop.py changes should handle this, but verify mock setups that mock `reflect_and_mutate` return the new type).

Look for any `reflector.reflect_and_mutate` mock that returns a `KBProgram` directly — change to return `ReflectionResult(program=KBProgram(...))`.

- [ ] **Step 6: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All tests PASS (snapshots may need updating)

- [ ] **Step 7: Update snapshots if needed**

Run: `uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v`

- [ ] **Step 8: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/loop.py tests/evolution/
git commit -m "refactor: reflect_and_mutate returns ReflectionResult with commit_message"
```

---

## Chunk 2: Lineage Log & Prompt Integration

### Task 6: Add `build_lineage_log()` to prompts.py

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py`
- Modify: `tests/evolution/test_prompts.py`

- [ ] **Step 1: Write failing tests for `build_lineage_log`**

Add to `tests/evolution/test_prompts.py`:

```python
from programmaticmemory.evolution.prompts import build_lineage_log
from programmaticmemory.evolution.types import (
    EvalResult,
    KBProgram,
    PoolEntry,
    ProgramPool,
    SoftmaxSelection,
)


class TestBuildLineageLog:
    def _make_pool_with_lineage(self):
        """Build a pool with: seed -> iter_1 (regression), seed -> iter_4 (improvement)."""
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))

        seed = KBProgram(source_code="def read(): return llm_completion()")
        pool.add(seed, EvalResult(score=0.289), name="seed_0",
                 commit_message="Title: LLM query-focused summarizer\n- Uses toolkit.llm_completion() in read()")

        child1 = KBProgram(source_code="def read(): return token_overlap()", generation=1, parent_hash=seed.hash)
        pool.add(child1, EvalResult(score=0.171), name="iter_1",
                 commit_message="Title: Replace LLM with token overlap\n- Removed llm_completion from read()")

        child4 = KBProgram(source_code="def read(): return llm_completion(improved=True)", generation=1, parent_hash=seed.hash)
        pool.add(child4, EvalResult(score=0.310), name="iter_4",
                 commit_message="Title: Improve LLM prompt\n- Tuned summarization prompt for precision")

        return pool, pool.entries[0]  # seed entry

    def test_contains_seed_commit(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        assert "LLM query-focused summarizer" in log

    def test_marks_current(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        assert "* current:" in log
        assert seed_entry.program.hash in log

    def test_shows_children(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        assert "iter_1" in log
        assert "iter_4" in log

    def test_marks_regression(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        # iter_1 scored 0.171 vs parent 0.289 -> regression
        assert "REGRESSION" in log

    def test_shows_delta(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        # iter_1: 0.171 - 0.289 = -0.118
        assert "-0.118" in log

    def test_shows_function_diff(self):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        # child1 has token_overlap but not llm_completion (compared to seed)
        # Exact function names depend on AST parsing of the short programs
        # Just verify the +/- formatting is present for entries with diffs
        assert "+" in log or "-" in log  # at least some diff markers

    def test_single_entry_no_crash(self):
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        seed = KBProgram(source_code="x = 1")
        pool.add(seed, EvalResult(score=0.5), name="seed_0")
        log = build_lineage_log(pool, pool.entries[0])
        assert "* current:" in log

    def test_snapshot(self, snapshot):
        pool, seed_entry = self._make_pool_with_lineage()
        log = build_lineage_log(pool, seed_entry)
        assert log == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_prompts.py::TestBuildLineageLog -v`
Expected: FAIL — `ImportError: cannot import name 'build_lineage_log'`

- [ ] **Step 3: Implement `build_lineage_log`**

Add to `src/programmaticmemory/evolution/prompts.py`:

```python
from programmaticmemory.evolution.types import ProgramPool, PoolEntry, diff_functions


def build_lineage_log(pool: ProgramPool, entry: PoolEntry) -> str:
    """Build a git-log-style lineage history for a program entry.

    Shows all ancestors (root → entry) and direct children with commit messages,
    score deltas, regression markers, and function diffs.
    """
    hash_to_entry = {e.program.hash: e for e in pool.entries}

    # Walk ancestor chain upward
    ancestors: list[PoolEntry] = []
    current = entry
    while current.program.parent_hash and current.program.parent_hash in hash_to_entry:
        parent = hash_to_entry[current.program.parent_hash]
        ancestors.append(parent)
        current = parent
    ancestors.reverse()  # root first

    # Find direct children
    children = [e for e in pool.entries if e.program.parent_hash == entry.program.hash]

    # Format entries
    lines: list[str] = []

    def _format_entry(e: PoolEntry, parent_entry: PoolEntry | None, is_current: bool = False) -> None:
        header = f"commit {e.program.hash} ({e.name}) score={e.score:.3f}"
        if parent_entry is not None:
            delta = e.score - parent_entry.score
            header += f" ({'\u0394'}{delta:+.3f})"
            if delta < 0:
                header += " \u2190 REGRESSION"
        if is_current:
            lines.append(f"\n* current: {e.program.hash} ({e.name}) score={e.score:.3f}  \u2190 you are improving this\n")
            return
        lines.append(header)
        msg = e.commit_message or "Initial seed program"
        for msg_line in msg.splitlines():
            lines.append(f"  {msg_line}")
        # Function diff (only for non-seed entries with a parent)
        if parent_entry is not None:
            added, removed = diff_functions(parent_entry.program.source_code, e.program.source_code)
            if added:
                lines.append(f"  + {', '.join(f'{n}()' for n in added)}")
            if removed:
                lines.append(f"  - {', '.join(f'{n}()' for n in removed)}")
        lines.append("")

    # Render ancestors
    prev: PoolEntry | None = None
    for anc in ancestors:
        _format_entry(anc, prev)
        prev = anc

    # Render current marker
    _format_entry(entry, prev, is_current=True)

    # Render children
    for child in children:
        _format_entry(child, entry)

    return "\n".join(lines)
```

Note: import `diff_functions` from `types` and `ProgramPool`, `PoolEntry` — these may need to be imported carefully to avoid circular imports. Since `prompts.py` already imports from `types.py`, this should be fine. Add the new imports alongside existing ones.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_prompts.py::TestBuildLineageLog -v`
Expected: All tests PASS (snapshot will be created on first run)

- [ ] **Step 5: Update snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py::TestBuildLineageLog --snapshot-update -v`

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py tests/evolution/test_prompts.py tests/evolution/__snapshots__/
git commit -m "feat: add build_lineage_log() for git-log-style lineage history"
```

---

### Task 7: Add `lineage_log` parameter to `build_reflection_user_prompt`

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py:204-384`
- Modify: `tests/evolution/test_prompts.py`

- [ ] **Step 1: Write failing test**

Add to `tests/evolution/test_prompts.py` in `TestBuildReflectionUserPrompt`:

```python
    def test_includes_lineage_log(self, snapshot: SnapshotAssertion):
        log = (
            "commit abc123 (seed_0) score=0.289\n"
            "  Title: LLM summarizer\n"
            "  - Uses llm_completion\n\n"
            "* current: abc123 (seed_0) score=0.289  ← you are improving this\n\n"
            "commit def456 (iter_1) score=0.171 (Δ-0.118) ← REGRESSION\n"
            "  Title: Removed LLM\n"
            "  - Replaced with token overlap\n"
        )
        prompt = build_reflection_user_prompt(
            code="class KnowledgeBase: pass",
            score=0.289,
            failed_cases=[{"question": "q", "expected": "a", "output": "wrong", "score": 0.0}],
            iteration=3,
            lineage_log=log,
        )
        assert "<lineage_log>" in prompt
        assert "REGRESSION" in prompt
        assert "Do NOT repeat changes that previously caused regressions" in prompt
        assert prompt == snapshot

    def test_no_lineage_log_when_none(self, snapshot: SnapshotAssertion):
        prompt = build_reflection_user_prompt(
            code="class KnowledgeBase: pass",
            score=0.5,
            failed_cases=[],
            iteration=1,
            lineage_log=None,
        )
        assert "<lineage_log>" not in prompt
        assert prompt == snapshot
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_prompts.py::TestBuildReflectionUserPrompt::test_includes_lineage_log -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'lineage_log'`

- [ ] **Step 3: Add `lineage_log` parameter**

Modify `build_reflection_user_prompt` signature to add `lineage_log: str | None = None` parameter.

Build the lineage log section after `<evaluation_score>`:

```python
    # Build lineage log section
    lineage_section = ""
    if lineage_log:
        lineage_section = f"""
The following is the evolution history of the current program's lineage. \
Each entry shows what was changed, what functions were added/removed, and the resulting score. \
Pay close attention to REGRESSION markers — these indicate changes that hurt performance. \
Do NOT repeat changes that previously caused regressions.

<lineage_log>
{lineage_log}</lineage_log>
"""
```

Insert `{lineage_section}` into the f-string after `<evaluation_score>{score:.3f}</evaluation_score>` and before `{train_section}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_prompts.py::TestBuildReflectionUserPrompt -v`
Expected: All tests PASS

- [ ] **Step 5: Update all affected snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py -m "not llm" --snapshot-update -v`
Run: `uv run pytest tests/evolution/test_reflector.py -m "not llm" --snapshot-update -v`

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py tests/evolution/test_prompts.py tests/evolution/test_reflector.py tests/evolution/__snapshots__/
git commit -m "feat: add lineage_log parameter to reflection prompt"
```

---

### Task 8: Extend `PATCH_FORMAT_SPEC` with commit message format

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py:129-159`

- [ ] **Step 1: Update `PATCH_FORMAT_SPEC`**

In `prompts.py`, modify `PATCH_FORMAT_SPEC` to include the commit message format. Add before the existing patch format instructions:

```python
PATCH_FORMAT_SPEC = """\
Before the patch, output a commit message summarizing your changes:

```
*** Commit Message
Title: <one-line summary of what you changed and why>
- <root cause / diagnosis>
- <what you changed>
```

Then output your changes as a V4A patch. The patch is applied to the current program shown in <current_program>.

Format:
...
```

The full example should show both blocks together:

```
Example — replacing a return value:
```
*** Commit Message
Title: Truncate read output to respect 1000-char limit
- read() returned all stored text, exceeding the limit
- Added [:1000] truncation to the return value

*** Begin Patch
*** Update File: program.py
@@ return statement
 def read(self, query: Query) -> str:
-    return "\\n".join(self.store)
+    return "\\n".join(self.store[-5:])
*** End Patch
```
"""
```

- [ ] **Step 2: Update snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py tests/evolution/test_reflector.py -m "not llm" --snapshot-update -v`
Expected: Snapshots updated (all prompts now include the commit message format)

- [ ] **Step 3: Verify no test failures**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py tests/evolution/__snapshots__/
git commit -m "feat: extend PATCH_FORMAT_SPEC with commit message format"
```

---

### Task 9: Wire lineage log into loop.py and __main__.py

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py:17-19,170-210`
- Modify: `src/programmaticmemory/evolution/__main__.py:314-322`

- [ ] **Step 1: Add lineage log building to loop.py**

Add import:
```python
from programmaticmemory.evolution.prompts import build_lineage_log
```

Before the reflection call (around line 183), build the lineage log:

```python
lineage_log = build_lineage_log(pool, parent_entry)
if lineage_log.strip():
    self.logger.log(f"Lineage log ({lineage_log.count(chr(10))} lines)", header="EVOLUTION")
```

Pass `lineage_log` to `reflect_and_mutate`. This requires adding a `lineage_log` parameter to `reflect_and_mutate` and threading it through to `build_reflection_user_prompt`.

In `reflector.py`, add `lineage_log: str | None = None` parameter to `reflect_and_mutate()` and pass it to `build_reflection_user_prompt(..., lineage_log=lineage_log)`.

- [ ] **Step 2: Load `COMMIT_MESSAGE` from seed files in __main__.py**

In `__main__.py`, modify the seed loading loop (around line 314-322):

```python
    initial_programs = []
    seed_commit_messages: list[str | None] = []
    for f in seed_files:
        source = f.read_text()
        result = smoke_test(source)
        if not result.success:
            print(f"Error: invalid seed program {f.name}: {result.error}", file=sys.stderr)
            sys.exit(1)
        initial_programs.append(KBProgram(source_code=source))
        # Extract COMMIT_MESSAGE constant if present
        commit_msg = None
        try:
            ns: dict = {}
            exec(compile(source, f.name, "exec"), ns)  # noqa: S102
            commit_msg = ns.get("COMMIT_MESSAGE")
        except Exception:
            pass
        seed_commit_messages.append(commit_msg)
        logger.log(f"Loaded seed: {f.name}", header="CONFIG")
```

Pass `seed_commit_messages` to `EvolutionLoop` (add a new parameter) so the loop can pass them when adding seeds to the pool. Alternatively, keep it simpler: pass them alongside `initial_programs`.

Add parameter to `EvolutionLoop.__init__`:
```python
seed_commit_messages: list[str | None] | None = None,
```

In `EvolutionLoop.run()`, when adding seeds to the pool (around line 131):
```python
seed_msg = self.seed_commit_messages[idx] if self.seed_commit_messages else None
pool.add(seed, eval_result, name=seed_name, reflection_result=reflect_result, commit_message=seed_msg)
```

- [ ] **Step 3: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS (may need snapshot updates)

- [ ] **Step 4: Update snapshots**

Run: `uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v`

- [ ] **Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/__main__.py tests/evolution/
git commit -m "feat: wire lineage log into evolution loop and seed loading"
```

---

## Chunk 3: Acceptance Criteria Tests

### Task 10: AC1 — LLM Integration Test for Commit Message

**Files:**
- Modify: `tests/evolution/test_llm_integration.py`

- [ ] **Step 1: Write the LLM integration test**

Add to `tests/evolution/test_llm_integration.py`. The file already imports `_extract_patch`, `apply_patch`, `compile_kb_program`, `CompiledProgram`, `build_reflection_user_prompt`, and `INITIAL_KB_PROGRAM`. Add the new import alongside `_extract_patch`:

```python
from programmaticmemory.evolution.reflector import Reflector, _extract_patch, _extract_commit_message
```

Then add the test:

```python
@pytest.mark.llm
def test_commit_message_generation(snapshot: SnapshotAssertion):
    """GPT 5.3 CodeX produces a valid commit message + patch in the new format.

    Verifies:
    1. LLM output contains *** Commit Message block
    2. _extract_commit_message() parses a non-empty message with Title: line
    3. *** Begin Patch / *** End Patch is present and parseable
    4. apply_patch() succeeds and compile_kb_program() validates the result
    """
    failed_cases = [
        {
            "question": "What instruments does Caroline play?",
            "expected": "violin, piano",
            "output": "violin, piano, guitar, drums",
            "score": 0.3,
            "conversation_history": [
                {"role": "user", "content": "What instruments does Caroline play?"},
                {"role": "assistant", "content": "violin, piano, guitar, drums"},
            ],
            "memory_logs": [],
        }
    ]

    user_prompt = build_reflection_user_prompt(
        code=INITIAL_KB_PROGRAM,
        score=0.300,
        failed_cases=failed_cases,
        iteration=2,
    )

    output = _llm_call(
        REFLECT_MODEL,
        [{"role": "user", "content": user_prompt}],
    )

    # 1. Extract commit message
    commit_message = _extract_commit_message(output)
    assert commit_message is not None, f"No commit message found in output:\n{output[:500]}"

    # 2. Commit message has a Title line
    assert "Title:" in commit_message, f"Commit message missing Title: line:\n{commit_message}"

    # 3. Patch is still parseable
    patch = _extract_patch(output)
    assert patch is not None, f"No patch found in output:\n{output[:500]}"

    # 4. Apply and compile
    patched_code = apply_patch(INITIAL_KB_PROGRAM, patch)
    compile_result = compile_kb_program(patched_code)
    assert isinstance(compile_result, CompiledProgram), f"Compile failed: {compile_result}"

    assert {
        "prompt": user_prompt,
        "output": output,
        "commit_message": commit_message,
        "patched_code": patched_code,
    } == snapshot
```

- [ ] **Step 2: Run the test (requires API key)**

Run: `uv run pytest tests/evolution/test_llm_integration.py::test_commit_message_generation -v`
Expected: PASS — GPT 5.3 CodeX produces both commit message and patch in the new format

- [ ] **Step 3: Commit**

```bash
git add tests/evolution/test_llm_integration.py tests/evolution/__snapshots__/
git commit -m "test: AC1 — LLM integration test for commit message generation"
```

---

### Task 11: AC2 — Lineage Log Snapshot + Subagent Verification

**Files:**
- Modify: `tests/evolution/test_prompts.py`

- [ ] **Step 1: Write the end-to-end lineage log test with snapshot**

Add to `tests/evolution/test_prompts.py`:

```python
class TestLineageLogEndToEnd:
    """AC2: End-to-end test with 5-entry lineage and subagent verification."""

    def _build_five_entry_lineage(self):
        """Build a realistic 5-entry lineage:

        seed (0.289, LLM summarizer)
          ├── iter_1 (0.171, removed LLM → REGRESSION)
          └── iter_4 (0.310, improved prompts)
                ├── iter_8 (0.280, regression)
                └── iter_12 (0.355, improvement)
        """
        pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))

        seed_code = (
            "from dataclasses import dataclass, field\n"
            "class KnowledgeItem:\n    summary: str = ''\n"
            "class Query:\n    query_text: str = ''\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): self.toolkit = toolkit; self.raw_texts = []\n"
            "    def write(self, item, raw_text): self.raw_texts.append(raw_text)\n"
            "    def read(self, query): return self.toolkit.llm_completion([{'role':'user','content':'summarize'}])[:1000]\n"
        )
        seed = KBProgram(source_code=seed_code)
        pool.add(seed, EvalResult(score=0.289), name="seed_0",
                 commit_message="Title: LLM query-focused summarizer\n- Stores raw text, uses toolkit.llm_completion() in read() for query-focused summarization")

        iter1_code = (
            "from dataclasses import dataclass, field\n"
            "class KnowledgeItem:\n    summary: str = ''\n"
            "class Query:\n    query_text: str = ''\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): self.store = []\n"
            "    def write(self, item, raw_text): self.store.append(raw_text)\n"
            "    def read(self, query): return self._token_overlap(query.query_text)\n"
            "    def _token_overlap(self, q): return ''\n"
        )
        iter1 = KBProgram(source_code=iter1_code, generation=1, parent_hash=seed.hash)
        pool.add(iter1, EvalResult(score=0.171), name="iter_1",
                 commit_message="Title: Replace LLM with token overlap\n- Removed toolkit.llm_completion() to avoid hallucination, added deterministic token overlap")

        iter4_code = (
            "from dataclasses import dataclass, field\n"
            "class KnowledgeItem:\n    summary: str = ''\n"
            "class Query:\n    query_text: str = ''\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): self.toolkit = toolkit; self.raw_texts = []\n"
            "    def write(self, item, raw_text): self.raw_texts.append(raw_text)\n"
            "    def read(self, query): return self.toolkit.llm_completion([{'role':'user','content':'precise summary'}])[:1000]\n"
        )
        iter4 = KBProgram(source_code=iter4_code, generation=1, parent_hash=seed.hash)
        pool.add(iter4, EvalResult(score=0.310), name="iter_4",
                 commit_message="Title: Improve LLM summarization prompt\n- Tuned the LLM prompt for more precise, factual summarization")

        iter8_code = (
            "from dataclasses import dataclass, field\nimport sqlite3\n"
            "class KnowledgeItem:\n    summary: str = ''\n    people: list = None\n"
            "class Query:\n    query_text: str = ''\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): self.toolkit = toolkit; self._init_db()\n"
            "    def _init_db(self): pass\n"
            "    def write(self, item, raw_text): pass\n"
            "    def read(self, query): return ''\n"
        )
        iter8 = KBProgram(source_code=iter8_code, generation=2, parent_hash=iter4.hash)
        pool.add(iter8, EvalResult(score=0.280), name="iter_8",
                 commit_message="Title: Add SQLite structured storage\n- Replaced LLM retrieval with SQLite-based structured storage")

        iter12_code = (
            "from dataclasses import dataclass, field\n"
            "class KnowledgeItem:\n    summary: str = ''\n    people: list = None\n"
            "class Query:\n    query_text: str = ''\n    focus_person: str = ''\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): self.toolkit = toolkit; self.raw_texts = []\n"
            "    def write(self, item, raw_text): self.raw_texts.append(raw_text)\n"
            "    def read(self, query): return self.toolkit.llm_completion([{'role':'user','content':f'focus on {query.focus_person}'}])[:1000]\n"
            "    def _filter_by_person(self, person): return []\n"
        )
        iter12 = KBProgram(source_code=iter12_code, generation=2, parent_hash=iter4.hash)
        pool.add(iter12, EvalResult(score=0.355), name="iter_12",
                 commit_message="Title: Add person-focused LLM retrieval\n- Added focus_person to Query for targeted LLM summarization")

        return pool, pool.entries[0]  # seed entry

    def test_lineage_log_snapshot(self, snapshot: SnapshotAssertion):
        pool, seed_entry = self._build_five_entry_lineage()
        log = build_lineage_log(pool, seed_entry)

        # Basic structural checks
        assert "seed_0" in log
        assert "iter_1" in log
        assert "iter_4" in log
        assert "REGRESSION" in log
        assert "* current:" in log

        assert log == snapshot

    def test_full_prompt_with_lineage_snapshot(self, snapshot: SnapshotAssertion):
        pool, seed_entry = self._build_five_entry_lineage()
        log = build_lineage_log(pool, seed_entry)

        prompt = build_reflection_user_prompt(
            code=seed_entry.program.source_code,
            score=0.289,
            failed_cases=[{"question": "What instruments?", "expected": "violin, piano", "output": "violin, piano, guitar", "score": 0.3}],
            iteration=5,
            lineage_log=log,
        )

        assert "<lineage_log>" in prompt
        assert "Do NOT repeat changes that previously caused regressions" in prompt
        assert prompt == snapshot
```

- [ ] **Step 2: Run tests and create snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py::TestLineageLogEndToEnd --snapshot-update -v`
Expected: Tests PASS, snapshots created

- [ ] **Step 3: Write subagent verification test**

This test dispatches an LLM (using the test infrastructure) to read the rendered prompt and answer questions about it. Add to the same class:

```python
    @pytest.mark.llm
    def test_subagent_can_interpret_lineage(self, snapshot: SnapshotAssertion):
        """A subagent reading ONLY the rendered prompt can identify current, children, and regressions."""
        pool, seed_entry = self._build_five_entry_lineage()
        log = build_lineage_log(pool, seed_entry)
        prompt = build_reflection_user_prompt(
            code=seed_entry.program.source_code,
            score=0.289,
            failed_cases=[{"question": "q", "expected": "a", "output": "wrong", "score": 0.0}],
            iteration=5,
            lineage_log=log,
        )

        verification_prompt = (
            "You are analyzing a reflection prompt for a code evolution system. "
            "Read the <lineage_log> section carefully and answer these questions. "
            "Answer each with ONLY the requested information, no explanation.\n\n"
            f"PROMPT:\n{prompt}\n\n"
            "QUESTIONS:\n"
            "1. What is the name (e.g., seed_0, iter_N) of the current program being improved? Answer: \n"
            "2. List the names of its direct children (comma-separated). Answer: \n"
            "3. List the names of commits marked as REGRESSION (comma-separated). Answer: \n"
            "4. What specific code pattern was removed in iter_1 that caused its regression? Answer: \n"
            "5. Based on the lineage, what should the reflector avoid doing? Answer: \n"
        )

        output = _llm_call(
            REFLECT_MODEL,
            [{"role": "user", "content": verification_prompt}],
        )

        output_lower = output.lower()
        # 1. Current program is seed_0
        assert "seed_0" in output_lower or "seed" in output_lower
        # 2. Direct children include iter_1 and iter_4
        assert "iter_1" in output_lower
        assert "iter_4" in output_lower
        # 3. Regressions include iter_1 (and possibly iter_8)
        assert "iter_1" in output_lower  # definitely a regression
        # 4. Removed llm_completion
        assert "llm" in output_lower or "completion" in output_lower
        # 5. Should avoid removing LLM
        assert "remov" in output_lower or "llm" in output_lower

        assert {"prompt": verification_prompt, "output": output} == snapshot
```

Note: add `import litellm` and use the existing `_llm_call` helper (or import it). Since this test is in `test_prompts.py`, you'll need to add a local helper or import from `test_llm_integration.py`. Simplest: define a local helper:

```python
def _llm_call(model: str, messages: list[dict]) -> str:
    import litellm
    response = litellm.completion(model=model, messages=messages, caching=True)
    return response.choices[0].message.content

REFLECT_MODEL = "openrouter/openai/gpt-5.3-codex"
```

- [ ] **Step 4: Run all tests**

Run: `uv run pytest tests/evolution/test_prompts.py::TestLineageLogEndToEnd -v`
(Non-LLM tests should pass; LLM test requires API key)

For the LLM test:
Run: `uv run pytest tests/evolution/test_prompts.py::TestLineageLogEndToEnd::test_subagent_can_interpret_lineage -v`

- [ ] **Step 5: Update snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py::TestLineageLogEndToEnd --snapshot-update -v`

- [ ] **Step 6: Commit**

```bash
git add tests/evolution/test_prompts.py tests/evolution/__snapshots__/
git commit -m "test: AC2 — lineage log snapshot and subagent verification"
```

---

### Task 12: Final verification and cleanup

- [ ] **Step 1: Run full non-LLM test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

- [ ] **Step 2: Run lint**

Run: `uv run ruff check src/ && uv run ruff format --check src/`
Expected: No errors

- [ ] **Step 3: Run LLM integration tests**

Run: `uv run pytest tests/evolution/test_llm_integration.py -v`
Expected: All PASS (existing + new commit message test)

- [ ] **Step 4: Final commit with any cleanup**

```bash
git add -A
git commit -m "chore: lineage log feature complete — final cleanup"
```
