# Compile-Fix Loop Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a compile-fix loop inside Reflector so that when generated Memory Programs fail to compile or smoke-test, the LLM is asked to fix the error (up to 3 times) before giving up.

**Architecture:** Reflector.reflect_and_mutate validates generated code internally via compile_memory_program + smoke_test. On failure, a dedicated fix prompt (code + error, no reflection context) is sent to the LLM. Loop.py removes its own smoke_test call since Reflector now guarantees returned programs are valid.

**Tech Stack:** Python, litellm, existing sandbox.py compile/smoke_test infrastructure

---

### Task 1: Add fix prompt templates to prompts.py

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py`
- Test: `tests/evolution/test_prompts.py`

**Step 1: Write the failing tests**

Add to `tests/evolution/test_prompts.py`:

```python
from programmaticmemory.evolution.prompts import (
    COMPILE_FIX_SYSTEM_PROMPT,
    build_compile_fix_prompt,
)


class TestCompileFixSystemPrompt:
    def test_contains_interface_spec_placeholder(self):
        assert "{interface_spec}" in COMPILE_FIX_SYSTEM_PROMPT

    def test_format_works(self):
        formatted = COMPILE_FIX_SYSTEM_PROMPT.format(interface_spec="spec here")
        assert "spec here" in formatted
        assert "{interface_spec}" not in formatted

    def test_instructs_fix(self):
        formatted = COMPILE_FIX_SYSTEM_PROMPT.format(interface_spec="spec")
        assert "fix" in formatted.lower() or "correct" in formatted.lower()


class TestBuildCompileFixPrompt:
    def test_includes_code_and_error(self):
        prompt = build_compile_fix_prompt(
            code="class Memory: pass",
            error_type="Syntax error",
            error_details="unexpected indent at line 5",
        )
        assert "class Memory: pass" in prompt
        assert "Syntax error" in prompt
        assert "unexpected indent at line 5" in prompt

    def test_includes_error_type_label(self):
        prompt = build_compile_fix_prompt(
            code="x",
            error_type="Import whitelist violation",
            error_details="Disallowed import: numpy",
        )
        assert "Import whitelist violation" in prompt
        assert "Disallowed import: numpy" in prompt
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_prompts.py::TestCompileFixSystemPrompt tests/evolution/test_prompts.py::TestBuildCompileFixPrompt -v`
Expected: FAIL with ImportError (names don't exist yet)

**Step 3: Write minimal implementation**

Add to the end of `src/programmaticmemory/evolution/prompts.py`:

```python
COMPILE_FIX_SYSTEM_PROMPT = """\
You are an expert Python programmer. A Memory Program failed to compile or run.
Fix the error and output the complete corrected code in a ```python``` block.

{interface_spec}

Rules:
1. Output ONLY the corrected code in a ```python``` block. No explanation needed.
2. The code must define exactly three classes: Observation, Query, Memory.
3. Only use allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb.
4. Make minimal changes — fix only what's broken.
"""


def build_compile_fix_prompt(code: str, error_type: str, error_details: str) -> str:
    """Build user prompt for fixing a compile/runtime error."""
    return f"""\
## Broken Code

```python
{code}
```

## Error

**{error_type}**: {error_details}

Fix the error and output the complete corrected code in a ```python``` block."""
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_prompts.py::TestCompileFixSystemPrompt tests/evolution/test_prompts.py::TestBuildCompileFixPrompt -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/prompts.py tests/evolution/test_prompts.py
git commit -m "feat: add compile-fix prompt templates for Reflector fix loop"
```

---

### Task 2: Add compile-fix loop to Reflector

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py`
- Test: `tests/evolution/test_reflector.py`

**Step 1: Write the failing tests**

Add to `tests/evolution/test_reflector.py`:

```python
from programmaticmemory.evolution.sandbox import CompileError, SmokeTestResult


class TestReflectorCompileFixLoop:
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_valid_code_returns_immediately(self, mock_litellm, mock_compile, mock_smoke):
        """When code compiles and passes smoke test, return without fix attempts."""
        new_code = "from dataclasses import dataclass\n\n@dataclass\nclass Observation:\n    raw: str\n\n@dataclass\nclass Query:\n    raw: str\n\nclass Memory:\n    def __init__(self, toolkit): pass\n    def write(self, obs): pass\n    def read(self, query): return ''"
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = f"Analysis.\n\n```python\n{new_code}\n```"
        mock_litellm.completion.return_value = mock_resp

        mock_compile.return_value = (MagicMock(), MagicMock(), MagicMock())
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 1  # Only the reflection call, no fix calls

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_compile_error_triggers_fix_and_succeeds(self, mock_litellm, mock_compile, mock_smoke):
        """CompileError triggers fix loop; fixed code is returned."""
        good_code = "class Observation: pass\nclass Query: pass\nclass Memory: pass"

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = "```python\nbad code\n```"

        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[0].message.content = f"```python\n{good_code}\n```"

        mock_litellm.completion.side_effect = [reflection_resp, fix_resp]

        # First compile fails, second succeeds
        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="invalid syntax"),
            (MagicMock(), MagicMock(), MagicMock()),
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2  # reflection + 1 fix

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_smoke_test_failure_triggers_fix(self, mock_litellm, mock_compile, mock_smoke):
        """Smoke test failure triggers fix loop."""
        good_code = "class Observation: pass\nclass Query: pass\nclass Memory: pass"

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = "```python\ncode v1\n```"

        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[0].message.content = f"```python\n{good_code}\n```"

        mock_litellm.completion.side_effect = [reflection_resp, fix_resp]

        # Both compile fine
        mock_compile.return_value = (MagicMock(), MagicMock(), MagicMock())
        # First smoke fails, second succeeds
        mock_smoke.side_effect = [
            SmokeTestResult(success=False, error="Runtime: KeyError"),
            SmokeTestResult(success=True),
        ]

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_max_fix_attempts_exhausted_returns_none(self, mock_litellm, mock_compile, mock_smoke):
        """After max_fix_attempts, return None."""
        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = "```python\nbad\n```"
        mock_litellm.completion.return_value = bad_resp

        mock_compile.return_value = CompileError(message="Syntax error", details="bad")

        reflector = Reflector(model="mock/model", max_fix_attempts=3)
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is None
        # 1 reflection + 3 fix attempts = 4
        assert mock_litellm.completion.call_count == 4

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_code_extraction_failure_counts_as_attempt(self, mock_litellm, mock_compile, mock_smoke):
        """If fix LLM returns no code block, it still counts as an attempt."""
        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = "```python\nbad\n```"

        no_code_resp = MagicMock()
        no_code_resp.choices = [MagicMock()]
        no_code_resp.choices[0].message.content = "I cannot fix this."

        mock_litellm.completion.side_effect = [reflection_resp, no_code_resp, no_code_resp, no_code_resp]
        mock_compile.return_value = CompileError(message="Syntax error", details="bad")

        reflector = Reflector(model="mock/model", max_fix_attempts=3)
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is None
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_reflector.py::TestReflectorCompileFixLoop -v`
Expected: FAIL (new tests reference `compile_memory_program`/`smoke_test` imports in reflector that don't exist yet)

**Step 3: Write minimal implementation**

Replace `src/programmaticmemory/evolution/reflector.py`:

```python
"""Reflector — LLM-driven reflection and code mutation for Memory Programs."""

from __future__ import annotations

import re

import litellm

from programmaticmemory.evolution.prompts import (
    COMPILE_FIX_SYSTEM_PROMPT,
    MEMORY_INTERFACE_SPEC,
    REFLECTION_SYSTEM_PROMPT,
    build_compile_fix_prompt,
    build_reflection_user_prompt,
)
from programmaticmemory.evolution.sandbox import CompileError, compile_memory_program, smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import EvalResult, MemoryProgram
from programmaticmemory.logging.logger import get_logger


def _extract_code_block(text: str) -> str | None:
    """Extract the last Python code block from LLM output."""
    matches = re.findall(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    return None


class Reflector:
    """Reflects on evaluation results and mutates Memory Programs."""

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        temperature: float = 0.7,
        max_fix_attempts: int = 3,
        toolkit_config: ToolkitConfig | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_fix_attempts = max_fix_attempts
        self.toolkit_config = toolkit_config
        self.logger = get_logger()

    def _validate_code(self, code: str) -> str | None:
        """Compile and smoke-test code. Return error string or None if valid."""
        result = compile_memory_program(code)
        if isinstance(result, CompileError):
            return f"{result.message}: {result.details}"

        st = smoke_test(code, self.toolkit_config)
        if not st.success:
            return st.error

        return None

    def _try_fix(self, code: str, error: str) -> str | None:
        """Ask LLM to fix broken code. Return fixed code or None."""
        system_prompt = COMPILE_FIX_SYSTEM_PROMPT.format(interface_spec=MEMORY_INTERFACE_SPEC)
        user_prompt = build_compile_fix_prompt(code=code, error_type="Error", error_details=error)

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
        )
        output = response.choices[0].message.content
        return _extract_code_block(output)

    def reflect_and_mutate(
        self,
        current: MemoryProgram,
        eval_result: EvalResult,
        iteration: int,
    ) -> MemoryProgram | None:
        """Reflect on failures and produce a mutated Memory Program.

        Returns None if code extraction fails or compile-fix loop is exhausted.
        Returned MemoryProgram is guaranteed to pass compile + smoke_test.
        """
        # Build failed case dicts for the prompt
        failed_dicts = []
        for fc in eval_result.failed_cases[:5]:
            failed_dicts.append(
                {
                    "question": fc.question,
                    "output": fc.output,
                    "expected": fc.expected,
                    "score": fc.score,
                    "conversation_history": fc.conversation_history,
                    "memory_logs": fc.memory_logs,
                }
            )

        system_prompt = REFLECTION_SYSTEM_PROMPT.format(interface_spec=MEMORY_INTERFACE_SPEC)
        user_prompt = build_reflection_user_prompt(
            code=current.source_code,
            score=eval_result.score,
            failed_cases=failed_dicts,
            iteration=iteration,
        )

        self.logger.log(f"Reflecting on iteration {iteration}, score={eval_result.score:.3f}", header="REFLECT")

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
        )
        output = response.choices[0].message.content

        # Extract code
        new_code = _extract_code_block(output)
        if new_code is None:
            self.logger.log("Failed to extract code block from reflection output", header="REFLECT")
            return None

        # Validate and fix loop
        error = self._validate_code(new_code)
        if error is None:
            return MemoryProgram(
                source_code=new_code,
                generation=current.generation + 1,
                parent_hash=current.hash,
            )

        for attempt in range(1, self.max_fix_attempts + 1):
            self.logger.log(f"Fix attempt {attempt}/{self.max_fix_attempts}: {error}", header="REFLECT")
            fixed_code = self._try_fix(new_code, error)
            if fixed_code is None:
                self.logger.log(f"Fix attempt {attempt}: no code block in LLM response", header="REFLECT")
                continue

            new_code = fixed_code
            error = self._validate_code(new_code)
            if error is None:
                self.logger.log(f"Fix succeeded on attempt {attempt}", header="REFLECT")
                return MemoryProgram(
                    source_code=new_code,
                    generation=current.generation + 1,
                    parent_hash=current.hash,
                )

        self.logger.log(f"All {self.max_fix_attempts} fix attempts exhausted", header="REFLECT")
        return None
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_reflector.py -v`
Expected: ALL PASS (both old and new tests)

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py tests/evolution/test_reflector.py
git commit -m "feat: add compile-fix loop to Reflector with max 3 retry attempts"
```

---

### Task 3: Update loop.py to remove redundant smoke_test

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py`
- Modify: `tests/evolution/test_loop.py`

**Step 1: Write/update the tests**

In `tests/evolution/test_loop.py`:
- Remove the `@patch("programmaticmemory.evolution.loop.smoke_test")` decorators from `test_child_accepted_when_better`, `test_child_rejected_when_worse`, and `test_tracker_receives_metrics` (and remove the `mock_smoke` parameter + setup lines)
- Remove `test_smoke_test_failure_skips_evaluation` entirely (this logic now lives in Reflector)
- The `mock_smoke` patches are no longer needed because loop.py no longer calls smoke_test

Updated tests:

```python
class TestEvolutionLoop:
    def test_initial_evaluation_only(self):
        """With max_iterations=0, only initial program is evaluated."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, per_case_scores=[0.5])

        reflector = MagicMock(spec=Reflector)

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=0,
        )
        state = loop.run()

        assert state.best_score == 0.5
        assert state.total_iterations == 0
        assert evaluator.evaluate.call_count == 1
        assert reflector.reflect_and_mutate.call_count == 0

    def test_child_accepted_when_better(self):
        """Child program replaces current when it scores higher."""
        train, val = _make_train_val()

        child_program = MemoryProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.8
        assert state.best_program == child_program
        assert state.history[-1].accepted is True

    def test_child_rejected_when_worse(self):
        """Child program is rejected when it scores lower."""
        train, val = _make_train_val()

        initial = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        child = MemoryProgram(source_code="worse", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.7, failed_cases=[]),
            EvalResult(score=0.3, per_case_scores=[0.3]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            initial_program=initial,
            max_iterations=1,
        )
        state = loop.run()

        assert state.best_score == 0.7
        assert state.best_program == initial
        assert state.history[-1].accepted is False

    def test_reflection_failure_skips_iteration(self):
        """If reflector returns None, iteration is skipped."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = None

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=2,
        )
        state = loop.run()

        assert evaluator.evaluate.call_count == 1
        assert reflector.reflect_and_mutate.call_count == 2
        assert state.total_iterations == 2

    def test_stop_condition_halts_loop(self):
        """Stop condition should terminate the loop early."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.return_value = EvalResult(score=0.5, failed_cases=[])

        reflector = MagicMock(spec=Reflector)

        stop = MagicMock()
        stop.return_value = True

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=10,
            stop_condition=stop,
        )
        state = loop.run()

        assert state.total_iterations == 0
        assert reflector.reflect_and_mutate.call_count == 0

    def test_tracker_receives_metrics(self):
        """ExperimentTracker should receive log_metrics calls."""
        train, val = _make_train_val()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.5, failed_cases=[]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = MemoryProgram(source_code="child", generation=1)

        tracker = MagicMock()

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            train_data=train,
            val_data=val,
            max_iterations=1,
            tracker=tracker,
        )
        loop.run()

        assert tracker.log_metrics.call_count >= 2
        assert tracker.log_summary.call_count == 1
```

**Step 2: Update loop.py implementation**

In `src/programmaticmemory/evolution/loop.py`:
- Remove import: `from programmaticmemory.evolution.sandbox import smoke_test`
- Remove import: `from programmaticmemory.evolution.toolkit import ToolkitConfig`
- Remove the `toolkit_config` parameter from `EvolutionLoop.__init__`
- Remove lines 97-103 (smoke_test call and its failure handling)
- The iteration body becomes: reflect -> if None continue -> evaluate -> accept/reject

Updated loop body (inside `for i in range(1, self.max_iterations + 1):`):

```python
            self.logger.log(f"Iteration {i}/{self.max_iterations}", header="EVOLUTION")

            # Reflect and mutate (includes compile-fix loop)
            child = self.reflector.reflect_and_mutate(current, eval_result, i)
            if child is None:
                self.logger.log("Reflection failed to produce valid code, skipping", header="EVOLUTION")
                state.history.append(EvolutionRecord(iteration=i, program=current, score=best_score, accepted=False))
                state.total_iterations = i
                continue

            # Evaluate child
            child_result = self.evaluator.evaluate(child, self.train_data, self.val_data, self.dataset_type)
            child_score = child_result.score
            # ... rest unchanged
```

**Step 3: Run all tests**

Run: `uv run pytest tests/evolution/test_loop.py tests/evolution/test_reflector.py -v`
Expected: ALL PASS

**Step 4: Run full test suite (non-LLM)**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: ALL PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_loop.py
git commit -m "refactor: remove smoke_test from loop.py, now handled by Reflector"
```

---

### Task 4: Verify everything works together

**Step 1: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: ALL PASS

**Step 2: Run linting**

Run: `ruff check src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/prompts.py`
Expected: No errors

**Step 3: Commit design doc**

```bash
git add docs/plans/
git commit -m "docs: add compile-fix loop design document"
```
