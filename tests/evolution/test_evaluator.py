"""Tests for evolution/evaluator.py — scorers, JSON parsing, evaluation pipelines.

Key verification points from the design document:
- Online train: messages accumulate across steps (multi-turn conversation)
- Validation: only Step 1 + Step 2, memory.write NOT called
- Memory lifecycle: re-instantiation → empty memory (state isolation)
"""

import textwrap
import time
from unittest.mock import MagicMock, patch

import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import (
    ExactMatchScorer,
    LLMJudgeScorer,
    MemoryEvaluator,
    RuntimeViolationError,
    _guarded_read,
    _guarded_write,
    _parse_json_from_llm,
)
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import DataItem, KBProgram

_TEST_TOOLKIT_CONFIG = ToolkitConfig(llm_model="test/model")

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
    def test_sends_system_and_user_message(self, mock_litellm):
        """LLMJudgeScorer sends a minimal system message followed by the user prompt."""
        mock_resp = MagicMock()
        mock_resp.choices = [MagicMock()]
        mock_resp.choices[0].message.content = "1"
        mock_litellm.completion.return_value = mock_resp

        scorer = LLMJudgeScorer(model="mock/model")
        score = scorer("Paris", "Paris")

        assert score == 1.0
        call_kwargs = mock_litellm.completion.call_args.kwargs
        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "Paris" in messages[1]["content"]


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


# ── Batch mock helper ────────────────────────────────────────────────────────


def _make_batch_mock(response_batches: list[list[str]]):
    """Create a mock for litellm.completion that mimics the old batch_completion interface.

    response_batches: one list of strings per logical batch (group of completion calls).
    Each inner list maps 1:1 to the messages in that batch.
    captured_calls stores the messages grouped by batch (same structure as before).

    The evaluator now calls litellm.completion per-message via a thread pool.
    This mock tracks call boundaries by counting: when len(current_batch_calls)
    matches the expected batch size, it starts a new batch group.
    """
    import threading

    lock = threading.Lock()
    # Flat queue of all responses in order
    flat_responses: list[str] = []
    batch_sizes: list[int] = []
    for batch in response_batches:
        flat_responses.extend(batch)
        batch_sizes.append(len(batch))

    response_idx = [0]
    # Track captured calls grouped by batch
    captured_calls: list[list[list[dict]]] = []
    current_group: list[list[dict]] = []
    batch_cursor = [0]  # which batch group we're filling

    def mock_completion(*args, **kwargs):
        with lock:
            idx = response_idx[0]
            response_idx[0] += 1
            messages = kwargs.get("messages", [])
            current_group.append(list(messages))

            # Check if we've filled the current batch
            if batch_cursor[0] < len(batch_sizes):
                expected_size = batch_sizes[batch_cursor[0]]
                if len(current_group) >= expected_size:
                    captured_calls.append(list(current_group))
                    current_group.clear()
                    batch_cursor[0] += 1

            text = flat_responses[idx % len(flat_responses)]
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = text
            return mock_resp

    mock_completion.captured_calls = captured_calls
    return mock_completion


# ── Memory lifecycle tests ──────────────────────────────────────────────────


class TestMemoryLifecycle:
    def test_initial_program_instantiates(self):
        """Initial memory program template can be instantiated."""
        from programmaticmemory.evolution.sandbox import CompiledProgram, compile_kb_program
        from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig

        result = compile_kb_program(INITIAL_KB_PROGRAM)
        assert isinstance(result, CompiledProgram)
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        memory = result.kb_cls(tk)
        assert memory is not None
        tk.close()

    def test_write_then_read_returns_content(self):
        """Write followed by read should return the written content."""
        from programmaticmemory.evolution.sandbox import CompiledProgram, compile_kb_program
        from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig

        result = compile_kb_program(INITIAL_KB_PROGRAM)
        assert isinstance(result, CompiledProgram)
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        memory = result.kb_cls(tk)
        memory.write(result.ki_cls(summary="The sky is blue."), "The sky is blue.")
        output = memory.read(result.query_cls(raw="sky"))
        assert "The sky is blue." in output
        tk.close()

    def test_reinstantiation_gives_empty_memory(self):
        """Re-instantiating Memory should produce an empty store (no state leak)."""
        from programmaticmemory.evolution.sandbox import CompiledProgram, compile_kb_program
        from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig

        result = compile_kb_program(INITIAL_KB_PROGRAM)
        assert isinstance(result, CompiledProgram)
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))

        # First instance: write data
        mem1 = result.kb_cls(tk)
        mem1.write(result.ki_cls(summary="secret data"), "secret data raw")
        output1 = mem1.read(result.query_cls(raw="anything"))
        assert "secret data" in output1

        # Second instance: should be empty
        mem2 = result.kb_cls(tk)
        output2 = mem2.read(result.query_cls(raw="anything"))
        assert "secret data" not in output2
        tk.close()


# ── Offline Pipeline Tests ─────────────────────────────────────────────────


class TestMemoryEvaluatorOffline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_basic_offline_evaluation(self, mock_litellm, snapshot: SnapshotAssertion):
        """Offline: batch ingest → query → answer → score."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "France capital is Paris."}', '{"summary": "Germany capital is Berlin."}'],  # train batch
                ['{"raw": "capital of France"}', '{"raw": "capital of Germany"}'],  # val round 1: queries
                ["Paris", "Berlin"],  # val round 2: answers
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [
            DataItem(raw_text="France capital is Paris.", question="q", expected_answer="e"),
            DataItem(raw_text="Germany capital is Berlin.", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris"),
            DataItem(raw_text="x", question="Capital of Germany?", expected_answer="Berlin"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        # Train should be exactly 1 call with 2 messages
        assert len(batch_mock.captured_calls) == 3  # train + val round1 + val round2
        assert len(batch_mock.captured_calls[0]) == 2  # 2 train items in one batch
        assert result.score == 1.0
        assert len(result.per_case_scores) == 2
        assert result.failed_cases == []
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_wrong_answer(self, mock_litellm, snapshot: SnapshotAssertion):
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "The capital of France is Paris."}'],  # train batch
                ['{"raw": "capital"}'],  # val round 1: query
                ["London"],  # val round 2: wrong answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="The capital of France is Paris.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What is the capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 0.0
        assert len(result.failed_cases) == 1
        assert result.failed_cases[0].output == "London"
        assert batch_mock.captured_calls == snapshot

    def test_compile_error_returns_zero(self):
        program = KBProgram(source_code="invalid python {{{}}")
        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(
            program,
            [DataItem(raw_text="x", question="q", expected_answer="a")],
            [DataItem(raw_text="x", question="q", expected_answer="a")],
        )
        assert result.score == 0.0
        assert any("Compile error" in log for log in result.logs)

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_val_uses_multiturn(self, mock_litellm, snapshot: SnapshotAssertion):
        """Val uses exactly 2 batch_completion rounds with multi-turn messages."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "obs1"}'],  # offline train (1 item)
                ['{"raw": "q1"}', '{"raw": "q2"}'],  # val round 1: both queries
                ["correct1", "correct2"],  # val round 2: both answers
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [
            DataItem(raw_text="x", question="Q1?", expected_answer="correct1"),
            DataItem(raw_text="x", question="Q2?", expected_answer="correct2"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 1.0
        assert len(batch_mock.captured_calls) == 3  # train + 2 val rounds
        assert len(batch_mock.captured_calls[1]) == 2  # round 1: 2 queries
        assert len(batch_mock.captured_calls[2]) == 2  # round 2: 2 answers (3 msgs each)
        assert len(batch_mock.captured_calls[2][0]) == 4  # system + query + response + retrieved
        assert batch_mock.captured_calls == snapshot


# ── Always-on Knowledge injection ─────────────────────────────────────────


class TestAlwaysOnKnowledgeInEvaluator:
    """Verify ALWAYS_ON_KNOWLEDGE appears inside <retrieved_memory> in evaluator LLM calls."""

    PROGRAM_WITH_AOK = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "Extract the key fact."
INSTRUCTION_QUERY = "Ask for the fact."
INSTRUCTION_RESPONSE = "Answer concisely."
ALWAYS_ON_KNOWLEDGE = "Always respond in English. Be precise."

@dataclass
class KnowledgeItem:
    summary: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []
    def write(self, item, raw_text=""):
        self.store.append(item.summary)
    def read(self, query):
        return " ".join(self.store) if self.store else "No information stored."
"""

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_always_on_knowledge_in_retrieved_memory(self, mock_litellm, snapshot: SnapshotAssertion):
        """Non-empty ALWAYS_ON_KNOWLEDGE should appear inside <retrieved_memory> tags."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "The sky is blue."}'],  # train batch: ki
                ['{"raw": "sky color"}'],  # val round 1: query
                ["blue"],  # val round 2: answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=self.PROGRAM_WITH_AOK)
        train = [DataItem(raw_text="The sky is blue.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What color is the sky?", expected_answer="blue")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 1.0

        # The retrieved memory prompt (val round 2) should contain the always-on knowledge
        val_answer_call = batch_mock.captured_calls[2]  # 3rd batch call = val answer round
        retrieved_prompt = val_answer_call[0][-1]["content"]  # last message in conversation
        assert "Always respond in English. Be precise." in retrieved_prompt
        assert "<retrieved_memory>" in retrieved_prompt
        # AOK should appear before the actual retrieved content
        aok_pos = retrieved_prompt.index("Always respond in English.")
        retrieved_pos = retrieved_prompt.index("The sky is blue.")
        assert aok_pos < retrieved_pos

        assert batch_mock.captured_calls == snapshot


# ── Online Pipeline Tests ──────────────────────────────────────────────────


class TestMemoryEvaluatorOnline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_train_messages_accumulate(self, mock_litellm, snapshot: SnapshotAssertion):
        """Online train: 3 batch rounds for train + 2 for val, messages grow across rounds."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "q"}'],  # online train round 1: query gen
                ["my answer"],  # online train round 2: answer gen
                ['{"summary": "obs stored"}'],  # online train round 3: ki gen
                ['{"raw": "vq"}'],  # val round 1: query gen
                ["obs stored"],  # val round 2: answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="obs stored")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert len(batch_mock.captured_calls) == 5  # 3 train rounds + 2 val rounds
        # Round 1: 1 query prompt (1 msg each)
        assert len(batch_mock.captured_calls[0]) == 1
        assert len(batch_mock.captured_calls[0][0]) == 2  # system + 1 user message
        # Round 2: 4 messages (system + user + assistant + user)
        assert len(batch_mock.captured_calls[1][0]) == 4
        assert batch_mock.captured_calls[1][0][0]["role"] == "system"
        assert batch_mock.captured_calls[1][0][1]["role"] == "user"
        assert batch_mock.captured_calls[1][0][2]["role"] == "assistant"
        assert batch_mock.captured_calls[1][0][3]["role"] == "user"
        assert "<retrieved_memory>" in batch_mock.captured_calls[1][0][3]["content"]
        # Round 3: 6 messages (system + user + asst + user + asst + user)
        assert len(batch_mock.captured_calls[2][0]) == 6
        assert batch_mock.captured_calls[2][0][4]["role"] == "assistant"  # answer
        assert batch_mock.captured_calls[2][0][5]["role"] == "user"  # ki gen with feedback
        assert result.score == 1.0
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step1_output_parses_to_query(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 1 mock output should be parseable into a Query dataclass."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "parsed query"}'],  # train round 1
                ["answer"],  # train round 2
                ['{"summary": "obs"}'],  # train round 3
                ['{"raw": "vq"}'],  # val round 1
                ["va"],  # val round 2
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        # No parse errors in logs means query was parsed successfully
        assert not any("query parse failed" in log for log in result.logs)
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step3_output_parses_to_knowledge_item(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 3 mock output should be parseable into a KnowledgeItem dataclass."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "q"}'],  # train round 1
                ["answer"],  # train round 2
                ['{"summary": "parsed knowledge item value"}'],  # train round 3: ki
                ['{"raw": "vq"}'],  # val round 1
                ["va"],  # val round 2
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert not any("knowledge item parse failed" in log for log in result.logs)
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_write_called_and_memory_updates(self, mock_litellm, snapshot: SnapshotAssertion):
        """After Step 3+4, memory.write should be called and memory state should update."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "q"}'],  # train round 1
                ["answer"],  # train round 2
                ['{"summary": "stored via online"}'],  # train round 3: ki written
                ['{"raw": "vq"}'],  # val round 1
                ["stored via online"],  # val round 2: answer matches
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="stored via online")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 1.0
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_online_step3_includes_feedback_and_ground_truth(self, mock_litellm, snapshot: SnapshotAssertion):
        """Step 3 prompt must include evaluation result and ground truth."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "q"}'],  # train round 1
                ["wrong answer"],  # train round 2: incorrect answer
                ['{"summary": "obs"}'],  # train round 3
                ['{"raw": "vq"}'],  # val round 1
                ["va"],  # val round 2
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="", question="Q?", expected_answer="correct answer")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        evaluator.evaluate(program, train, val)

        # Round 3 (call index 2) should contain feedback in the last user message
        step3_messages = batch_mock.captured_calls[2][0]
        step3_user_prompt = step3_messages[-1]["content"]
        assert "Ground truth" in step3_user_prompt
        assert "correct answer" in step3_user_prompt
        assert "incorrect" in step3_user_prompt  # evaluation result
        assert batch_mock.captured_calls == snapshot


# ── Validation Pipeline Tests ──────────────────────────────────────────────


class TestValidationPipeline:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_only_step1_and_step2(self, mock_litellm, snapshot: SnapshotAssertion):
        """Validation should only do Step 1 (query gen) + Step 2 (answer), no Step 3/4."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "fact"}'],  # train ki
                ['{"raw": "query"}'],  # val round 1: query
                ["answer"],  # val round 2: answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Q?", expected_answer="answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        evaluator.evaluate(program, train, val)

        # Should be exactly 3 batch_completion calls: 1 train ki + 2 val rounds
        assert len(batch_mock.captured_calls) == 3
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_does_not_call_write(self, mock_litellm, snapshot: SnapshotAssertion):
        """memory.write must NOT be called during validation phase."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "q"}'],  # online train round 1
                ["ans"],  # online train round 2
                ['{"summary": "obs"}'],  # online train round 3
                ['{"raw": "vq"}'],  # val round 1
                ["va"],  # val round 2
            ]
        )
        mock_litellm.completion = batch_mock

        # Use a KB program that tracks write calls
        tracking_program = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    summary: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []
        self.write_log = []

    def write(self, item, raw_text=""):
        self.store.append(item.summary)
        self.write_log.append(item.summary)
        self.toolkit.logger.log(f"WRITE_CALLED:{item.summary}")

    def read(self, query):
        return " ".join(self.store) if self.store else "empty"
"""
        program = KBProgram(source_code=tracking_program)
        train = [DataItem(raw_text="", question="Q?", expected_answer="A")]
        val = [DataItem(raw_text="", question="VQ?", expected_answer="va")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        evaluator.evaluate(program, train, val)

        # 5 batch calls: 3 train rounds + 2 val rounds
        assert len(batch_mock.captured_calls) == 5
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_conversation_history_in_failed_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """Failed cases should include the full multi-turn conversation history."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "fact"}'],  # train ki
                ['{"raw": "query"}'],  # val round 1: query
                ["wrong answer"],  # val round 2: wrong answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Q?", expected_answer="correct")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert len(result.failed_cases) == 1
        fc = result.failed_cases[0]
        # Should have 4 messages: user(query) + asst(query json) + user(retrieved) + asst(answer)
        assert len(fc.conversation_history) == 4
        roles = [m["role"] for m in fc.conversation_history]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_multiturn_messages_structure(self, mock_litellm, snapshot: SnapshotAssertion):
        """Val round 2 batch call should contain multi-turn messages per item."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "fact"}'],  # train ki
                ['{"raw": "my query"}'],  # val round 1: query
                ["the answer"],  # val round 2: answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="What is X?", expected_answer="the answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        evaluator.evaluate(program, train, val)

        # Val round 2 (call index 2): each item should have 4 messages (system + 3 turns)
        step2_msgs = batch_mock.captured_calls[2][0]
        assert len(step2_msgs) == 4
        assert step2_msgs[0]["role"] == "system"
        assert "What is X?" in step2_msgs[1]["content"]  # query gen mentions question
        assert step2_msgs[2]["content"] == '{"raw": "my query"}'  # assistant's query
        assert "<retrieved_memory>" in step2_msgs[3]["content"]  # retrieved memory prompt
        assert batch_mock.captured_calls == snapshot


# ── Edge Cases ──────────────────────────────────────────────────────────────


class TestEvaluatorEdgeCases:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_knowledge_item_generation_failure_skips_item(self, mock_litellm, snapshot: SnapshotAssertion):
        """Offline: if knowledge item generation fails (bad JSON), item is skipped but eval continues."""
        batch_mock = _make_batch_mock(
            [
                ["not valid json at all"],  # train ki fails
                ['{"raw": "query"}'],  # val round 1: query
                ["some answer"],  # val round 2: answer
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="q?", expected_answer="answer")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score is not None
        assert len(result.per_case_scores) == 1
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_query_generation_failure_scores_zero(self, mock_litellm, snapshot: SnapshotAssertion):
        """If query generation fails during val, that item scores 0."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "fact"}'],  # train ki
                ["not valid json"],  # val round 1: query gen fails
                [],  # val round 2: no valid slots, empty batch
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="fact", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="q?", expected_answer="a")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 0.0
        assert len(result.failed_cases) == 1
        assert batch_mock.captured_calls == snapshot

    def test_empty_val_data(self, snapshot: SnapshotAssertion):
        """Empty val data should return score 0 without crashing."""
        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        with patch("programmaticmemory.evolution.evaluator.litellm") as mock_litellm:
            batch_mock = _make_batch_mock(
                [
                    ['{"summary": "x"}'],  # train ki
                ]
            )
            mock_litellm.completion = batch_mock
            result = evaluator.evaluate(
                program,
                [DataItem(raw_text="x", question="q", expected_answer="a")],
                [],
            )
        assert result.score == 0.0
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_success_cases_collected(self, mock_litellm, snapshot: SnapshotAssertion):
        """Correct answers should be collected as success_cases."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "f1"}'],  # train ki
                ['{"raw": "q1"}', '{"raw": "q2"}'],  # val round 1: queries
                ["correct1", "wrong"],  # val round 2: item 1 correct, item 2 wrong
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="f1", question="q", expected_answer="e")]
        val = [
            DataItem(raw_text="x", question="Q1?", expected_answer="correct1"),
            DataItem(raw_text="x", question="Q2?", expected_answer="right answer"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert len(result.success_cases) == 1
        assert result.success_cases[0].question == "Q1?"
        assert result.success_cases[0].score == 1.0
        assert len(result.success_cases[0].conversation_history) == 4
        assert len(result.failed_cases) == 1
        assert result.failed_cases[0].question == "Q2?"
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_multiple_val_items(self, mock_litellm, snapshot: SnapshotAssertion):
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "f1"}', '{"summary": "f2"}'],  # train ki x2
                ['{"raw": "q1"}', '{"raw": "q2"}'],  # val round 1: both queries
                ["correct1", "wrong"],  # val round 2: item 1 correct, item 2 wrong
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [
            DataItem(raw_text="f1", question="q", expected_answer="e"),
            DataItem(raw_text="f2", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="x", question="Q1?", expected_answer="correct1"),
            DataItem(raw_text="x", question="Q2?", expected_answer="right answer"),
        ]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 0.5
        assert len(result.per_case_scores) == 2
        assert len(result.failed_cases) == 1
        assert batch_mock.captured_calls == snapshot

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_offline_train_exception_in_batch_skips_item(self, mock_litellm):
        """If one completion call raises, that item is skipped gracefully."""
        import threading

        lock = threading.Lock()
        call_idx = [0]

        def mock_completion(*args, **kwargs):
            with lock:
                call_idx[0] += 1
                idx = call_idx[0]
            if idx == 1:  # first train item: raise
                raise ValueError("API error")
            if idx == 2:  # second train item
                mock_resp = MagicMock()
                mock_resp.choices = [MagicMock()]
                mock_resp.choices[0].message.content = '{"summary": "Paris is the capital of France."}'
                return mock_resp
            # val calls
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = '{"raw": "q"}' if idx == 3 else "Paris"
            return mock_resp

        mock_litellm.completion = mock_completion

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [
            DataItem(raw_text="bad item", question="q", expected_answer="e"),
            DataItem(raw_text="France capital Paris.", question="q", expected_answer="e"),
        ]
        val = [DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        # Should still complete; only 1 item written to memory
        assert result.score is not None
        assert len(result.per_case_scores) == 1


# ── Guarded Write/Read Tests ─────────────────────────────────────────────


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
            _guarded_write(memory, MagicMock(), raw_text="")


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


# ── Runtime Violation Early Abort Tests ────────────────────────────────────

OVERSIZED_READ_PROGRAM = textwrap.dedent("""\
    from dataclasses import dataclass

    INSTRUCTION_KNOWLEDGE_ITEM = ""
    INSTRUCTION_QUERY = ""
    INSTRUCTION_RESPONSE = ""
    ALWAYS_ON_KNOWLEDGE = ""

    @dataclass
    class KnowledgeItem:
        content: str

    @dataclass
    class Query:
        question: str

    class KnowledgeBase:
        def __init__(self, toolkit):
            pass

        def write(self, item, raw_text=""):
            pass

        def read(self, query):
            return "x" * 5000
""")


class TestRuntimeViolationEarlyAbort:
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_oversized_read_aborts_eval(self, mock_litellm):
        """Eval aborts on first kb.read() returning >1000 chars."""
        batch_mock = _make_batch_mock(
            [
                ['{"content": "hello"}'],  # train ki batch
                ['{"question": "what?"}'],  # val round 1: query gen
                # No round 2 needed — abort happens before answer generation
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=OVERSIZED_READ_PROGRAM)
        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        train = [DataItem(raw_text="hello", question="q", expected_answer="a")]
        val = [DataItem(raw_text="x", question="what?", expected_answer="x")]

        result = evaluator.evaluate(program, train, val)

        assert result.score == 0.0
        assert result.runtime_violation is not None
        assert "5000" in result.runtime_violation


# ── Pluggable Val Scorer Tests ─────────────────────────────────────────────


class TestValScorerIntegration:
    """Tests for the pluggable val_scorer path."""

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_receives_retrieved_memory(self, mock_litellm):
        """When val_scorer is set, it receives KB-retrieved strings instead of LLM answering."""
        received_items = []
        received_retrieved = []

        class CapturingScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response, always_on_knowledge):
                received_items.extend(items)
                received_retrieved.extend(retrieved)
                return [("custom_answer", 0.75)] * len(items)

        batch_mock = _make_batch_mock(
            [
                ['{"summary": "The sky is blue."}'],  # train: ki generation
                ['{"raw": "sky query"}'],  # val round 1: query generation
                # No round 2! val_scorer handles scoring, not LLM answer generation.
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [
            DataItem(raw_text="The sky is blue.", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="", question="What color is the sky?", expected_answer="blue"),
        ]

        evaluator = MemoryEvaluator(
            task_model="mock/model",
            toolkit_config=_TEST_TOOLKIT_CONFIG,
            val_scorer=CapturingScorer(),
        )
        result = evaluator.evaluate(program, train, val)

        # val_scorer was called with the right data
        assert len(received_items) == 1
        assert received_items[0].question == "What color is the sky?"
        assert "The sky is blue." in received_retrieved[0]  # KB has the stored text
        # Score comes from val_scorer, not default scorer
        assert result.score == 0.75
        assert result.per_case_outputs == ["custom_answer"]
        # Only 2 batch calls (train ki + val query), NOT 3 (no val answer generation)
        assert len(batch_mock.captured_calls) == 2

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_path_includes_conversation_history(self, mock_litellm):
        """ValScorer path should include retrieval conversation in failed_cases."""

        class FailingScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response, always_on_knowledge):
                return [("episode transcript: FAIL", 0.0)] * len(items)

        batch_mock = _make_batch_mock(
            [
                ['{"summary": "The sky is blue."}'],  # train: ki generation
                ['{"raw": "sky query"}'],  # val round 1: query generation
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [
            DataItem(raw_text="The sky is blue.", question="q", expected_answer="e"),
        ]
        val = [
            DataItem(raw_text="", question="What color is the sky?", expected_answer="blue"),
        ]

        evaluator = MemoryEvaluator(
            task_model="mock/model",
            toolkit_config=_TEST_TOOLKIT_CONFIG,
            val_scorer=FailingScorer(),
        )
        result = evaluator.evaluate(program, train, val)

        assert len(result.failed_cases) == 1
        fc = result.failed_cases[0]
        # Should have 3 messages: user(query prompt) + asst(query json) + user(retrieved prompt)
        # (no 4th assistant message — ValScorer provides output via episode transcript, not LLM answer)
        assert len(fc.conversation_history) == 3
        roles = [m["role"] for m in fc.conversation_history]
        assert roles == ["user", "assistant", "user"]
        assert fc.output == "episode transcript: FAIL"

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_none_uses_default_path(self, mock_litellm):
        """When val_scorer is None, existing LLM answer + scorer path is used (3 batch calls)."""
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "Paris is capital of France."}'],
                ['{"raw": "capital of France"}'],
                ["Paris"],
            ]
        )
        mock_litellm.completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="Paris is capital.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 1.0
        assert len(batch_mock.captured_calls) == 3  # train + val query + val answer


class TestEvaluateDual:
    """Tests for MemoryEvaluator.evaluate_dual — single train ingestion, dual val eval."""

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_evaluate_dual_returns_two_results(self, mock_litellm):
        """evaluate_dual returns (score_result, reflect_result) with separate scores."""
        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="Alice likes cats.", question="", expected_answer="")]
        val_score = [DataItem(raw_text="", question="What does Alice like?", expected_answer="cats")]
        val_reflect = [DataItem(raw_text="", question="Who likes cats?", expected_answer="Alice")]

        # 1 train KI generation + 1 score query + 1 score answer + 1 reflect query + 1 reflect answer = 5
        batch_mock = _make_batch_mock(
            [
                ['{"summary": "Alice likes cats.", "observations": ["Alice likes cats"]}'],  # train KI
                ['{"raw": "Alice cats"}'],  # score query
                ["cats"],  # score answer
                ['{"raw": "cats Alice"}'],  # reflect query
                ["Alice"],  # reflect answer
            ]
        )
        mock_litellm.completion = batch_mock

        evaluator = MemoryEvaluator(
            scorer=ExactMatchScorer(), task_model="test/model", toolkit_config=_TEST_TOOLKIT_CONFIG
        )
        score_result, reflect_result = evaluator.evaluate_dual(program, train, val_score, val_reflect)

        assert score_result.score >= 0.0
        assert reflect_result.score >= 0.0
        # Both should have per_case data
        assert len(score_result.per_case_scores) == 1
        assert len(reflect_result.per_case_scores) == 1

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_evaluate_dual_shares_train_ingestion(self, mock_litellm):
        """Both val sets query the same KB built from a single train ingestion."""
        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="Bob has a dog named Rex.", question="", expected_answer="")]
        val_score = [DataItem(raw_text="", question="What pet does Bob have?", expected_answer="dog")]
        val_reflect = [DataItem(raw_text="", question="What is the dog's name?", expected_answer="Rex")]

        batch_mock = _make_batch_mock(
            [
                ['{"summary": "Bob has a dog named Rex.", "observations": ["Bob has dog Rex"]}'],
                ['{"raw": "Bob pet dog"}'],
                ["dog"],
                ['{"raw": "dog name Rex"}'],
                ["Rex"],
            ]
        )
        mock_litellm.completion = batch_mock

        evaluator = MemoryEvaluator(
            scorer=ExactMatchScorer(), task_model="test/model", toolkit_config=_TEST_TOOLKIT_CONFIG
        )
        score_result, reflect_result = evaluator.evaluate_dual(program, train, val_score, val_reflect)

        # Train ingestion happens once (1 KI generation call), then 2+2 val calls
        # Total: 5 LLM calls, not 7 (which would be 2 separate evaluate() calls)
        assert score_result is not None
        assert reflect_result is not None

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_evaluate_dual_compile_error_returns_empty_pair(self, mock_litellm):
        """Compile failure returns two zero-score results."""
        program = KBProgram(source_code="invalid python {{{{")
        evaluator = MemoryEvaluator(
            scorer=ExactMatchScorer(), task_model="test/model", toolkit_config=_TEST_TOOLKIT_CONFIG
        )
        score_result, reflect_result = evaluator.evaluate_dual(program, [], [], [])

        assert score_result.score == 0.0
        assert reflect_result.score == 0.0
