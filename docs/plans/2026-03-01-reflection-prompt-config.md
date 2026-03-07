# Reflection Prompt Config Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `ReflectionPromptConfig` dataclass with 3 flags (`max_failed_cases`, `max_train_examples`, `max_memory_log_chars`) that control the reflection prompt content, exposed as `--reflection-*` CLI args.

**Architecture:** `ReflectionPromptConfig` lives in `prompts.py`. `build_reflection_user_prompt` accepts it and uses it to limit/truncate/deduplicate content. `Reflector` holds the config and passes it through. CLI adds 3 `--reflection-*` args.

**Tech Stack:** Python dataclasses, argparse, pytest + syrupy snapshots

---

### Task 1: Add `ReflectionPromptConfig` dataclass and update `build_reflection_user_prompt`

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py`
- Test: `tests/evolution/test_prompts.py`

**Step 1: Write the failing tests**

Add new test class to `tests/evolution/test_prompts.py`:

```python
from programmaticmemory.evolution.prompts import ReflectionPromptConfig


class TestReflectionPromptConfig:
    def test_max_failed_cases(self, snapshot: SnapshotAssertion):
        cases = [{"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0} for i in range(10)]
        config = ReflectionPromptConfig(max_failed_cases=2)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        assert "q0" in prompt
        assert "q1" in prompt
        assert "q2" not in prompt
        assert prompt == snapshot

    def test_max_train_examples(self, snapshot: SnapshotAssertion):
        examples = [
            TrainExample(messages=[{"role": "user", "content": f"example {i}"}])
            for i in range(10)
        ]
        config = ReflectionPromptConfig(max_train_examples=2)
        prompt = build_reflection_user_prompt(
            code="x", score=0.0, failed_cases=[], iteration=1,
            train_examples=examples, config=config,
        )
        assert "example 0" in prompt
        assert "example 1" in prompt
        assert "example 2" not in prompt
        assert prompt == snapshot

    def test_max_memory_log_chars_truncates(self):
        logs = [f"Stored: {'x' * 500}" for _ in range(10)]
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0, "memory_logs": logs}]
        config = ReflectionPromptConfig(max_memory_log_chars=200)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        # Find the memory logs section and check it's truncated
        logs_section = prompt[prompt.find("Memory logs:"):]
        assert len(logs_section) < 500  # well under what 10 * 500 chars would be

    def test_max_memory_log_chars_zero_excludes(self, snapshot: SnapshotAssertion):
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0, "memory_logs": ["log1", "log2"]}]
        config = ReflectionPromptConfig(max_memory_log_chars=0)
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        assert "Memory logs:" not in prompt
        assert "log1" not in prompt
        assert prompt == snapshot

    def test_memory_logs_deduplicated(self):
        """When all cases have identical logs, show logs once, not per case."""
        shared_logs = ["Stored: fact1", "Stored: fact2", "Query: q1"]
        cases = [
            {"question": f"q{i}", "expected": "a", "output": "o", "score": 0.0, "memory_logs": shared_logs}
            for i in range(3)
        ]
        config = ReflectionPromptConfig()
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1, config=config)
        # Logs should appear exactly once (deduplicated)
        assert prompt.count("Stored: fact1") == 1
        assert prompt.count("Stored: fact2") == 1

    def test_default_config(self, snapshot: SnapshotAssertion):
        """Default config produces same output as no config for basic cases."""
        cases = [{"question": "q", "expected": "a", "output": "o", "score": 0.0}]
        prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
        prompt_with_config = build_reflection_user_prompt(
            code="x", score=0.0, failed_cases=cases, iteration=1, config=ReflectionPromptConfig(),
        )
        assert prompt == prompt_with_config
```

Also update the existing `test_includes_all_cases` test — it currently asserts all 10 cases appear. With the default `max_failed_cases=5`, only 5 will appear. Update the assertion:

```python
def test_includes_all_cases(self, snapshot: SnapshotAssertion):
    cases = [{"question": f"q{i}", "expected": f"a{i}", "output": "wrong", "score": 0.0} for i in range(10)]
    prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
    # Default config limits to 5 cases
    assert "q4" in prompt
    assert "q5" not in prompt
    assert prompt == snapshot
```

And update `test_many_memory_logs_not_truncated` — with default `max_memory_log_chars=2000`, 20 short logs will still fit:

```python
def test_many_memory_logs_within_budget(self):
    logs = [f"log entry {i}" for i in range(20)]
    cases = [
        {"question": "q", "expected": "a", "output": "o", "score": 0.0, "memory_logs": logs}
    ]
    prompt = build_reflection_user_prompt(code="x", score=0.0, failed_cases=cases, iteration=1)
    assert "log entry 0" in prompt
    assert "log entry 19" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_prompts.py -v -x`
Expected: FAIL — `ReflectionPromptConfig` not importable, `config` param not accepted

**Step 3: Implement `ReflectionPromptConfig` and update `build_reflection_user_prompt`**

In `src/programmaticmemory/evolution/prompts.py`:

1. Add dataclass after imports:

```python
@dataclass
class ReflectionPromptConfig:
    """Controls what content is included in the reflection prompt."""
    max_failed_cases: int = 5
    max_train_examples: int = 5
    max_memory_log_chars: int = 2000  # 0 = exclude memory logs entirely
```

2. Add a helper to truncate memory logs (head+tail with elision):

```python
def _truncate_memory_logs(logs: list[str], max_chars: int) -> str:
    """Render memory logs with a character budget, keeping head and tail."""
    if max_chars <= 0:
        return ""
    full = "".join(f"  - {log}\n" for log in logs)
    if len(full) <= max_chars:
        return full
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(full) - max_chars
    return full[:head] + f"\n  ... [{omitted} chars omitted] ...\n" + full[-tail:]
```

3. Update `build_reflection_user_prompt` signature to accept `config: ReflectionPromptConfig | None = None`, default to `ReflectionPromptConfig()` if None.

4. Apply `config.max_failed_cases` — slice `failed_cases[:config.max_failed_cases]`.

5. Apply `config.max_train_examples` — slice `train_examples[:config.max_train_examples]`.

6. Memory logs deduplication + truncation:
   - Check if all cases share identical `memory_logs`
   - If yes: render once as a standalone section before failed cases, apply `_truncate_memory_logs` with `config.max_memory_log_chars`, don't render logs inside individual cases
   - If no: render per-case as before, apply `_truncate_memory_logs` per case with `config.max_memory_log_chars`

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_prompts.py -v`
Expected: Some tests FAIL because snapshots need updating

**Step 5: Update snapshots**

Run: `uv run pytest tests/evolution/test_prompts.py -m "not llm" --snapshot-update -v`
Expected: Snapshots updated, all non-LLM tests pass

**Step 6: Lint**

Run: `uv run ruff check src/programmaticmemory/evolution/prompts.py && uv run ruff format src/programmaticmemory/evolution/prompts.py`

**Step 7: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py tests/evolution/test_prompts.py tests/evolution/__snapshots__/test_prompts.ambr
git commit -m "feat: add ReflectionPromptConfig to control reflection prompt content"
```

---

### Task 2: Thread config through `Reflector`

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py`
- Test: `tests/evolution/test_reflector.py`

**Step 1: Write the failing test**

Add test to `tests/evolution/test_reflector.py` that checks config is passed through. Find the existing test pattern (likely mock-based) and add:

```python
def test_reflect_uses_prompt_config(self, ...):
    config = ReflectionPromptConfig(max_failed_cases=2)
    reflector = Reflector(model=MODEL, temperature=0.0, prompt_config=config)
    # ... call reflect_and_mutate with 5 failed cases ...
    # ... assert the LLM call only contains 2 cases ...
```

Exact test shape depends on existing test patterns in `test_reflector.py` — read the file first during implementation.

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_reflector.py -v -x -k "prompt_config"`
Expected: FAIL — `prompt_config` not accepted by `Reflector`

**Step 3: Implement**

In `src/programmaticmemory/evolution/reflector.py`:

1. Import `ReflectionPromptConfig` from `prompts.py`
2. Add `prompt_config: ReflectionPromptConfig | None = None` to `Reflector.__init__`, store as `self.prompt_config`
3. In `reflect_and_mutate`, pass config to `build_reflection_user_prompt`:

```python
user_prompt = build_reflection_user_prompt(
    code=current.source_code,
    score=eval_result.score,
    failed_cases=failed_dicts,
    iteration=iteration,
    train_examples=eval_result.train_examples or None,
    config=self.prompt_config,
)
```

4. Remove the `[:5]` slice on `eval_result.failed_cases` in `reflect_and_mutate` (line 85) — config now controls this inside `build_reflection_user_prompt`.

**Step 4: Run tests**

Run: `uv run pytest tests/evolution/test_reflector.py -v`
Expected: PASS (existing tests still work with default config)

**Step 5: Update snapshots if needed**

Run: `uv run pytest tests/evolution/test_reflector.py -m "not llm" --snapshot-update -v`

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py tests/evolution/test_reflector.py tests/evolution/__snapshots__/test_reflector.ambr
git commit -m "feat: thread ReflectionPromptConfig through Reflector"
```

---

### Task 3: Add `--reflection-*` CLI args

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py`

**Step 1: Add CLI arguments**

After the existing `--weave-project` arg (line 57), add:

```python
parser.add_argument("--reflection-max-failed-cases", type=int, default=5,
                    help="Max failed cases in reflection prompt (default: 5)")
parser.add_argument("--reflection-max-train-examples", type=int, default=5,
                    help="Max training examples in reflection prompt (default: 5)")
parser.add_argument("--reflection-max-memory-log-chars", type=int, default=2000,
                    help="Max chars for memory logs in reflection prompt, 0 to exclude (default: 2000)")
```

**Step 2: Wire to Reflector**

Replace line 79 (`reflector = Reflector(model=args.reflect_model)`) with:

```python
from programmaticmemory.evolution.prompts import ReflectionPromptConfig

prompt_config = ReflectionPromptConfig(
    max_failed_cases=args.reflection_max_failed_cases,
    max_train_examples=args.reflection_max_train_examples,
    max_memory_log_chars=args.reflection_max_memory_log_chars,
)
reflector = Reflector(model=args.reflect_model, prompt_config=prompt_config)
```

**Step 3: Verify CLI help**

Run: `uv run python -m programmaticmemory.evolution --help`
Expected: Three `--reflection-*` args visible in help output

**Step 4: Lint**

Run: `uv run ruff check src/programmaticmemory/evolution/__main__.py && uv run ruff format src/programmaticmemory/evolution/__main__.py`

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: add --reflection-* CLI args for prompt content control"
```

---

### Task 4: Run full test suite and verify

**Step 1: Run all non-LLM tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All pass

**Step 2: Run lint on all changed files**

Run: `uv run ruff check src/ && uv run ruff format --check src/`
Expected: Clean

**Step 3: Smoke test CLI**

Run: `uv run python -m programmaticmemory.evolution --help`
Expected: All `--reflection-*` flags visible
