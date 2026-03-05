# KnowledgeItem Summary Redesign — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace raw-text-copying KnowledgeItem with a summary field, and pass raw source text as a separate `write()` parameter.

**Architecture:** Change `write(item)` → `write(item, raw_text)` across the full pipeline (prompts → sandbox → evaluator → tests). The default program stores summaries and raw texts in separate lists; `read()` returns 500 chars from each.

**Tech Stack:** Python, pytest, syrupy snapshots

---

### Task 1: Update INITIAL_KB_PROGRAM and KB_INTERFACE_SPEC

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py:20-104`

**Step 1: Edit KB_INTERFACE_SPEC write() line**

In `prompts.py:41`, change:
```python
   - `write(self, item: KnowledgeItem) -> None`: Store information
```
to:
```python
   - `write(self, item: KnowledgeItem, raw_text: str) -> None`: Store information. `raw_text` is the original source text that produced the knowledge item.
```

**Step 2: Edit INITIAL_KB_PROGRAM**

In `prompts.py:66-104`, change:
- `INSTRUCTION_KNOWLEDGE_ITEM` → `"Summarize the key information from the text."`
- `KnowledgeItem.raw` → `KnowledgeItem.summary` with description `"What you have learnt from the text"` and docstring `"A summary of what was learnt from the source text."`
- `KnowledgeBase.write(self, item, raw_text)` — stores `item.summary` in `self.summaries` and `raw_text` in `self.observations`
- `KnowledgeBase.read()` — returns first 500 chars of joined summaries + first 500 chars of joined observations

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py
git commit -m "refactor: change KnowledgeItem.raw to summary, add raw_text param to write()"
```

---

### Task 2: Update sandbox smoke_test

**Files:**
- Modify: `src/programmaticmemory/evolution/sandbox.py:279`

**Step 1: Edit smoke_test write call**

At `sandbox.py:279`, change:
```python
            kb.write(item)
```
to:
```python
            kb.write(item, "smoke test raw text")
```

**Step 2: Commit**

```bash
git add src/programmaticmemory/evolution/sandbox.py
git commit -m "fix: pass raw_text to kb.write() in smoke_test"
```

---

### Task 3: Update evaluator _guarded_write and call sites

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:49-56,308,461-471`

**Step 1: Add raw_text parameter to _guarded_write**

At `evaluator.py:49`, change:
```python
def _guarded_write(kb: Any, item: Any, timeout: float = MEMORY_OP_TIMEOUT) -> None:
    """Wrap kb.write(item) with timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kb.write, item)
```
to:
```python
def _guarded_write(kb: Any, item: Any, raw_text: str = "", timeout: float = MEMORY_OP_TIMEOUT) -> None:
    """Wrap kb.write(item, raw_text) with timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(kb.write, item, raw_text)
```

**Step 2: Pass raw_text at offline batch call site**

At `evaluator.py:308`, change:
```python
                _guarded_write(kb, ki)
```
to:
```python
                _guarded_write(kb, ki, raw_text=item.raw_text)
```

**Step 3: Pass raw_text at interactive training call site**

At `evaluator.py:461-471`, the write loop needs access to the `DataItem`. Change the zip to include `round3_items`:
```python
        for (item, _msgs, _eval), r3_msgs, ki_content in zip(
            round3_items, round3_messages, round3_responses, strict=True
        ):
```
Then change:
```python
                _guarded_write(kb, ki)
```
to:
```python
                _guarded_write(kb, ki, raw_text=item.raw_text)
```

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py
git commit -m "feat: pass raw_text through _guarded_write to kb.write()"
```

---

### Task 4: Update unit tests

**Files:**
- Modify: `tests/evolution/test_evaluator.py:809-826`

**Step 1: Update _guarded_write tests**

```python
class TestGuardedWrite:
    def test_normal_write_succeeds(self):
        memory = MagicMock()
        item = MagicMock()
        _guarded_write(memory, item, raw_text="test text")
        memory.write.assert_called_once_with(item, "test text")

    def test_timeout_raises_violation(self):
        memory = MagicMock()
        memory.write.side_effect = lambda item, raw_text: time.sleep(10)
        with pytest.raises(RuntimeViolationError, match="timed out"):
            _guarded_write(memory, MagicMock(), raw_text="x", timeout=0.1)

    def test_exception_propagates(self):
        memory = MagicMock()
        memory.write.side_effect = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            _guarded_write(memory, MagicMock())
```

**Step 2: Run tests**

```bash
uv run pytest tests/evolution/test_evaluator.py::TestGuardedWrite -v
```

**Step 3: Commit**

```bash
git add tests/evolution/test_evaluator.py
git commit -m "test: update _guarded_write tests for raw_text parameter"
```

---

### Task 5: Update snapshots

**Step 1: Update prompt and evaluator snapshots**

```bash
uv run pytest tests/evolution/test_prompts.py -m "not llm" --snapshot-update -v
uv run pytest tests/evolution/test_evaluator.py -m "not llm" --snapshot-update -v
uv run pytest tests/evolution/test_reflector.py -m "not llm" --snapshot-update -v
```

**Step 2: Update LLM cache and integration snapshots (requires API key)**

```bash
uv run pytest tests/evolution/test_llm_integration.py --snapshot-update -v
```

**Step 3: Run full non-LLM test suite to verify**

```bash
uv run pytest tests/evolution/ -m "not llm" -v
```

**Step 4: Commit**

```bash
git add tests/evolution/
git commit -m "chore: update snapshots and LLM cache for KnowledgeItem summary redesign"
```
