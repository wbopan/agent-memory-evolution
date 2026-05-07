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


def test_stratified_half_split_is_deterministic_and_balanced():
    from mstar.benchmarks.state_bench import _stratified_half_split

    items = list(range(100))
    a, b = _stratified_half_split(items, seed=42)
    assert len(a) == 50 and len(b) == 50
    assert set(a) | set(b) == set(items)
    assert set(a) & set(b) == set()
    a2, b2 = _stratified_half_split(items, seed=42)
    assert a == a2 and b == b2
    a3, _ = _stratified_half_split(items, seed=43)
    assert a != a3


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
