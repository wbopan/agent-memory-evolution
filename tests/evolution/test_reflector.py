"""Tests for evolution/reflector.py — code extraction and reflection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.prompts import ReflectionPromptConfig
from programmaticmemory.evolution.reflector import Reflector, _extract_code_block
from programmaticmemory.evolution.sandbox import CompileError, SmokeTestResult
from programmaticmemory.evolution.types import EvalResult, FailedCase, MemoryProgram


class TestExtractCodeBlock:
    def test_single_block(self):
        text = "Some analysis.\n```python\nclass A: pass\n```\nDone."
        assert _extract_code_block(text) == "class A: pass"

    def test_multiple_blocks_takes_last(self):
        text = """\
Here's a helper:
```python
def helper(): pass
```

And the full code:
```python
class Memory: pass
```
"""
        assert _extract_code_block(text) == "class Memory: pass"

    def test_no_code_block_returns_none(self):
        text = "No code here, just analysis."
        assert _extract_code_block(text) is None

    def test_multiline_code(self):
        text = """\
Analysis done.

```python
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str
    category: str = "general"

@dataclass
class Query:
    raw: str

class Memory:
    def __init__(self, toolkit):
        self.store = []

    def write(self, obs):
        self.store.append(obs.raw)

    def read(self, query):
        return "\\n".join(self.store)
```
"""
        code = _extract_code_block(text)
        assert code is not None
        assert "class Observation" in code
        assert "class Query" in code
        assert "class Memory" in code
        assert "@dataclass" in code

    def test_non_python_block_ignored(self):
        text = "```json\n{}\n```"
        assert _extract_code_block(text) is None


class TestReflector:
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_successful_reflection(self, mock_litellm, mock_compile, mock_smoke, snapshot: SnapshotAssertion):
        """Reflector should produce a new MemoryProgram with incremented generation."""
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        new_code = """\
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class Memory:
    def __init__(self, toolkit):
        self.store = {}

    def write(self, obs):
        self.store[obs.raw[:20]] = obs.raw

    def read(self, query):
        return self.store.get(query.raw, "Not found")
"""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = f"Diagnosis: using dict.\n\n```python\n{new_code}\n```"
        mock_litellm.completion.return_value = mock_resp

        current = MemoryProgram(source_code="old code", generation=2)
        eval_result = EvalResult(
            score=0.3,
            failed_cases=[
                FailedCase(question="q", output="wrong", expected="right", score=0.0),
            ],
        )

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(current, eval_result, iteration=3)

        assert child is not None
        assert child.generation == 3
        assert child.parent_hash == current.hash
        assert "class Memory" in child.source_code
        assert "self.store" in child.source_code
        assert mock_litellm.completion.call_args.kwargs["messages"] == snapshot

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_no_code_block_returns_none(self, mock_litellm, snapshot: SnapshotAssertion):
        """If LLM output has no code block, return None."""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "I analyzed the code but can't suggest improvements."
        mock_litellm.completion.return_value = mock_resp

        current = MemoryProgram(source_code="x")
        eval_result = EvalResult(score=0.5)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(current, eval_result, iteration=1)

        assert child is None
        assert mock_litellm.completion.call_args.kwargs["messages"] == snapshot

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_passes_failed_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """Verify the reflection prompt includes failed case info."""
        captured_messages = []

        def capture_completion(*args, **kwargs):
            captured_messages.append(kwargs.get("messages", []))
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "No code."
            return mock_resp

        mock_litellm.completion = capture_completion

        current = MemoryProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.2,
            failed_cases=[
                FailedCase(
                    question="What is X?",
                    output="unknown",
                    expected="42",
                    score=0.0,
                    memory_logs=["Stored: X=42"],
                ),
            ],
        )

        reflector = Reflector(model="mock/model")
        reflector.reflect_and_mutate(current, eval_result, iteration=5)

        assert len(captured_messages) == 1
        messages = captured_messages[0]
        # Single user message containing everything (interface spec + code + failed cases)
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        user_content = messages[0]["content"]
        assert "Observation" in user_content  # interface spec
        assert "code here" in user_content
        assert "0.200" in user_content
        assert "What is X?" in user_content
        assert "42" in user_content
        # Default config excludes memory logs (max_memory_log_chars=0)
        assert "Stored: X=42" not in user_content
        assert captured_messages == snapshot

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_uses_configured_model_and_temperature(
        self, mock_litellm, mock_compile, mock_smoke, snapshot: SnapshotAssertion
    ):
        """Verify model and temperature are passed to litellm."""
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        captured_kwargs = []

        def capture_completion(*args, **kwargs):
            captured_kwargs.append(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[
                0
            ].message.content = "```python\nclass Observation: pass\nclass Query: pass\nclass Memory: pass\n```"
            return mock_resp

        mock_litellm.completion = capture_completion

        reflector = Reflector(model="custom/reflect-model", temperature=0.9)
        reflector.reflect_and_mutate(
            MemoryProgram(source_code="x"),
            EvalResult(score=0.0),
            iteration=1,
        )

        assert captured_kwargs[0]["model"] == "custom/reflect-model"
        assert captured_kwargs[0]["temperature"] == 0.9
        assert [kw["messages"] for kw in captured_kwargs] == snapshot

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_prompt_config_limits_failed_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """ReflectionPromptConfig.max_failed_cases limits how many cases appear in the prompt."""
        captured_messages = []

        def capture_completion(*args, **kwargs):
            captured_messages.append(kwargs.get("messages", []))
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "No code."
            return mock_resp

        mock_litellm.completion = capture_completion

        current = MemoryProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.1,
            failed_cases=[
                FailedCase(question=f"Question {i}?", output=f"wrong_{i}", expected=f"right_{i}", score=0.0)
                for i in range(1, 7)  # 6 failed cases
            ],
        )

        config = ReflectionPromptConfig(max_failed_cases=2)
        reflector = Reflector(model="mock/model", prompt_config=config)
        reflector.reflect_and_mutate(current, eval_result, iteration=1)

        assert len(captured_messages) == 1
        messages = captured_messages[0]
        user_content = messages[0]["content"]
        # Only 2 cases should appear (not 5 or 6)
        assert "Question 1?" in user_content
        assert "Question 2?" in user_content
        assert "Question 3?" not in user_content
        assert "Question 6?" not in user_content
        assert captured_messages == snapshot

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_passes_success_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """Verify the reflection prompt includes success case info."""
        captured_messages = []

        def capture_completion(*args, **kwargs):
            captured_messages.append(kwargs.get("messages", []))
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "No code."
            return mock_resp

        mock_litellm.completion = capture_completion

        current = MemoryProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.5,
            failed_cases=[
                FailedCase(question="What is X?", output="unknown", expected="42", score=0.0),
            ],
            success_cases=[
                FailedCase(
                    question="What is Y?",
                    output="7",
                    expected="7",
                    score=1.0,
                    conversation_history=[
                        {"role": "user", "content": "query for Y"},
                        {"role": "assistant", "content": "7"},
                    ],
                ),
            ],
        )

        reflector = Reflector(model="mock/model")
        reflector.reflect_and_mutate(current, eval_result, iteration=3)

        assert len(captured_messages) == 1
        user_content = captured_messages[0][0]["content"]
        assert "<success_cases>" in user_content
        assert "What is Y?" in user_content
        assert "Preserve the behavior" in user_content
        assert captured_messages == snapshot


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

        mock_compile.return_value = MagicMock()
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
            MagicMock(),
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
        mock_compile.return_value = MagicMock()
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
        assert mock_litellm.completion.call_count == 4  # 1 reflection + 3 fix attempts

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_succeeds_on_second_attempt(self, mock_litellm, mock_compile, mock_smoke):
        """First fix attempt fails, second succeeds — verifies code forwarding between attempts."""
        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = "```python\noriginal bad\n```"

        fix1_resp = MagicMock()
        fix1_resp.choices = [MagicMock()]
        fix1_resp.choices[0].message.content = "```python\nstill bad\n```"

        fix2_resp = MagicMock()
        fix2_resp.choices = [MagicMock()]
        fix2_resp.choices[0].message.content = "```python\nfinally good\n```"

        mock_litellm.completion.side_effect = [reflection_resp, fix1_resp, fix2_resp]

        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="line 1"),  # initial
            CompileError(message="Syntax error", details="line 2"),  # fix attempt 1
            MagicMock(),  # fix attempt 2
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            MemoryProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert child.source_code == "finally good"
        assert mock_litellm.completion.call_count == 3  # reflection + 2 fix attempts


class TestReflectorRuntimeFix:
    """Tests for Reflector.fix_runtime_violation."""

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_succeeds(self, mock_litellm, mock_compile, mock_smoke):
        """LLM returns valid fix -> compile+smoke pass -> return fixed code."""
        fixed_code = "class Observation:\n  pass\nclass Query:\n  pass\nclass Memory:\n  pass"
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=f"```python\n{fixed_code}\n```"))]
        )
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        result = reflector.fix_runtime_violation("old code", "memory.read() returned 5000 chars (limit: 1000)")

        assert result == fixed_code
        # _try_fix was called — verify the prompt includes "Runtime violation"
        call_args = mock_litellm.completion.call_args
        prompt = call_args.kwargs["messages"][0]["content"]
        assert "Runtime violation" in prompt

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_no_code_block_returns_none(self, mock_litellm):
        """LLM returns no code block -> return None."""
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="I don't know how to fix this."))]
        )
        reflector = Reflector(model="mock/model")
        result = reflector.fix_runtime_violation("old code", "memory.read() timed out after 5.0s")

        assert result is None

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_memory_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_with_compile_error_enters_compile_fix_loop(self, mock_litellm, mock_compile, mock_smoke):
        """First fix has compile error -> compile-fix loop fixes it."""
        first_fix = "bad code"
        second_fix = "good code"
        mock_litellm.completion.side_effect = [
            # First call: _try_fix for runtime violation -> code with compile error
            MagicMock(choices=[MagicMock(message=MagicMock(content=f"```python\n{first_fix}\n```"))]),
            # Second call: compile-fix loop's _try_fix -> valid code
            MagicMock(choices=[MagicMock(message=MagicMock(content=f"```python\n{second_fix}\n```"))]),
        ]
        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="invalid syntax"),  # first_fix fails
            MagicMock(),  # second_fix compiles
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        result = reflector.fix_runtime_violation("old code", "memory.read() timed out")

        assert result == second_fix
        assert mock_litellm.completion.call_count == 2
