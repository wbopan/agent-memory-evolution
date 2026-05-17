"""Tests for mstar.benchmarks.state_bench."""

from __future__ import annotations

import pytest


def _sample_task_dict() -> dict:
    """A minimal but realistic task definition mirroring 1-return_partial_order's shape."""
    return {
        "task_id": "1-return_partial_order",
        "task_summary": (
            "**Task:** The customer wants to return only the headphones from a "
            "discounted three-item order.\n\n"
            "**Challenge:** Agent must redistribute SAVE20 across remaining items, "
            "not refund $249 flat."
        ),
        "task_requirements": [
            {
                "id": "preview_then_confirm",
                "kind": "must",
                "requirement": "Agent must preview before confirming.",
                "evidence": "conversation",
            },
            {
                "id": "no_full_cancel",
                "kind": "must_not",
                "requirement": "Cancel the entire order.",
                "evidence": "tool_calls",
            },
        ],
        "state_requirements": [
            {
                "entity_type": "orders",
                "record_key": "ORD-6001",
                "field": "status",
                "expected_value": "partially_returned",
            },
            {
                "entity_type": "orders",
                "record_key": "ORD-6001",
                "field": "refund_amount",
                "expected_value": 141,
            },
            {
                "entity_type": "orders",
                "record_key": "ORD-6001",
                "field": "restocking_fee",
                "expected_value": 37,
            },
        ],
        "opening_message": "Hi, I'd like to return the headphones from order ORD-6001.",
        "user_simulator": {"personality": "polite"},
    }


def test_render_d_full_contains_task_summary_text():
    from mstar.benchmarks.state_bench import _render_d_full

    out = _render_d_full(_sample_task_dict())
    assert "wants to return only the headphones" in out
    assert "redistribute SAVE20" in out


def test_render_d_full_lists_requirements_with_must_must_not():
    from mstar.benchmarks.state_bench import _render_d_full

    out = _render_d_full(_sample_task_dict())
    assert "MUST" in out
    assert "MUST NOT" in out
    assert "Agent must preview before confirming." in out
    assert "Cancel the entire order." in out


def test_render_d_full_state_lines_are_column_aligned():
    """State-requirement lines should align the `=` column."""
    from mstar.benchmarks.state_bench import _render_d_full

    out = _render_d_full(_sample_task_dict())
    lines = [ln for ln in out.splitlines() if ln.startswith("- orders.ORD-6001")]
    assert len(lines) == 3
    eq_columns = [ln.index("=") for ln in lines]
    assert len(set(eq_columns)) == 1, f"= column drifts across state lines: {eq_columns}"


def test_render_d_full_includes_opening_message():
    from mstar.benchmarks.state_bench import _render_d_full

    out = _render_d_full(_sample_task_dict())
    assert "Hi, I'd like to return the headphones" in out


def test_render_d_full_handles_minimal_task():
    """No requirements, no state — should not raise."""
    from mstar.benchmarks.state_bench import _render_d_full

    minimal = {
        "task_id": "x",
        "task_summary": "**Task:** Do nothing.",
        "task_requirements": [],
        "state_requirements": [],
        "opening_message": "hi",
        "user_simulator": {},
    }
    out = _render_d_full(minimal)
    assert "Do nothing." in out
    assert "hi" in out


def test_render_question_includes_opening_and_task_id():
    from mstar.benchmarks.state_bench import _render_question

    out = _render_question(_sample_task_dict())
    assert "1-return_partial_order" in out
    assert "Hi, I'd like to return the headphones" in out


def test_render_question_no_opening_message_falls_back_to_summary():
    from mstar.benchmarks.state_bench import _render_question

    task = {
        "task_id": "z",
        "task_summary": "**Task:** Help the user.",
        "opening_message": "",
    }
    out = _render_question(task)
    assert "z" in out
    assert "Help the user." in out


def test_split_train_val_is_deterministic_and_partitions():
    from mstar.benchmarks.state_bench import _split_train_val

    items = list(range(100))
    train, val = _split_train_val(items, val_size=50, seed=42)
    assert len(train) == 50 and len(val) == 50
    assert set(train) | set(val) == set(items)
    assert set(train) & set(val) == set()
    train2, val2 = _split_train_val(items, val_size=50, seed=42)
    assert train == train2 and val == val2
    train3, _ = _split_train_val(items, val_size=50, seed=43)
    assert train != train3


def test_split_train_val_honors_val_size():
    from mstar.benchmarks.state_bench import _split_train_val

    items = list(range(100))
    train, val = _split_train_val(items, val_size=70, seed=42)
    assert len(val) == 70 and len(train) == 30
    assert set(train) | set(val) == set(items)
    assert set(train) & set(val) == set()


def test_split_train_val_rejects_out_of_range():
    from mstar.benchmarks.state_bench import _split_train_val

    items = list(range(100))
    for bad in (0, 100, 150, -1):
        with pytest.raises(ValueError, match="val_size must be in"):
            _split_train_val(items, val_size=bad, seed=0)


def test_load_state_bench_synth_fixture():
    from mstar.benchmarks.state_bench import load_state_bench

    ds = load_state_bench(
        data_dir="tests/evolution/fixtures/state_bench_synth",
        domain="customer_support",
        seed=0,
    )
    # 10 train splits half/half -> 5 train + 5 val. Official test = 5.
    assert len(ds.train) == 5
    assert len(ds.val) == 5
    assert len(ds.test) == 5
    item = ds.train[0]
    assert "Synthetic task" in item.raw_text
    assert item.metadata["domain"] == "customer_support"
    assert item.metadata["task_id"].startswith("synth-")
    assert "task_path" in item.metadata


def test_load_state_bench_synth_supports_default_data_dir_override():
    """Loading without explicit data_dir uses Data/STATE-Bench by default; loader
    accepts a data_dir kwarg per the registered loader signature."""
    from mstar.benchmarks.state_bench import load_state_bench

    ds = load_state_bench(
        data_dir="tests/evolution/fixtures/state_bench_synth",
        domain="customer_support",
        seed=0,
    )
    # Same content via deterministic seed
    ids_train = [it.metadata["task_id"] for it in ds.train]
    assert all(tid.startswith("synth-") for tid in ids_train)


def test_mstar_kb_agent_injects_kb_text_after_existing_system_messages():
    """Agent inserts KB content after the last contiguous leading system message."""
    import sys
    sys.path.insert(0, "Data/STATE-Bench")

    from agents.mstar_kb_agent import MstarKBAgent

    agent = MstarKBAgent.__new__(MstarKBAgent)  # bypass __init__ which needs a client
    agent._kb_text = "FACT: refunds use SAVE20 redistribution."

    messages = [
        {"role": "system", "content": "You are an agent."},
        {"role": "user", "content": "Hi."},
    ]
    out = agent.prepare_conversation(messages)

    # Persona stays first; KB sits between persona and user; user retained.
    assert out[0] == messages[0]
    assert out[1]["role"] == "system"
    assert "SAVE20 redistribution" in out[1]["content"]
    assert out[2] == messages[1]


def test_mstar_kb_agent_no_kb_text_passes_through_unchanged():
    import sys
    sys.path.insert(0, "Data/STATE-Bench")

    from agents.mstar_kb_agent import MstarKBAgent

    agent = MstarKBAgent.__new__(MstarKBAgent)
    agent._kb_text = ""

    messages = [{"role": "system", "content": "Sys."}, {"role": "user", "content": "Hi."}]
    out = agent.prepare_conversation(messages)
    assert out == messages


def test_state_bench_infra_error_inherits_runtime_violation_error():
    """CRITICAL: must inherit RuntimeViolationError so evaluator infra-error path catches it."""
    from mstar.benchmarks.state_bench import StateBenchInfraError
    from mstar.evolution.evaluator import RuntimeViolationError

    assert issubclass(StateBenchInfraError, RuntimeViolationError)


def test_state_bench_val_scorer_dispatches_and_aggregates(monkeypatch):
    from mstar.benchmarks import state_bench as sb
    from mstar.evolution.types import DataItem

    calls: list[tuple] = []

    def fake_run(item, kb_text, task_model, reasoning_effort):
        calls.append((item.metadata["task_id"], kb_text, task_model, reasoning_effort))
        return (
            "transcript-" + item.metadata["task_id"],
            1.0,
            "rationale-" + item.metadata["task_id"],
        )

    monkeypatch.setattr(sb, "_run_single_task", fake_run)

    items = [
        DataItem(raw_text="", question="q1", expected_answer="", metadata={"task_id": "a"}),
        DataItem(raw_text="", question="q2", expected_answer="", metadata={"task_id": "b"}),
    ]
    scorer = sb.StateBenchValScorer(max_workers=2, task_timeout=10.0)
    out = scorer.score_batch(
        items=items,
        retrieved=["kb-a", "kb-b"],
        task_model="azure/gpt-5.1",
        instruction_response="ignored",
        always_on_knowledge="",
        reasoning_effort="low",
    )
    assert len(out) == 2
    assert {c[0] for c in calls} == {"a", "b"}
    transcripts = {t[0] for t in out}
    assert "transcript-a" in transcripts and "transcript-b" in transcripts


def test_state_bench_val_scorer_handles_per_task_exception(monkeypatch):
    from mstar.benchmarks import state_bench as sb
    from mstar.evolution.types import DataItem

    def fake_run(item, kb_text, task_model, reasoning_effort):
        if item.metadata["task_id"] == "boom":
            raise ValueError("simulated model failure")
        return ("ok", 1.0, "ok")

    monkeypatch.setattr(sb, "_run_single_task", fake_run)
    items = [
        DataItem(raw_text="", question="", expected_answer="", metadata={"task_id": "ok"}),
        DataItem(raw_text="", question="", expected_answer="", metadata={"task_id": "boom"}),
    ]
    scorer = sb.StateBenchValScorer(max_workers=2, task_timeout=10.0)
    out = scorer.score_batch(
        items=items,
        retrieved=["", ""],
        task_model="azure/gpt-5.1",
        instruction_response="",
        always_on_knowledge="",
        reasoning_effort=None,
    )
    failed = [r for r in out if r[1] == 0.0]
    assert len(failed) == 1
    assert "simulated model failure" in failed[0][0]


def test_state_bench_val_scorer_propagates_infra_error(monkeypatch):
    from mstar.benchmarks import state_bench as sb
    from mstar.evolution.types import DataItem

    def fake_run(item, kb_text, task_model, reasoning_effort):
        raise sb.StateBenchInfraError("proxy down")

    monkeypatch.setattr(sb, "_run_single_task", fake_run)
    items = [DataItem(raw_text="", question="", expected_answer="", metadata={"task_id": "x"})]
    scorer = sb.StateBenchValScorer(max_workers=1, task_timeout=10.0)

    with pytest.raises(sb.StateBenchInfraError):
        scorer.score_batch(
            items=items,
            retrieved=[""],
            task_model="azure/gpt-5.1",
            instruction_response="",
            always_on_knowledge="",
            reasoning_effort=None,
        )


def test_ensure_state_bench_initialized_is_idempotent():
    """Initializer should be safely callable multiple times."""
    import importlib

    from mstar.benchmarks import state_bench as sb

    sb._ensure_state_bench_initialized()
    sb._ensure_state_bench_initialized()
    mod = importlib.import_module("state_bench")
    assert mod is not None


def test_looks_like_infra_error_classifies_openai_exceptions():
    import openai

    from mstar.benchmarks.state_bench import _looks_like_infra_error

    # APIConnectionError takes message+request, but it's enough to construct via __new__ for issubclass test:
    assert _looks_like_infra_error(openai.APIConnectionError.__new__(openai.APIConnectionError)) is True
    assert _looks_like_infra_error(openai.APITimeoutError.__new__(openai.APITimeoutError)) is True
    assert _looks_like_infra_error(ValueError("not infra")) is False
    assert _looks_like_infra_error(KeyError("not infra")) is False


def test_build_rationale_includes_task_summary_state_reasoning_and_failed_requirements():
    """Rationale must carry enough signal to drive evolution: per-axis verdict,
    judge reasoning, per-requirement failures, and the agent's final turn."""
    from types import SimpleNamespace

    from mstar.benchmarks.state_bench import _build_rationale

    task = SimpleNamespace(
        task_id="1-return_partial_order",
        task_summary="**Task:** Refund only the headphones, redistribute SAVE20.",
    )
    trajectory = SimpleNamespace(
        task_completion_pass=0,
        state_requirements_score=SimpleNamespace(
            score=0,
            reasoning="orders.ORD-6001.refund_amount expected 141 but was 249.",
        ),
        task_requirements_score=SimpleNamespace(
            score=0,
            reasoning="Agent skipped the preview step.",
            details=[
                {
                    "id": "preview_then_confirm",
                    "passed": False,
                    "reasoning": "process_return called with confirm=True without prior preview.",
                },
                {"id": "no_full_cancel", "passed": True, "reasoning": "n/a"},
            ],
        ),
        efficiency=SimpleNamespace(turns=3, tool_calls=4),
        conversation=[
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "I've processed your return for $249."},
        ],
    )

    out = _build_rationale(task, trajectory)
    # Verdict + axis breakdown
    assert "FAIL" in out
    assert "state_pass=0" in out and "task_reqs_pass=0" in out and "completion=0" in out
    assert "turns=3" in out and "tool_calls=4" in out
    # Task summary
    assert "redistribute SAVE20" in out
    # State reasoning
    assert "expected 141 but was 249" in out
    # Task-requirements reasoning + per-requirement failure (with explanation)
    assert "skipped the preview step" in out
    assert "preview_then_confirm" in out
    assert "without prior preview" in out
    # Passed requirements should NOT pollute the failed-requirements section
    assert "no_full_cancel" not in out.split("Failed requirements:")[1] if "Failed requirements:" in out else True
    # Final agent turn
    assert "I've processed your return" in out


def test_build_rationale_handles_pass_case_without_failed_requirements_block():
    from types import SimpleNamespace

    from mstar.benchmarks.state_bench import _build_rationale

    task = SimpleNamespace(task_id="ok", task_summary="**Task:** Easy.")
    trajectory = SimpleNamespace(
        task_completion_pass=1,
        state_requirements_score=SimpleNamespace(score=1, reasoning="all matched"),
        task_requirements_score=SimpleNamespace(score=1, reasoning="agent did right", details=[]),
        efficiency=SimpleNamespace(turns=2, tool_calls=1),
        conversation=[{"role": "assistant", "content": "Done."}],
    )
    out = _build_rationale(task, trajectory)
    assert "PASS" in out
    assert "Failed requirements:" not in out


def test_build_compact_transcript_keeps_size_under_2kb_for_typical_task():
    """The reflector must NOT see the full Responses API JSON.

    A typical 4-turn STATE-Bench task with full JSON is ~16-30 KB; the compact
    transcript should be ≤2 KB (≤500 tokens), so 3 failed cases × transcript
    fit easily into a 25K-token reflection prompt.
    """
    from types import SimpleNamespace

    from mstar.benchmarks.state_bench import _build_compact_transcript

    task = SimpleNamespace(task_id="123-cs_return")
    trajectory = SimpleNamespace(
        conversation=[
            {"role": "user", "content": "Hi I want to return ORD-6001."},
            {
                "role": "assistant",
                "content": "Let me look that up." + " details" * 100,  # bloat
                "tool_calls": [{"name": "get_order", "arguments": {"order_id": "ORD-6001"}}],
            },
            {"role": "tool", "content": "ORD-6001 has 3 items..." + " bloat" * 200},
            {"role": "user", "content": "Just refund the headphones."},
            {
                "role": "assistant",
                "content": "Processing now." + " more" * 80,
                "tool_calls": [{"name": "process_return", "arguments": {}}],
            },
        ]
    )
    out = _build_compact_transcript(task, trajectory)
    assert "123-cs_return" in out
    assert "User T1" in out and "User T2" in out
    assert "Agent T1" in out and "Agent T2" in out
    assert "get_order" in out
    assert "process_return" in out
    # Tight bound — the compact form should NEVER blow up to 16+ KB even with
    # very long content (each line capped at ~150 chars).
    assert len(out) < 2000, f"compact transcript too large: {len(out)} chars"


@pytest.mark.llm
def test_state_bench_real_task_passes_or_scores_zero():
    """Real LLM smoke test: run one customer_support task via Azure proxy.

    Requires:
    - Data/STATE-Bench/ vendored (Task 0)
    - azure-openai-proxy running on http://127.0.0.1:4000
    - Logged into ``az`` CLI (AzureCliCredential).
    """
    from mstar.benchmarks.state_bench import StateBenchValScorer, load_state_bench

    ds = load_state_bench(domain="customer_support", seed=0)
    item = ds.val[0]
    scorer = StateBenchValScorer(max_workers=1, task_timeout=300.0)
    out = scorer.score_batch(
        items=[item],
        retrieved=[""],
        task_model="azure/gpt-5.1",
        instruction_response="",
        always_on_knowledge="",
        reasoning_effort="low",
    )
    assert len(out) == 1
    transcript, score, _rationale = out[0]
    # Don't assert score==1 — just that the harness ran and returned a valid score.
    assert score in (0.0, 1.0)
    assert isinstance(transcript, str) and len(transcript) > 0
