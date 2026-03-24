"""Tests for seed programs — compile check and end-to-end evaluation with real LLM."""

from __future__ import annotations

from pathlib import Path

import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.sandbox import CompiledProgram, compile_kb_program, smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import DataItem, KBProgram

SEEDS_DIR = Path(__file__).resolve().parents[2] / "src" / "programmaticmemory" / "seeds"
MODEL = "openrouter/deepseek/deepseek-v3.2"

SEED_FILES = sorted(SEEDS_DIR.glob("*.py"))
SEED_IDS = [f.stem for f in SEED_FILES]


# ---------------------------------------------------------------------------
# Compile + smoke test (no LLM needed)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("seed_file", SEED_FILES, ids=SEED_IDS)
@pytest.mark.uses_chroma
def test_seed_compiles(seed_file: Path):
    """Each seed program compiles and defines all required classes and constants."""
    source = seed_file.read_text()
    result = compile_kb_program(source)
    assert isinstance(result, CompiledProgram), f"{seed_file.name} compile failed: {result}"
    assert result.ki_cls.__name__ == "KnowledgeItem"
    assert result.query_cls.__name__ == "Query"
    assert result.kb_cls.__name__ == "KnowledgeBase"
    assert isinstance(result.instruction_knowledge_item, str)
    assert len(result.instruction_knowledge_item) > 0
    assert isinstance(result.instruction_query, str)
    assert len(result.instruction_query) > 0
    assert isinstance(result.instruction_response, str)
    assert len(result.instruction_response) > 0
    assert isinstance(result.always_on_knowledge, str)


@pytest.mark.parametrize("seed_file", SEED_FILES, ids=SEED_IDS)
@pytest.mark.uses_chroma
def test_seed_smoke_test(seed_file: Path):
    """Each seed program passes the smoke test (basic write/read cycle)."""
    source = seed_file.read_text()
    result = smoke_test(source, timeout=30.0)
    assert result.success, f"{seed_file.name} smoke test failed: {result.error}"


# ---------------------------------------------------------------------------
# End-to-end offline evaluation with real LLM
# ---------------------------------------------------------------------------


@pytest.mark.llm
@pytest.mark.uses_chroma
@pytest.mark.parametrize("seed_file", SEED_FILES, ids=SEED_IDS)
def test_seed_end_to_end(seed_file: Path, snapshot: SnapshotAssertion):
    """Each seed: ingest fact via raw_text, retrieve and answer correctly."""
    source = seed_file.read_text()
    program = KBProgram(source_code=source)
    train_data = [
        DataItem(
            raw_text="Project Zephyr's access code is DELTA-7742.",
            question="What is Project Zephyr's access code?",
            expected_answer="DELTA-7742",
        ),
    ]
    val_data = list(train_data)

    evaluator = MemoryEvaluator(task_model=MODEL, toolkit_config=ToolkitConfig(llm_model=MODEL))
    result = evaluator.evaluate(program, train_data, val_data)

    assert result.score > 0, f"Expected positive score, got {result.score}. Outputs: {result.per_case_outputs}"
    assert len(result.per_case_outputs) == 1
    assert len(result.per_case_outputs[0]) > 0

    assert {
        "score": result.score,
        "output": result.per_case_outputs[0],
        "num_failed": len(result.failed_cases),
    } == snapshot
