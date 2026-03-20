"""Serialization/deserialization helpers for evolution types (checkpoint support)."""

from __future__ import annotations

from programmaticmemory.evolution.types import (
    EvalResult,
    FailedCase,
    KBProgram,
    PoolEntry,
    TrainExample,
)

# ---------------------------------------------------------------------------
# FailedCase
# ---------------------------------------------------------------------------


def serialize_failed_case(fc: FailedCase) -> dict:
    """Serialize a FailedCase to a JSON-safe dict."""
    return {
        "question": fc.question,
        "output": fc.output,
        "rationale": fc.rationale,
        "score": fc.score,
        "conversation_history": list(fc.conversation_history),
        "memory_logs": list(fc.memory_logs),
    }


def deserialize_failed_case(d: dict) -> FailedCase:
    """Deserialize a FailedCase from a dict."""
    return FailedCase(
        question=d["question"],
        output=d["output"],
        rationale=d.get("rationale", d.get("expected", "")),
        score=d["score"],
        conversation_history=list(d.get("conversation_history", [])),
        memory_logs=list(d.get("memory_logs", [])),
    )


# ---------------------------------------------------------------------------
# EvalResult
# ---------------------------------------------------------------------------


def serialize_eval_result(er: EvalResult) -> dict:
    """Serialize an EvalResult to a JSON-safe dict."""
    return {
        "score": er.score,
        "per_case_scores": list(er.per_case_scores),
        "per_case_outputs": list(er.per_case_outputs),
        "failed_cases": [serialize_failed_case(fc) for fc in er.failed_cases],
        "success_cases": [serialize_failed_case(fc) for fc in er.success_cases],
        "logs": list(er.logs),
        "train_examples": [{"messages": list(te.messages)} for te in er.train_examples],
        "runtime_violation": er.runtime_violation,
    }


def deserialize_eval_result(d: dict) -> EvalResult:
    """Deserialize an EvalResult from a dict."""
    return EvalResult(
        score=d["score"],
        per_case_scores=list(d.get("per_case_scores", [])),
        per_case_outputs=list(d.get("per_case_outputs", [])),
        failed_cases=[deserialize_failed_case(fc) for fc in d.get("failed_cases", [])],
        success_cases=[deserialize_failed_case(fc) for fc in d.get("success_cases", [])],
        logs=list(d.get("logs", [])),
        train_examples=[TrainExample(messages=list(te["messages"])) for te in d.get("train_examples", [])],
        runtime_violation=d.get("runtime_violation"),
    )


# ---------------------------------------------------------------------------
# PoolEntry  (source_code stored separately in programs/*.py)
# ---------------------------------------------------------------------------


def serialize_pool_entry(entry: PoolEntry) -> dict:
    """Serialize a PoolEntry to a JSON-safe dict.

    NOTE: source_code is intentionally excluded — it is stored on disk as
    programs/<name>.py and loaded separately via deserialize_pool_entry().
    """
    return {
        "name": entry.name,
        "program_hash": entry.program.hash,
        "program_generation": entry.program.generation,
        "program_parent_hash": entry.program.parent_hash,
        "eval_result": serialize_eval_result(entry.eval_result),
        "reflection_result": serialize_eval_result(entry.reflection_result) if entry.reflection_result else None,
        "commit_message": entry.commit_message,
    }


def deserialize_pool_entry(d: dict, source_code: str) -> PoolEntry:
    """Deserialize a PoolEntry from a dict, supplying source_code externally."""
    program = KBProgram(
        source_code=source_code,
        generation=d.get("program_generation", 0),
        parent_hash=d.get("program_parent_hash"),
    )
    reflection_result_d = d.get("reflection_result")
    return PoolEntry(
        program=program,
        eval_result=deserialize_eval_result(d["eval_result"]),
        name=d.get("name", "seed_0"),
        reflection_result=deserialize_eval_result(reflection_result_d) if reflection_result_d is not None else None,
        commit_message=d.get("commit_message"),
    )
