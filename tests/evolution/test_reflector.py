"""Tests for evolution/reflector.py — code extraction and reflection."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.prompts import ReflectionPromptConfig
from programmaticmemory.evolution.reflector import (
    Reflector,
    _extract_commit_message,
    _extract_full_code,
    _extract_patch,
)
from programmaticmemory.evolution.sandbox import CompileError, SmokeTestResult
from programmaticmemory.evolution.types import EvalResult, FailedCase, KBProgram


class TestExtractPatch:
    def test_single_block(self):
        text = (
            "Some analysis.\n\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ change\n"
            "-old line\n"
            "+new line\n"
            "*** End Patch\n\n"
            "Done."
        )
        patch = _extract_patch(text)
        assert patch is not None
        assert "*** Update File: program.py" in patch
        assert "-old line\n" in patch
        assert "+new line\n" in patch

    def test_multiple_blocks_takes_last(self):
        text = (
            "First attempt:\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ first\n"
            "-a\n"
            "+b\n"
            "*** End Patch\n\n"
            "Actually, better version:\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ second\n"
            "-x\n"
            "+y\n"
            "*** End Patch\n"
        )
        patch = _extract_patch(text)
        assert patch is not None
        assert "-x\n" in patch
        assert "+y\n" in patch
        # Should NOT contain the first patch's content
        assert "-a\n" not in patch

    def test_no_patch_returns_none(self):
        text = "No patch here, just analysis."
        assert _extract_patch(text) is None

    def test_multiline_patch(self):
        text = (
            "Analysis done.\n\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ imports\n"
            " from dataclasses import dataclass\n"
            "+import json\n"
            "@@ KnowledgeBase.read\n"
            " def read(self, query):\n"
            "-    return '\\n'.join(self.store)\n"
            "+    return json.dumps(self.store[-5:])\n"
            "*** End Patch\n"
        )
        patch = _extract_patch(text)
        assert patch is not None
        assert "*** Update File: program.py" in patch
        assert "+import json\n" in patch
        assert "+    return json.dumps(self.store[-5:])\n" in patch

    def test_non_patch_block_ignored(self):
        text = "```python\nclass A: pass\n```"
        assert _extract_patch(text) is None


class TestExtractFullCode:
    def test_extracts_last_python_block(self):
        text = (
            "Here is the improved program:\n\n"
            "```python\nclass First: pass\n```\n\n"
            "Actually, better version:\n\n"
            "```python\nclass Second: pass\n```\n"
        )
        code = _extract_full_code(text)
        assert code is not None
        assert "class Second" in code
        assert "class First" not in code

    def test_no_code_block_returns_none(self):
        assert _extract_full_code("No code here, just text.") is None

    def test_non_python_block_ignored(self):
        text = '```json\n{"key": "value"}\n```'
        assert _extract_full_code(text) is None

    def test_strips_whitespace(self):
        text = "```python\n\n  class Foo: pass\n\n```"
        code = _extract_full_code(text)
        assert code is not None
        assert code == "class Foo: pass"


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
        text = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"
        assert _extract_commit_message(text) is None

    def test_strips_whitespace(self):
        text = (
            "*** Commit Message\n  Title: Fix bug  \n  - Changed something  \n\n*** Begin Patch\nstuff\n*** End Patch"
        )
        msg = _extract_commit_message(text)
        assert msg is not None
        assert msg == "Title: Fix bug\n- Changed something"

    def test_commit_message_without_patch_still_extracts(self):
        text = "*** Commit Message\nTitle: Something\n- Did stuff\n"
        msg = _extract_commit_message(text)
        assert msg is not None
        assert "Title: Something" in msg


class TestReflector:
    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_successful_reflection(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch, snapshot: SnapshotAssertion
    ):
        """Reflector should produce a new KBProgram with incremented generation."""
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        patched_code = """\
from dataclasses import dataclass

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = {}

    def write(self, item):
        self.store[item.raw[:20]] = item.raw

    def read(self, query):
        return self.store.get(query.raw, "Not found")
"""
        mock_apply_patch.return_value = patched_code

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = (
            "Diagnosis: using dict.\n\n"
            "*** Begin Patch\n"
            "*** Update File: program.py\n"
            "@@ change\n"
            "-old line\n"
            "+new line\n"
            "*** End Patch"
        )
        mock_litellm.completion.return_value = mock_resp

        current = KBProgram(source_code="old code", generation=2)
        eval_result = EvalResult(
            score=0.3,
            failed_cases=[
                FailedCase(question="q", output="wrong", rationale="right", score=0.0),
            ],
        )

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(current, eval_result, iteration=3)

        assert child is not None
        assert child.program.generation == 3
        assert child.program.parent_hash == current.hash
        assert "class KnowledgeBase" in child.program.source_code
        assert "self.store" in child.program.source_code
        assert mock_litellm.completion.call_args.kwargs["messages"] == snapshot

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_no_code_block_returns_none(self, mock_litellm, snapshot: SnapshotAssertion):
        """If LLM output has no code block, enters fix loop then returns None."""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "I analyzed the code but can't suggest improvements."
        mock_litellm.completion.return_value = mock_resp

        current = KBProgram(source_code="x")
        eval_result = EvalResult(score=0.5)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(current, eval_result, iteration=1)

        assert child is None
        # 1 reflection + 3 fix attempts (all return no patch)
        assert mock_litellm.completion.call_count == 4
        # Snapshot the first (reflection) call's messages
        assert mock_litellm.completion.call_args_list[0].kwargs["messages"] == snapshot

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

        current = KBProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.2,
            failed_cases=[
                FailedCase(
                    question="What is X?",
                    output="unknown",
                    rationale="42",
                    score=0.0,
                    memory_logs=["Stored: X=42"],
                ),
            ],
        )

        reflector = Reflector(model="mock/model")
        reflector.reflect_and_mutate(current, eval_result, iteration=5)

        # 1 reflection + 3 fix attempts (all return "No code.")
        assert len(captured_messages) == 4
        messages = captured_messages[0]  # Check the reflection call
        # System placeholder + user message containing everything (interface spec + code + failed cases)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        user_content = messages[1]["content"]
        assert "KnowledgeItem" in user_content  # interface spec
        assert "code here" in user_content
        assert "0.200" in user_content
        assert "What is X?" in user_content
        assert "42" in user_content
        # Default config excludes memory logs (max_memory_log_chars=0)
        assert "Stored: X=42" not in user_content
        assert captured_messages == snapshot

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_uses_configured_model(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch, snapshot: SnapshotAssertion
    ):
        """Verify model is passed to litellm."""
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)
        mock_apply_patch.return_value = "class KnowledgeItem: pass\nclass Query: pass\nclass KnowledgeBase: pass"

        captured_kwargs = []

        def capture_completion(*args, **kwargs):
            captured_kwargs.append(kwargs)
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[
                0
            ].message.content = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"
            return mock_resp

        mock_litellm.completion = capture_completion

        reflector = Reflector(model="custom/reflect-model")
        reflector.reflect_and_mutate(
            KBProgram(source_code="x"),
            EvalResult(score=0.0),
            iteration=1,
        )

        assert captured_kwargs[0]["model"] == "custom/reflect-model"
        assert "temperature" not in captured_kwargs[0]
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

        current = KBProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.1,
            failed_cases=[
                FailedCase(question=f"Question {i}?", output=f"wrong_{i}", rationale=f"right_{i}", score=0.0)
                for i in range(1, 7)  # 6 failed cases
            ],
        )

        config = ReflectionPromptConfig(max_failed_cases=2)
        reflector = Reflector(model="mock/model", prompt_config=config)
        reflector.reflect_and_mutate(current, eval_result, iteration=1)

        # 1 reflection + 3 fix attempts
        assert len(captured_messages) == 4
        messages = captured_messages[0]  # Check the reflection call
        user_content = messages[1]["content"]
        # Weighted sampling selects exactly 2 from 6 (not all 6)
        case_count = user_content.count("<case id=")
        assert case_count == 2
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

        current = KBProgram(source_code="code here")
        eval_result = EvalResult(
            score=0.5,
            failed_cases=[
                FailedCase(question="What is X?", output="unknown", rationale="42", score=0.0),
            ],
            success_cases=[
                FailedCase(
                    question="What is Y?",
                    output="7",
                    rationale="7",
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

        # 1 reflection + 3 fix attempts
        assert len(captured_messages) == 4
        user_content = captured_messages[0][1]["content"]  # Check reflection call
        assert "<success_cases>" in user_content
        assert "What is Y?" in user_content
        assert "Preserve the behavior" in user_content
        assert captured_messages == snapshot

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflect_and_mutate_empty_choices_returns_none(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch
    ):
        """Empty response choices return None with safe handling."""
        mock_apply_patch.return_value = "class KnowledgeBase: pass"
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        mock_litellm.completion.return_value = MagicMock(choices=[])

        reflector = Reflector(model="mock/model")
        result = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert result is None

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflect_and_mutate_none_content_returns_none(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch
    ):
        """None response content returns None with safe handling."""
        mock_apply_patch.return_value = "class KnowledgeBase: pass"
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        mock_litellm.completion.return_value = MagicMock(choices=[MagicMock(message=MagicMock(content=None))])

        reflector = Reflector(model="mock/model")
        result = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert result is None


class TestReflectorPatchFormatRecovery:
    """Tests for patch format failure recovery via the fix loop."""

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_no_patch_enters_fix_loop_and_succeeds(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """When reflection output has no patch, fix loop retries and succeeds."""
        good_code = "class KnowledgeItem: pass\nclass Query: pass\nclass KnowledgeBase: pass"
        mock_apply_patch.return_value = good_code
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        # First call: reflection output with no patch markers
        no_patch_resp = MagicMock()
        no_patch_resp.choices = [MagicMock()]
        no_patch_resp.choices[0].message.content = "I improved the code but forgot the patch format."

        # Second call: fix LLM returns proper patch
        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[
            0
        ].message.content = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"

        mock_litellm.completion.side_effect = [no_patch_resp, fix_resp]

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2  # reflection + 1 fix

    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_full_code_fallback_skips_fix_loop(self, mock_litellm, mock_compile, mock_smoke):
        """When reflection output has no patch but has a ```python block, use it directly."""
        good_code = (
            "from dataclasses import dataclass\n\n"
            "@dataclass\nclass KnowledgeItem:\n    raw: str\n\n"
            "@dataclass\nclass Query:\n    raw: str\n\n"
            "class KnowledgeBase:\n"
            "    def __init__(self, toolkit): pass\n"
            "    def write(self, item, raw_text=''): pass\n"
            "    def read(self, query): return ''"
        )

        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        resp = MagicMock()
        resp.choices = [MagicMock()]
        resp.choices[0].message.content = f"Here is the improved program:\n\n```python\n{good_code}\n```"
        mock_litellm.completion.return_value = resp

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert child.program.source_code == good_code
        assert mock_litellm.completion.call_count == 1  # Only reflection, no fix needed

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_no_patch_no_code_exhausts_fix_loop(self, mock_litellm):
        """When reflection and all fix attempts produce no valid output, return None."""
        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = "I cannot produce a patch."
        mock_litellm.completion.return_value = bad_resp

        reflector = Reflector(model="mock/model", max_fix_attempts=2)
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is None
        assert mock_litellm.completion.call_count == 3  # 1 reflection + 2 fix attempts

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_apply_patch_failure_enters_fix_loop(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """When apply_patch raises, fall through to fix loop instead of returning None."""
        good_code = "class KnowledgeItem: pass\nclass Query: pass\nclass KnowledgeBase: pass"

        # First apply_patch (from reflect) raises, second (from fix) succeeds
        mock_apply_patch.side_effect = [RuntimeError("context mismatch"), good_code]
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflect_resp = MagicMock()
        reflect_resp.choices = [MagicMock()]
        reflect_resp.choices[
            0
        ].message.content = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"

        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[
            0
        ].message.content = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"

        mock_litellm.completion.side_effect = [reflect_resp, fix_resp]

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2


class TestReflectorCompileFixLoop:
    _PATCH_RESPONSE = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_valid_code_returns_immediately(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """When code compiles and passes smoke test, return without fix attempts."""
        new_code = "from dataclasses import dataclass\n\n@dataclass\nclass KnowledgeItem:\n    raw: str\n\n@dataclass\nclass Query:\n    raw: str\n\nclass KnowledgeBase:\n    def __init__(self, toolkit): pass\n    def write(self, item): pass\n    def read(self, query): return ''"
        mock_apply_patch.return_value = new_code

        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = f"Analysis.\n\n{self._PATCH_RESPONSE}"
        mock_litellm.completion.return_value = mock_resp

        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 1  # Only the reflection call, no fix calls

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_compile_error_triggers_fix_and_succeeds(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """CompileError triggers fix loop; fixed code is returned."""
        good_code = "class KnowledgeItem: pass\nclass Query: pass\nclass KnowledgeBase: pass"

        # First apply_patch call (from reflect) returns bad code, second (from fix) returns good code
        mock_apply_patch.side_effect = ["bad code", good_code]

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = self._PATCH_RESPONSE

        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[0].message.content = self._PATCH_RESPONSE

        mock_litellm.completion.side_effect = [reflection_resp, fix_resp]

        # First compile fails, second succeeds
        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="invalid syntax"),
            MagicMock(),
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2  # reflection + 1 fix

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_smoke_test_failure_triggers_fix(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """Smoke test failure triggers fix loop."""
        good_code = "class KnowledgeItem: pass\nclass Query: pass\nclass KnowledgeBase: pass"

        # First apply_patch (reflect) returns code v1, second (fix) returns good code
        mock_apply_patch.side_effect = ["code v1", good_code]

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = self._PATCH_RESPONSE

        fix_resp = MagicMock()
        fix_resp.choices = [MagicMock()]
        fix_resp.choices[0].message.content = self._PATCH_RESPONSE

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
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert mock_litellm.completion.call_count == 2

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_max_fix_attempts_exhausted_returns_none(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """After max_fix_attempts, return None."""
        # reflect call applies patch -> bad code; each fix call applies patch -> still bad code
        mock_apply_patch.return_value = "bad"

        bad_resp = MagicMock()
        bad_resp.choices = [MagicMock()]
        bad_resp.choices[0].message.content = self._PATCH_RESPONSE
        mock_litellm.completion.return_value = bad_resp

        mock_compile.return_value = CompileError(message="Syntax error", details="bad")

        reflector = Reflector(model="mock/model", max_fix_attempts=3)
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is None
        # 1 reflection + 3 fix attempts = 4
        assert mock_litellm.completion.call_count == 4

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_code_extraction_failure_counts_as_attempt(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch
    ):
        """If fix LLM returns no patch, it still counts as an attempt."""
        # Reflection: patch -> apply_patch returns bad code
        mock_apply_patch.return_value = "bad"

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = self._PATCH_RESPONSE

        no_patch_resp = MagicMock()
        no_patch_resp.choices = [MagicMock()]
        no_patch_resp.choices[0].message.content = "I cannot fix this."

        mock_litellm.completion.side_effect = [reflection_resp, no_patch_resp, no_patch_resp, no_patch_resp]
        mock_compile.return_value = CompileError(message="Syntax error", details="bad")

        reflector = Reflector(model="mock/model", max_fix_attempts=3)
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is None
        assert mock_litellm.completion.call_count == 4  # 1 reflection + 3 fix attempts

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_succeeds_on_second_attempt(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """First fix attempt fails, second succeeds — verifies code forwarding between attempts."""
        # reflect -> "original bad", fix1 -> "still bad", fix2 -> "finally good"
        mock_apply_patch.side_effect = ["original bad", "still bad", "finally good"]

        reflection_resp = MagicMock()
        reflection_resp.choices = [MagicMock()]
        reflection_resp.choices[0].message.content = self._PATCH_RESPONSE

        fix1_resp = MagicMock()
        fix1_resp.choices = [MagicMock()]
        fix1_resp.choices[0].message.content = self._PATCH_RESPONSE

        fix2_resp = MagicMock()
        fix2_resp.choices = [MagicMock()]
        fix2_resp.choices[0].message.content = self._PATCH_RESPONSE

        mock_litellm.completion.side_effect = [reflection_resp, fix1_resp, fix2_resp]

        mock_compile.side_effect = [
            CompileError(message="Syntax error", details="line 1"),  # initial
            CompileError(message="Syntax error", details="line 2"),  # fix attempt 1
            MagicMock(),  # fix attempt 2
        ]
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        child = reflector.reflect_and_mutate(
            KBProgram(source_code="old", generation=0),
            EvalResult(score=0.3, failed_cases=[FailedCase(question="q", output="o", rationale="e", score=0.0)]),
            iteration=1,
        )

        assert child is not None
        assert child.program.source_code == "finally good"
        assert mock_litellm.completion.call_count == 3  # reflection + 2 fix attempts


class TestReflectorRuntimeFix:
    """Tests for Reflector.fix_runtime_violation."""

    _PATCH_RESPONSE = "*** Begin Patch\n*** Update File: program.py\n@@ change\n-old\n+new\n*** End Patch"

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_succeeds(self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch):
        """LLM returns valid fix -> compile+smoke pass -> return fixed code."""
        fixed_code = "class KnowledgeItem:\n  pass\nclass Query:\n  pass\nclass KnowledgeBase:\n  pass"
        mock_apply_patch.return_value = fixed_code
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=self._PATCH_RESPONSE))]
        )
        mock_compile.return_value = MagicMock()
        mock_smoke.return_value = SmokeTestResult(success=True)

        reflector = Reflector(model="mock/model")
        result = reflector.fix_runtime_violation("old code", "memory.read() returned 5000 chars (limit: 3000)")

        assert result == fixed_code
        # _try_fix was called — verify the prompt includes "Runtime violation"
        call_args = mock_litellm.completion.call_args
        prompt = call_args.kwargs["messages"][1]["content"]
        assert "Runtime violation" in prompt

    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_no_patch_returns_none(self, mock_litellm):
        """LLM returns no patch -> return None."""
        mock_litellm.completion.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="I don't know how to fix this."))]
        )
        reflector = Reflector(model="mock/model")
        result = reflector.fix_runtime_violation("old code", "memory.read() timed out after 5.0s")

        assert result is None

    @patch("programmaticmemory.evolution.reflector.apply_patch")
    @patch("programmaticmemory.evolution.reflector.smoke_test")
    @patch("programmaticmemory.evolution.reflector.compile_kb_program")
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_fix_with_compile_error_enters_compile_fix_loop(
        self, mock_litellm, mock_compile, mock_smoke, mock_apply_patch
    ):
        """First fix has compile error -> compile-fix loop fixes it."""
        first_fix = "bad code"
        second_fix = "good code"
        # First _try_fix (runtime) -> bad code; second _try_fix (compile-fix) -> good code
        mock_apply_patch.side_effect = [first_fix, second_fix]
        mock_litellm.completion.side_effect = [
            # First call: _try_fix for runtime violation
            MagicMock(choices=[MagicMock(message=MagicMock(content=self._PATCH_RESPONSE))]),
            # Second call: compile-fix loop's _try_fix
            MagicMock(choices=[MagicMock(message=MagicMock(content=self._PATCH_RESPONSE))]),
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
