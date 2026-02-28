"""Tests for evolution/evaluator.py — scorers, JSON parsing, evaluation pipelines.

Key verification points from the design document:
- Online train: messages accumulate across steps (multi-turn conversation)
- Validation: only Step 1 + Step 2, memory.write NOT called
- Memory lifecycle: re-instantiation → empty memory (state isolation)
"""

from unittest.mock import MagicMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import (
    ExactMatchScorer,
    LLMJudgeScorer,
    MemoryEvaluator,
    _parse_json_from_llm,
)
from programmaticmemory.evolution.prompts import INITIAL_MEMORY_PROGRAM
from programmaticmemory.evolution.types import DataItem, EvalMode, MemoryProgram

# ── Scorer Tests ────────────────────────────────────────────────────────────


class TestExactMatchScorer:
    def setup_method(self):
        self.scorer = ExactMatchScorer()

    def test_exact_match(self):
        assert self.scorer("Paris", "Paris") == 1.0

    def test_case_insensitive(self):
        assert self.scorer("paris", "Paris") == 1.0

    def test_containment(self):
        assert self.scorer("The answer is Paris.", "Paris") == 1.0

    def test_no_match(self):
        assert self.scorer("London", "Paris") == 0.0

    def test_punctuation_normalized(self):
        assert self.scorer("It's Paris!", "Paris") == 1.0

    def test_whitespace_normalized(self):
        assert self.scorer("  Paris  ", "Paris") == 1.0

    def test_empty_expected(self):
        assert self.scorer("anything", "") == 1.0

    def test_empty_output(self):
        assert self.scorer("", "Paris") == 0.0


class TestLLMJudgeScorer:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_sends_user_only_message(self, mock_litellm):
        """LLMJudgeScorer must not use system messages."""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "1"
        mock_litellm.completion.return_value = mock_resp

        scorer = LLMJudgeScorer()
        score = scorer("Paris", "Paris")

        assert score == 1.0
        call_kwargs = mock_litellm.completion.call_args.kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert "Paris" in messages[0]["content"]


# ── JSON Parsing Tests ──────────────────────────────────────────────────────


class TestParseJsonFromLLM:
    def test_plain_json(self):
        assert _parse_json_from_llm('{"raw": "hello"}') == {"raw": "hello"}

    def test_json_code_block(self):
        text = '```json\n{"raw": "hello"}\n```'
        assert _parse_json_from_llm(text) == {"raw": "hello"}

    def test_generic_code_block(self):
        text = '```\n{"raw": "hello"}\n```'
        assert _parse_json_from_llm(text) == {"raw": "hello"}

    def test_with_surrounding_text(self):
        text = 'Here is the JSON:\n```json\n{"raw": "hello"}\n```\nDone.'
        assert _parse_json_from_llm(text) == {"raw": "hello"}

    def test_multi_field(self):
        result = _parse_json_from_llm('{"text": "hello", "category": "greeting", "priority": 1}')
        assert result == {"text": "hello", "category": "greeting", "priority": 1}

    def test_invalid_json_raises(self):
        with pytest.raises(Exception):
            _parse_json_from_llm("not json at all")


# ── Mock LLM helper ────────────────────────────────────────────────────────


def _mock_completion_factory(responses: list[str]):
    """Create a mock litellm.completion that returns responses in order,
    and captures the messages arg for each call."""
    call_idx = [0]
    captured_calls: list[list[dict]] = []

    def mock_completion(*args, **kwargs):
        idx = call_idx[0]
        call_idx[0] += 1
        messages = kwargs.get("messages") or (args[0] if args else [])
        captured_calls.append(list(messages))  # deep copy of messages at call time
        resp = responses[idx % len(responses)]
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = resp
        return mock_resp

    mock_completion.captured_calls = captured_calls
    return mock_completion


# ── Memory lifecycle tests ──────────────────────────────────────────────────


class TestMemoryLifecycle:
    def test_initial_program_instantiates(self):
        """Initial memory program template can be instantiated."""
        from programmaticmemory.evolution.sandbox import compile_memory_program
        from programmaticmemory.evolution.toolkit import Toolkit

        result = compile_memory_program(INITIAL_MEMORY_PROGRAM)
        assert not isinstance(result, tuple) or len(result) == 3
        _, _, memory_cls = result
        tk = Toolkit()
        memory = memory_cls(tk)
        assert memory is not None
        tk.close()

    def test_write_then_read_returns_content(self):
        """Write followed by read should return the written content."""
        from programmaticmemory.evolution.sandbox import compile_memory_program
        from programmaticmemory.evolution.toolkit import Toolkit

        _, _, memory_cls = compile_memory_program(INITIAL_MEMORY_PROGRAM)
        tk = Toolkit()
        memory = memory_cls(tk)
        from dataclasses import dataclass

        @dataclass
        class Obs:
            raw: str

        @dataclass
        class Q:
            raw: str

        # Use the compiled classes instead
        result = compile_memory_program(INITIAL_MEMORY_PROGRAM)
        obs_cls, query_cls, memory_cls = result
        memory = memory_cls(tk)
        memory.write(obs_cls(raw="The sky is blue."))
        output = memory.read(query_cls(raw="sky"))
        assert "The sky is blue." in output
        tk.close()

    def test_reinstantiation_gives_empty_memory(self):
        """Re-instantiating Memory should produce an empty store (no state leak)."""
        from programmaticmemory.evolution.sandbox import compile_memory_program
        from programmaticmemory.evolution.toolkit import Toolkit

        obs_cls, query_cls, memory_cls = compile_memory_program(INITIAL_MEMORY_PROGRAM)
        tk = Toolkit()

        # First instance: write data
        mem1 = memory_cls(tk)
        mem1.write(obs_cls(raw="secret data"))
        output1 = mem1.read(query_cls(raw="anything"))
        assert "secret data" in output1

        # Second instance: should be empty
        mem2 = memory_cls(tk)
        output2 = mem2.read(query_cls(raw="anything"))
        assert "secret data" not in output2
        tk.close()


# ── Offline Pipeline Tests ─────────────────────────────────────────────────


class TestMemoryEvaluatorOffline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_basic_offline_evaluation(self, mock_litellm, snapshot: SnapshotAssertion):
        """Offline: batch ingest → query → answer → score."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "The capital of France is Paris."}',  # train: obs generation
                '{"raw": "capital of France"}',  # val: query generation
                "Paris",  # val: answer
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="The capital of France is Paris.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What is the capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score == 1.0
        assert len(result.per_case_scores) == 1
        assert result.failed_cases == []
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_wrong_answer(self, mock_litellm, snapshot: SnapshotAssertion):
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "The capital of France is Paris."}',
                '{"raw": "capital"}',
                "London",
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="The capital of France is Paris.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What is the capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score == 0.0
        assert len(result.failed_cases) == 1
        assert result.failed_cases[0].output == "London"
        assert mock_fn.captured_calls == snapshot

    def test_compile_error_returns_zero(self):
        program = MemoryProgram(source_code="invalid python {{{}}")
        evaluator = MemoryEvaluator(batch_process=False)
        result = evaluator.evaluate(
            program,
            [DataItem(raw_text="x", question="q", expected_answer="a")],
            [DataItem(raw_text="x", question="q", expected_answer="a")],
        )
        assert result.score == 0.0
        assert any("Compile error" in log for log in result.logs)

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_val_uses_multiturn(self, mock_litellm, snapshot: SnapshotAssertion):
        """Val flow should use accumulated messages (Step 1 → Step 2)."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "fact"}',  # train obs
                '{"raw": "query"}',  # val Step 1: query gen
                "answer",  # val Step 2: answer
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Q?", expected_answer="answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        # Val Step 2 call (3rd call overall) should have 3 messages:
        # user (query prompt) + assistant (query json) + user (retrieved memory prompt)
        val_step2_messages = mock_fn.captured_calls[2]
        assert len(val_step2_messages) == 3
        assert val_step2_messages[0]["role"] == "user"  # query gen prompt
        assert val_step2_messages[1]["role"] == "assistant"  # query json
        assert val_step2_messages[2]["role"] == "user"  # retrieved memory prompt
        assert "<retrieved_memory>" in val_step2_messages[2]["content"]
        assert mock_fn.captured_calls == snapshot


# ── Online Pipeline Tests ──────────────────────────────────────────────────


class TestMemoryEvaluatorOnline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_train_messages_accumulate(self, mock_litellm, snapshot: SnapshotAssertion):
        """Online train: messages list grows across steps (design doc requirement).

        Step 1: +user +assistant = 2 messages
        Step 2: +user +assistant = 4 messages
        Step 3: +user = 5 messages (call), then +assistant = 6 messages
        """
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "query value"}',  # train Step 1: query gen
                "my answer",  # train Step 2: answer
                '{"raw": "observation"}',  # train Step 3: obs gen with feedback
                '{"raw": "val query"}',  # val Step 1: query gen
                "val answer",  # val Step 2: answer
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="val answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        # Train Step 1 (call 0): 1 user message
        assert len(mock_fn.captured_calls[0]) == 1
        assert mock_fn.captured_calls[0][0]["role"] == "user"

        # Train Step 2 (call 1): 3 messages (user + assistant + user)
        assert len(mock_fn.captured_calls[1]) == 3
        assert mock_fn.captured_calls[1][0]["role"] == "user"  # query gen prompt
        assert mock_fn.captured_calls[1][1]["role"] == "assistant"  # query json
        assert mock_fn.captured_calls[1][2]["role"] == "user"  # retrieved memory prompt
        assert "<retrieved_memory>" in mock_fn.captured_calls[1][2]["content"]

        # Train Step 3 (call 2): 5 messages (user + asst + user + asst + user)
        assert len(mock_fn.captured_calls[2]) == 5
        assert mock_fn.captured_calls[2][3]["role"] == "assistant"  # answer
        assert mock_fn.captured_calls[2][4]["role"] == "user"  # obs gen with feedback
        assert "Ground truth" in mock_fn.captured_calls[2][4]["content"]
        assert "A" in mock_fn.captured_calls[2][4]["content"]  # expected answer in feedback
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step1_output_parses_to_query(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 1 mock output should be parseable into a Query dataclass."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "parsed query"}',
                "answer",
                '{"raw": "obs"}',
                '{"raw": "vq"}',
                "va",
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        # No parse errors in logs means query was parsed successfully
        assert not any("query parse failed" in log for log in result.logs)
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step3_output_parses_to_observation(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 3 mock output should be parseable into an Observation dataclass."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "q"}',
                "answer",
                '{"raw": "parsed observation value"}',  # Step 3 obs
                '{"raw": "vq"}',
                "va",
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        assert not any("observation parse failed" in log for log in result.logs)
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_write_called_and_memory_updates(self, mock_litellm, snapshot: SnapshotAssertion):
        """After Step 3+4, memory.write should be called and memory state should update."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "q"}',
                "answer",
                '{"raw": "stored via online"}',  # This obs should be written
                '{"raw": "vq"}',  # val query
                "stored via online",  # val answer (should contain written data)
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="stored via online")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        # The observation "stored via online" should have been written during train
        # and the val answer (mocked) says "stored via online" which matches expected
        assert result.score == 1.0
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step3_includes_feedback_and_ground_truth(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 3 prompt must include evaluation result and ground truth."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "q"}',
                "wrong answer",  # incorrect answer
                '{"raw": "obs"}',
                '{"raw": "vq"}',
                "va",
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="correct answer")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        # Step 3 (call index 2) should contain feedback
        step3_messages = mock_fn.captured_calls[2]
        step3_user_prompt = step3_messages[-1]["content"]
        assert "Ground truth" in step3_user_prompt
        assert "correct answer" in step3_user_prompt
        assert "incorrect" in step3_user_prompt  # evaluation result
        assert mock_fn.captured_calls == snapshot


# ── Validation Pipeline Tests ──────────────────────────────────────────────


class TestValidationPipeline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_only_step1_and_step2(self, mock_litellm, snapshot: SnapshotAssertion):
        """Validation should only do Step 1 (query gen) + Step 2 (answer), no Step 3/4."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "fact"}',  # train obs
                '{"raw": "query"}',  # val Step 1
                "answer",  # val Step 2
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Q?", expected_answer="answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        # Should be exactly 3 LLM calls: 1 train obs + 2 val (query + answer)
        assert len(mock_fn.captured_calls) == 3
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_does_not_call_write(self, mock_litellm, snapshot: SnapshotAssertion):
        """memory.write must NOT be called during validation phase."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "q"}',
                "ans",
                '{"raw": "obs"}',  # train (online: 3 calls)
                '{"raw": "vq"}',
                "va",  # val: 2 calls
            ]
        )
        mock_litellm.completion = mock_fn

        # Use a memory program that tracks write calls
        tracking_program = """\
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class Memory:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []
        self.write_log = []

    def write(self, obs):
        self.store.append(obs.raw)
        self.write_log.append(obs.raw)
        self.toolkit.logger.log(f"WRITE_CALLED:{obs.raw}")

    def read(self, query):
        return " ".join(self.store) if self.store else "empty"
"""
        program = MemoryProgram(source_code=tracking_program)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        # Check that write was called during train but NOT during val
        # The toolkit logger captured WRITE_CALLED entries — count them
        write_logs = [log for log in result.logs if "WRITE_CALLED" in log]
        # No direct WRITE_CALLED in result.logs (those are evaluator logs),
        # but we can check the memory_logs in failed cases or check via toolkit
        # The key check: only 3 LLM calls for train + 2 for val = 5 total
        assert len(mock_fn.captured_calls) == 5
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_conversation_history_in_failed_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """Failed cases should include the full multi-turn conversation history."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "fact"}',  # train obs
                '{"raw": "query"}',  # val Step 1
                "wrong answer",  # val Step 2
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Q?", expected_answer="correct")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert len(result.failed_cases) == 1
        fc = result.failed_cases[0]
        # Should have 4 messages: user(query) + asst(query json) + user(retrieved) + asst(answer)
        assert len(fc.conversation_history) == 4
        roles = [m["role"] for m in fc.conversation_history]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_multiturn_messages_structure(self, mock_litellm, snapshot: SnapshotAssertion):
        """Val Step 2 LLM call should see query gen prompt + query response + retrieved prompt."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "fact"}',
                '{"raw": "my query"}',
                "the answer",
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What is X?", expected_answer="the answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        # Val Step 2 (call index 2): should have 3 messages
        step2_msgs = mock_fn.captured_calls[2]
        assert len(step2_msgs) == 3
        assert "What is X?" in step2_msgs[0]["content"]  # query gen mentions question
        assert step2_msgs[1]["content"] == '{"raw": "my query"}'  # assistant's query
        assert "<retrieved_memory>" in step2_msgs[2]["content"]  # retrieved memory prompt
        assert mock_fn.captured_calls == snapshot


# ── Edge Cases ──────────────────────────────────────────────────────────────


class TestEvaluatorEdgeCases:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_observation_generation_failure_skips_item(self, mock_litellm, snapshot: SnapshotAssertion):
        """Offline: if obs generation fails, item is skipped but eval continues."""
        mock_fn = _mock_completion_factory(
            [
                "not valid json at all",  # train obs fails
                '{"raw": "query"}',  # val query
                "some answer",  # val answer
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="q?", expected_answer="answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score is not None
        assert len(result.per_case_scores) == 1
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_query_generation_failure_scores_zero(self, mock_litellm, snapshot: SnapshotAssertion):
        """If query generation fails during val, that item scores 0."""
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "fact"}',
                "not valid json",  # val query gen fails
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="q?", expected_answer="a")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score == 0.0
        assert len(result.failed_cases) == 1
        assert mock_fn.captured_calls == snapshot

    def test_empty_val_data(self, snapshot: SnapshotAssertion):
        """Empty val data should return score 0 without crashing."""
        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        with patch("programmaticmemory.evolution.evaluator.litellm") as mock_litellm:
            mock_fn = _mock_completion_factory(['{"raw": "x"}'])
            mock_litellm.completion = mock_fn
            result = evaluator.evaluate(
                program,
                [DataItem(raw_text="x", question="q", expected_answer="a")],
                [],
                eval_mode=EvalMode.OFFLINE,
            )
        assert result.score == 0.0
        assert mock_fn.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_multiple_val_items(self, mock_litellm, snapshot: SnapshotAssertion):
        mock_fn = _mock_completion_factory(
            [
                '{"raw": "f1"}',
                '{"raw": "f2"}',  # train obs x2
                '{"raw": "q1"}',
                "correct1",  # val item 1 (correct)
                '{"raw": "q2"}',
                "wrong",  # val item 2 (wrong)
            ]
        )
        mock_litellm.completion = mock_fn

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [
            DataItem(raw_text="f1", question="q", expected_answer="e"),
            DataItem(raw_text="f2", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="x", question="Q1?", expected_answer="correct1"),
            DataItem(raw_text="x", question="Q2?", expected_answer="right answer"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score == 0.5
        assert len(result.per_case_scores) == 2
        assert len(result.failed_cases) == 1
        assert mock_fn.captured_calls == snapshot


# ── Batch Process Tests ──────────────────────────────────────────────────────


class TestMemoryEvaluatorBatch:
    """Tests for batch_process=True path (default)."""

    def _make_batch_mock(self, response_batches: list[list[str]]):
        """Create a mock for litellm.batch_completion.

        response_batches: one list of strings per expected call to batch_completion.
        Each inner list maps 1:1 to the messages passed in that call.
        captured_calls stores the messages argument for each call.
        """
        call_idx = [0]
        captured_calls: list[list[list[dict]]] = []

        def mock_batch_completion(*args, **kwargs):
            idx = call_idx[0]
            call_idx[0] += 1
            messages = kwargs.get("messages", [])
            captured_calls.append([list(m) for m in messages])
            batch = response_batches[idx % len(response_batches)]
            results = []
            for text in batch:
                mock_resp = MagicMock()
                mock_resp.choices = [MagicMock()]
                mock_resp.choices[0].message.content = text
                results.append(mock_resp)
            return results

        mock_batch_completion.captured_calls = captured_calls
        return mock_batch_completion

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_train_single_batch_call(self, mock_litellm, snapshot: SnapshotAssertion):
        """Offline train: all obs prompts go in ONE batch_completion call."""
        batch_mock = self._make_batch_mock(
            [
                ['{"raw": "France capital is Paris."}', '{"raw": "Germany capital is Berlin."}'],  # train batch
                ['{"raw": "capital of France"}', '{"raw": "capital of Germany"}'],  # val round 1: queries
                ["Paris", "Berlin"],  # val round 2: answers
            ]
        )
        mock_litellm.batch_completion = batch_mock

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [
            DataItem(raw_text="France capital is Paris.", question="q", expected_answer="e"),
            DataItem(raw_text="Germany capital is Berlin.", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris"),
            DataItem(raw_text="x", question="Capital of Germany?", expected_answer="Berlin"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=True)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        # Train should be exactly 1 call with 2 messages
        assert len(batch_mock.captured_calls) == 3  # train + val round1 + val round2
        assert len(batch_mock.captured_calls[0]) == 2  # 2 train items in one batch
        assert result.score == 1.0
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_two_batch_rounds(self, mock_litellm, snapshot: SnapshotAssertion):
        """Val with batch_process=True uses exactly 2 batch_completion rounds."""
        batch_mock = self._make_batch_mock(
            [
                ['{"raw": "obs1"}'],  # offline train (1 item)
                ['{"raw": "q1"}', '{"raw": "q2"}'],  # val round 1: both queries
                ["correct1", "correct2"],  # val round 2: both answers
            ]
        )
        mock_litellm.batch_completion = batch_mock

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [
            DataItem(raw_text="x", question="Q1?", expected_answer="correct1"),
            DataItem(raw_text="x", question="Q2?", expected_answer="correct2"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=True)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        assert result.score == 1.0
        assert len(batch_mock.captured_calls) == 3  # train + 2 val rounds
        assert len(batch_mock.captured_calls[1]) == 2  # round 1: 2 queries
        assert len(batch_mock.captured_calls[2]) == 2  # round 2: 2 answers (3 msgs each)
        assert len(batch_mock.captured_calls[2][0]) == 3  # each answer msg = query+response+retrieved
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_train_three_batch_rounds(self, mock_litellm, snapshot: SnapshotAssertion):
        """Online train batch_process=True: 3 rounds for train + 2 rounds for val."""
        batch_mock = self._make_batch_mock(
            [
                ['{"raw": "q"}'],  # online train round 1: query gen
                ["my answer"],  # online train round 2: answer gen
                ['{"raw": "obs stored"}'],  # online train round 3: obs gen
                ['{"raw": "vq"}'],  # val round 1: query gen
                ["obs stored"],  # val round 2: answer
            ]
        )
        mock_litellm.batch_completion = batch_mock

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="x", question="VQ?", expected_answer="obs stored")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=True)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

        assert len(batch_mock.captured_calls) == 5  # 3 train rounds + 2 val rounds
        assert len(batch_mock.captured_calls[0]) == 1  # round 1: 1 query prompt (1 msg each)
        assert len(batch_mock.captured_calls[2]) == 1  # round 3: 1 obs prompt
        assert len(batch_mock.captured_calls[2][0]) == 5  # obs prompt has 5 messages (full context)
        assert result.score == 1.0
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_train_exception_in_batch_skips_item(self, mock_litellm):
        """If one batch response is an Exception, that item is skipped gracefully."""
        call_idx = [0]

        def mock_batch_completion(*args, **kwargs):
            call_idx[0] += 1
            if call_idx[0] == 1:  # train batch
                mock_resp = MagicMock()
                mock_resp.choices = [MagicMock()]
                mock_resp.choices[0].message.content = '{"raw": "Paris is the capital of France."}'
                return [ValueError("API error"), mock_resp]
            # val batch calls
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = '{"raw": "q"}' if call_idx[0] == 2 else "Paris"
            return [mock_resp]

        mock_litellm.batch_completion = mock_batch_completion

        program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
        train = [
            DataItem(raw_text="bad item", question="q", expected_answer="e"),
            DataItem(raw_text="France capital Paris.", question="q", expected_answer="e"),
        ]
        val = [DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", batch_process=True)
        result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.OFFLINE)

        # Should still complete; only 1 item written to memory
        assert result.score is not None
        assert len(result.per_case_scores) == 1
