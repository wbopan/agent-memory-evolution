# Runtime Guards Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add timeout and output-length guards to `memory.write()`/`memory.read()` during evaluation, with early-abort and a fix loop.

**Architecture:** Exception-based early abort. `_guarded_write`/`_guarded_read` helpers in evaluator.py wrap every memory call with ThreadPoolExecutor timeout + length check. On violation, `RuntimeViolationError` propagates to `evaluate()` which returns `EvalResult(runtime_violation=...)`. Loop.py detects it, calls `reflector.fix_runtime_violation()`, re-evaluates.

**Tech Stack:** concurrent.futures.ThreadPoolExecutor (already used in sandbox.smoke_test), existing reflector fix infrastructure.

**Design doc:** `docs/plans/2026-03-01-runtime-guards-design.md`

---

### Task 1: Add `runtime_violation` field to EvalResult

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:82-89`

**Step 1: Add the field**

In `types.py`, add `runtime_violation` to `EvalResult`:

```python
@dataclass
class EvalResult:
    """Aggregated evaluation result for a memory program."""

    score: float
    per_case_scores: list[float] = field(default_factory=list)
    per_case_outputs: list[str] = field(default_factory=list)
    failed_cases: list[FailedCase] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    runtime_violation: str | None = None
```

**Step 2: Run existing tests to verify nothing breaks**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS (new field has default=None, no existing code references it)

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/types.py
git commit -m "feat: add runtime_violation field to EvalResult"
```

---

### Task 2: Add `RuntimeViolationError` + guarded helpers with unit tests

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py` (add exception + two helpers at top of file)
- Modify: `tests/evolution/test_evaluator.py` (add test class)

**Step 1: Write failing tests**

Add to `tests/evolution/test_evaluator.py`:

```python
import time

from programmaticmemory.evolution.evaluator import (
    RuntimeViolationError,
    _guarded_read,
    _guarded_write,
)


class TestGuardedWrite:
    def test_normal_write_succeeds(self):
        memory = MagicMock()
        obs = MagicMock()
        _guarded_write(memory, obs)
        memory.write.assert_called_once_with(obs)

    def test_timeout_raises_violation(self):
        memory = MagicMock()
        memory.write.side_effect = lambda obs: time.sleep(10)
        with pytest.raises(RuntimeViolationError, match="timed out"):
            _guarded_write(memory, MagicMock(), timeout=0.1)

    def test_exception_propagates(self):
        memory = MagicMock()
        memory.write.side_effect = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            _guarded_write(memory, MagicMock())


class TestGuardedRead:
    def test_normal_read_succeeds(self):
        memory = MagicMock()
        memory.read.return_value = "short"
        result = _guarded_read(memory, MagicMock())
        assert result == "short"

    def test_timeout_raises_violation(self):
        memory = MagicMock()
        memory.read.side_effect = lambda q: time.sleep(10)
        with pytest.raises(RuntimeViolationError, match="timed out"):
            _guarded_read(memory, MagicMock(), timeout=0.1)

    def test_oversized_output_raises_violation(self):
        memory = MagicMock()
        memory.read.return_value = "x" * 2000
        with pytest.raises(RuntimeViolationError, match="2000 chars"):
            _guarded_read(memory, MagicMock(), max_chars=1000)

    def test_none_return_passes(self):
        memory = MagicMock()
        memory.read.return_value = None
        result = _guarded_read(memory, MagicMock())
        assert result is None

    def test_exception_propagates(self):
        memory = MagicMock()
        memory.read.side_effect = ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            _guarded_read(memory, MagicMock())
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestGuardedWrite -v && uv run pytest tests/evolution/test_evaluator.py::TestGuardedRead -v`
Expected: ImportError (RuntimeViolationError, _guarded_write, _guarded_read don't exist yet)

**Step 3: Implement**

Add to top of `src/programmaticmemory/evolution/evaluator.py` (after existing imports, before class definitions):

```python
import concurrent.futures

MEMORY_OP_TIMEOUT = 5.0
MEMORY_READ_MAX_CHARS = 1000


class RuntimeViolationError(Exception):
    """Raised when memory.write/read violates runtime constraints (timeout or output size)."""


def _guarded_write(memory: Any, obs: Any, timeout: float = MEMORY_OP_TIMEOUT) -> None:
    """Wrap memory.write(obs) with timeout."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(memory.write, obs)
        try:
            future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeViolationError(f"memory.write() timed out after {timeout}s")


def _guarded_read(
    memory: Any, query: Any, timeout: float = MEMORY_OP_TIMEOUT, max_chars: int = MEMORY_READ_MAX_CHARS
) -> Any:
    """Wrap memory.read(query) with timeout + output length check."""
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(memory.read, query)
        try:
            result = future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            raise RuntimeViolationError(f"memory.read() timed out after {timeout}s")
    result_str = str(result) if result is not None else ""
    if len(result_str) > max_chars:
        raise RuntimeViolationError(f"memory.read() returned {len(result_str)} chars (limit: {max_chars})")
    return result
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestGuardedWrite tests/evolution/test_evaluator.py::TestGuardedRead -v`
Expected: All 8 PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py
git commit -m "feat: add RuntimeViolationError + _guarded_write/_guarded_read helpers"
```

---

### Task 3: Replace bare memory calls in evaluator + early abort catch

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py` (7 call sites + 1 try/except in evaluate)
- Modify: `tests/evolution/test_evaluator.py` (add integration test)

**Step 1: Write failing integration test**

Add a test program constant and test class to `tests/evolution/test_evaluator.py`:

```python
OVERSIZED_READ_PROGRAM = textwrap.dedent("""\
    from dataclasses import dataclass

    @dataclass
    class Observation:
        content: str

    @dataclass
    class Query:
        question: str

    class Memory:
        def __init__(self, toolkit):
            pass

        def write(self, obs):
            pass

        def read(self, query):
            return "x" * 5000
""")


class TestRuntimeViolationEarlyAbort:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_oversized_read_aborts_eval(self, mock_litellm):
        """Eval aborts on first memory.read() returning >1000 chars."""
        mock_litellm.completion.side_effect = [
            MagicMock(choices=[MagicMock(message=MagicMock(content='{"content": "hello"}'))]),
            MagicMock(choices=[MagicMock(message=MagicMock(content='{"question": "what?"}'))]),
        ]
        program = MemoryProgram(source_code=OVERSIZED_READ_PROGRAM)
        evaluator = MemoryEvaluator(batch_process=False)
        train = [DataItem(raw_text="hello", question="q", expected_answer="a")]
        val = [DataItem(raw_text="", question="what?", expected_answer="x")]

        result = evaluator.evaluate(program, train, val, EvalMode.OFFLINE)

        assert result.score == 0.0
        assert result.runtime_violation is not None
        assert "5000" in result.runtime_violation
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestRuntimeViolationEarlyAbort -v`
Expected: FAIL (no early abort, eval completes normally with oversized read silently passed through)

**Step 3: Replace 7 call sites + add early abort catch**

In `evaluator.py`, make these changes:

**3a. `evaluate()` — wrap with try/except (around lines 176-183):**

Replace:
```python
        try:
            if eval_mode == EvalMode.OFFLINE:
                return self._evaluate_offline(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
            else:
                return self._evaluate_online(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
        finally:
            toolkit.close()
```

With:
```python
        try:
            if eval_mode == EvalMode.OFFLINE:
                return self._evaluate_offline(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
            else:
                return self._evaluate_online(
                    memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit
                )
        except RuntimeViolationError as e:
            self.logger.log(f"Runtime violation: {e}", header="EVAL")
            return EvalResult(score=0.0, logs=[f"Runtime violation: {e}"], runtime_violation=str(e))
        finally:
            toolkit.close()
```

**3b. `_evaluate_offline` batch path (line 218) — `memory.write(obs)`:**

Replace:
```python
                try:
                    obs = obs_cls(**_parse_json_from_llm(content))
                    memory.write(obs)
                except Exception as e:
                    logs.append(f"Obs parse/write failed: {e}")
```

With:
```python
                try:
                    obs = obs_cls(**_parse_json_from_llm(content))
                    _guarded_write(memory, obs)
                except RuntimeViolationError:
                    raise
                except Exception as e:
                    logs.append(f"Obs parse/write failed: {e}")
```

**3c. `_evaluate_offline` sequential path (line 228) — `memory.write(obs)`:**

Replace:
```python
                try:
                    memory.write(obs)
                except Exception as e:
                    logs.append(f"Write failed: {e}")
```

With:
```python
                try:
                    _guarded_write(memory, obs)
                except RuntimeViolationError:
                    raise
                except Exception as e:
                    logs.append(f"Write failed: {e}")
```

**3d. `_online_train_sequential` (line 301) — `memory.read(query)`:**

Replace:
```python
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Train read failed: {e}")
```

With:
```python
            try:
                retrieved = _guarded_read(memory, query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except RuntimeViolationError:
                raise
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Train read failed: {e}")
```

**3e. `_online_train_sequential` (line 350) — `memory.write(obs)`:**

Replace:
```python
            try:
                memory.write(obs)
            except Exception as e:
                logs.append(f"Train write failed: {e}")
```

With:
```python
            try:
                _guarded_write(memory, obs)
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Train write failed: {e}")
```

**3f. `_online_train_batched` (line 422) — `memory.write(obs)` in serial writes:**

Replace:
```python
            try:
                obs = obs_cls(**_parse_json_from_llm(obs_content))
                memory.write(obs)
            except Exception as e:
                logs.append(f"Train observation parse/write failed: {e}")
```

With:
```python
            try:
                obs = obs_cls(**_parse_json_from_llm(obs_content))
                _guarded_write(memory, obs)
            except RuntimeViolationError:
                raise
            except Exception as e:
                logs.append(f"Train observation parse/write failed: {e}")
```

**3g. `_parse_queries_and_read` (line 453) — `memory.read(query)`:**

Replace:
```python
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"{log_prefix} read failed: {e}")
```

With:
```python
            try:
                retrieved = _guarded_read(memory, query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except RuntimeViolationError:
                raise
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"{log_prefix} read failed: {e}")
```

**3h. `_evaluate_val_sequential` (line 564) — `memory.read(query)`:**

Replace:
```python
            try:
                retrieved = memory.read(query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Val read failed: {e}")
```

With:
```python
            try:
                retrieved = _guarded_read(memory, query)
                retrieved_str = str(retrieved) if retrieved is not None else ""
            except RuntimeViolationError:
                raise
            except Exception as e:
                retrieved_str = f"Read error: {e}"
                logs.append(f"Val read failed: {e}")
```

**Step 4: Run all evaluator tests**

Run: `uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v`
Expected: All PASS (existing tests unaffected since their programs don't trigger violations; new integration test passes)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py
git commit -m "feat: add runtime guards to all memory.write/read calls in evaluator"
```

---

### Task 4: Add `Reflector.fix_runtime_violation` method with tests

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py` (add public method)
- Modify: `tests/evolution/test_reflector.py` (add test class)

**Step 1: Write failing tests**

Add to `tests/evolution/test_reflector.py`:

```python
class TestReflectorRuntimeFix:
    """Tests for Reflector.fix_runtime_violation."""

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_succeeds(self, mock_litellm, mock_compile, mock_smoke):
        """LLM returns valid fix → compile+smoke pass → return fixed code."""
        fixed_code = "class Observation:\n  pass\nclass Query:\n  pass\nclass Memory:\n  pass"
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=f"```python\n{fixed_code}\n```"))]
        )
        mock_compile.return_value = (MagicMock(), MagicMock(), MagicMock())
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector()
        result = reflector.fix_runtime_violation("old code", "memory.read() returned 5000 chars (limit: 1000)")

        assert result == fixed_code
        # Verify _try_fix was called with runtime violation context
        call_args = mock_litellm.completion.call_args
        prompt = call_args[1]["messages"][0]["content"] if "messages" in call_args[1] else call_args[0][0][0]["content"]
        assert "Runtime violation" in prompt

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_no_code_block_returns_none(self, mock_litellm):
        """LLM returns no code block → return None."""
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="I don't know how to fix this."))]
        )
        reflector = Reflector()
        result = reflector.fix_runtime_violation("old code", "memory.read() timed out after 5.0s")

        assert result is None

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_with_compile_error_enters_compile_fix_loop(self, mock_litellm, mock_compile, mock_smoke):
        """First fix has compile error → compile-fix loop fixes it."""
        first_fix = "bad code"
        second_fix = "good code"
        mock_litellm.completion.side_effect = [
            # First call: fix_runtime_violation's _try_fix → returns code with compile error
            MagicMock(choices=[MagicMock(message=MagicMock(content=f"```python\n{first_fix}\n```"))]),
            # Second call: compile-fix loop's _try_fix → returns valid code
            MagicMock(choices=[MagicMock(message=MagicMock(content=f"```python\n{second_fix}\n```"))]),
        ]
        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="invalid syntax"),  # first_fix fails
            (MagicMock(), MagicMock(), MagicMock()),  # second_fix compiles
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector()
        result = reflector.fix_runtime_violation("old code", "memory.read() timed out")

        assert result == second_fix
        assert mock_litellm.completion.call_count == 2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_reflector.py::TestReflectorRuntimeFix -v`
Expected: AttributeError (fix_runtime_violation doesn't exist)

**Step 3: Implement**

Add to `src/programmaticmemory/evolution/reflector.py`, in the `Reflector` class after `reflect_and_mutate`:

```python
    def fix_runtime_violation(self, code: str, violation: str) -> str | None:
        """Fix a runtime violation. Returns validated (compile+smoke) code or None.

        Calls LLM to fix the violation, then validates. If the fix introduces
        compile/smoke errors, enters the compile-fix loop.
        """
        self.logger.log(f"Fixing runtime violation: {violation}", header="REFLECT")

        fixed = self._try_fix(code, "Runtime violation", violation)
        if fixed is None:
            self.logger.log("Runtime fix: no code block in LLM response", header="REFLECT")
            return None

        validation_error = self._validate_code(fixed)
        if validation_error is None:
            return fixed

        # Compile-fix loop for the fixed code
        for attempt in range(1, self.max_fix_attempts + 1):
            error_type, error_details = validation_error
            self.logger.log(
                f"Runtime fix compile-fix attempt {attempt}/{self.max_fix_attempts}: {error_details}",
                header="REFLECT",
            )
            fixed = self._try_fix(fixed, error_type, error_details)
            if fixed is None:
                continue
            validation_error = self._validate_code(fixed)
            if validation_error is None:
                return fixed

        self.logger.log("Runtime fix: all compile-fix attempts exhausted", header="REFLECT")
        return None
```

**Step 4: Run tests**

Run: `uv run pytest tests/evolution/test_reflector.py -m "not llm" -v`
Expected: All PASS (new + existing)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py tests/evolution/test_reflector.py
git commit -m "feat: add Reflector.fix_runtime_violation for fixing runtime constraint violations"
```

---

### Task 5: Add runtime violation fix loop in `loop.py` with tests

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py` (add fix loop after child evaluation)
- Modify: `tests/evolution/test_loop.py` (add test class)

**Step 1: Write failing tests**

Add to `tests/evolution/test_loop.py`:

```python
class TestEvolutionLoopRuntimeFix:
    """Tests for runtime violation fix loop in EvolutionLoop."""

    def test_runtime_violation_triggers_fix_and_reeval(self):
        """Runtime violation → fix_runtime_violation called → re-eval succeeds."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial eval
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),  # child eval
            EvalResult(score=0.8),  # re-eval after fix
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = "fixed code"
        reflector.max_fix_attempts = 3

        dataset = Dataset(
            train=[DataItem(raw_text="t", question="q", expected_answer="a")],
            val=[DataItem(raw_text="v", question="q", expected_answer="a")],
            test=[],
            eval_mode=EvalMode.OFFLINE,
        )

        loop = EvolutionLoop(evaluator=evaluator, reflector=reflector, dataset=dataset, initial_program=initial, max_iterations=1)
        state = loop.run()

        # fix_runtime_violation was called with the violation message
        reflector.fix_runtime_violation.assert_called_once_with(
            "child", "memory.read() returned 5000 chars (limit: 1000)"
        )
        # evaluator called 3 times: initial, child (violation), fixed child
        assert evaluator.evaluate.call_count == 3
        # Fixed child was accepted (0.8 > 0.5)
        assert state.best_score == 0.8

    def test_runtime_violation_fix_returns_none(self):
        """Runtime violation → fix returns None → iteration rejected."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial
            EvalResult(score=0.0, runtime_violation="memory.read() timed out after 5.0s"),  # child
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.return_value = None
        reflector.max_fix_attempts = 3

        dataset = Dataset(
            train=[DataItem(raw_text="t", question="q", expected_answer="a")],
            val=[DataItem(raw_text="v", question="q", expected_answer="a")],
            test=[],
            eval_mode=EvalMode.OFFLINE,
        )

        loop = EvolutionLoop(evaluator=evaluator, reflector=reflector, dataset=dataset, initial_program=initial, max_iterations=1)
        state = loop.run()

        # Fix failed, best stays at initial
        assert state.best_score == 0.5
        assert evaluator.evaluate.call_count == 2

    def test_runtime_violation_fix_loop_retries(self):
        """First fix still violates → loop retries → second fix succeeds."""
        initial = MemoryProgram(source_code="initial")
        child = MemoryProgram(source_code="child", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5),  # initial
            EvalResult(score=0.0, runtime_violation="memory.read() returned 5000 chars (limit: 1000)"),  # child
            EvalResult(score=0.0, runtime_violation="memory.read() returned 3000 chars (limit: 1000)"),  # first fix still violates
            EvalResult(score=0.7),  # second fix works
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child
        reflector.fix_runtime_violation.side_effect = ["fix1", "fix2"]
        reflector.max_fix_attempts = 3

        dataset = Dataset(
            train=[DataItem(raw_text="t", question="q", expected_answer="a")],
            val=[DataItem(raw_text="v", question="q", expected_answer="a")],
            test=[],
            eval_mode=EvalMode.OFFLINE,
        )

        loop = EvolutionLoop(evaluator=evaluator, reflector=reflector, dataset=dataset, initial_program=initial, max_iterations=1)
        state = loop.run()

        assert reflector.fix_runtime_violation.call_count == 2
        assert evaluator.evaluate.call_count == 4
        assert state.best_score == 0.7
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_loop.py::TestEvolutionLoopRuntimeFix -v`
Expected: FAIL (no runtime fix loop, violations ignored)

**Step 3: Implement**

In `src/programmaticmemory/evolution/loop.py`, add the fix loop after child evaluation (after line 121, before line 122).

Replace:
```python
            child_result = self.evaluator.evaluate(child, ds.train, ds.val, ds.eval_mode)
            child_score = child_result.score
```

With:
```python
            child_result = self.evaluator.evaluate(child, ds.train, ds.val, ds.eval_mode)

            # Runtime violation fix loop
            for _fix_attempt in range(self.reflector.max_fix_attempts):
                if not child_result.runtime_violation:
                    break
                self.logger.log(
                    f"Runtime violation: {child_result.runtime_violation}, attempting fix",
                    header="EVOLUTION",
                )
                fixed_code = self.reflector.fix_runtime_violation(
                    child.source_code, child_result.runtime_violation
                )
                if fixed_code is None:
                    self.logger.log("Runtime fix failed, giving up", header="EVOLUTION")
                    break
                child = MemoryProgram(
                    source_code=fixed_code,
                    generation=current.generation + 1,
                    parent_hash=current.hash,
                )
                child_result = self.evaluator.evaluate(child, ds.train, ds.val, ds.eval_mode)

            child_score = child_result.score
```

**Step 4: Run all tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_loop.py
git commit -m "feat: add runtime violation fix loop to evolution loop"
```
