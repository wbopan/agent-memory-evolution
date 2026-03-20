"""End-to-end test: run evolution → checkpoint → resume → verify continuity."""

from __future__ import annotations

import base64
import json
import pickle
import random
import textwrap
from unittest.mock import MagicMock

import pytest

from programmaticmemory.evolution.checkpoint import deserialize_pool_entry
from programmaticmemory.evolution.evaluator import MemoryEvaluator
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.reflector import ReflectionResult, Reflector
from programmaticmemory.evolution.strategies import FullDataset
from programmaticmemory.evolution.types import (
    DataItem,
    Dataset,
    EvalResult,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    ProgramPool,
    SoftmaxSelection,
)
from programmaticmemory.logging.run_output import RunOutputManager

# ---------------------------------------------------------------------------
# Minimal valid KB program seed
# ---------------------------------------------------------------------------

_SEED = textwrap.dedent("""\
    from dataclasses import dataclass, field

    INSTRUCTION_KNOWLEDGE_ITEM = "Extract facts."
    INSTRUCTION_QUERY = "Ask a question."
    INSTRUCTION_RESPONSE = "Answer directly."
    ALWAYS_ON_KNOWLEDGE = ""

    @dataclass
    class KnowledgeItem:
        text: str = ""

    @dataclass
    class Query:
        text: str = ""

    class KnowledgeBase:
        def __init__(self, toolkit):
            self.data = []
        def write(self, item, raw_text=""):
            self.data.append(raw_text)
        def read(self, query):
            return " ".join(self.data)[:3000]
""")

_SEED_2 = _SEED.replace("Extract facts.", "Capture key info.")

_CHILD = _SEED.replace("Extract facts.", "Store structured facts.")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dataset() -> Dataset:
    train = [DataItem(raw_text=f"Fact {i}", question=f"Q{i}?", expected_answer=f"A{i}") for i in range(5)]
    val = [DataItem(raw_text="", question=f"VQ{i}?", expected_answer=f"VA{i}") for i in range(10)]
    return Dataset(train=train, val=val, test=[])


def _make_eval_result(score: float) -> EvalResult:
    return EvalResult(
        score=score,
        per_case_scores=[score],
        per_case_outputs=["output"],
        failed_cases=[FailedCase(question="q", output="o", expected="e", score=0.0)] if score < 1.0 else [],
        success_cases=[],
    )


def _strip_header(source: str) -> str:
    """Strip the '# <name>  score=...  <label>' header comment that write_program prepends."""
    lines = source.splitlines(keepends=True)
    # Header is at most 2 lines: the comment + a blank line
    if lines and lines[0].startswith("#"):
        lines = lines[1:]  # strip comment
    if lines and lines[0].strip() == "":
        lines = lines[1:]  # strip blank line
    return "".join(lines)


# ---------------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------------


class TestResumeE2E:
    def test_checkpoint_and_resume(self, tmp_path):
        """Full run → checkpoint → resume → verify pool continuity."""
        random.seed(42)

        dataset = _make_dataset()
        seed0 = KBProgram(source_code=_SEED)
        seed1 = KBProgram(source_code=_SEED_2)
        child_program = KBProgram(source_code=_CHILD, generation=1, parent_hash=seed0.hash)

        # ── Phase 1: initial run (2 seeds + 1 iteration) ──────────────────

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = iter(
            [
                _make_eval_result(0.5),  # seed_0
                _make_eval_result(0.6),  # seed_1
                _make_eval_result(0.7),  # iter_1 child
            ]
        )

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = ReflectionResult(
            program=child_program, commit_message="Add structured facts"
        )
        reflector.max_fix_attempts = 0

        output_manager = RunOutputManager(base_dir=str(tmp_path), config={"test": True})
        run_dir = output_manager.run_dir

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            initial_programs=[seed0, seed1],
            max_iterations=1,
            strategy=SoftmaxSelection(temperature=0.15),
            eval_strategy=FullDataset(),
            output_manager=output_manager,
        )
        loop.run()
        output_manager.close()

        # ── Assert checkpoint structure ────────────────────────────────────

        state_file = run_dir / "state.json"
        assert state_file.exists(), "state.json must be written after iteration 1"

        checkpoint = json.loads(state_file.read_text(encoding="utf-8"))
        assert checkpoint["last_completed_iteration"] == 1
        assert "best_score" in checkpoint
        assert "random_state" in checkpoint
        assert "pool" in checkpoint
        assert len(checkpoint["pool"]) == 3  # seed_0, seed_1, iter_1

        # ── Assert eval dirs ──────────────────────────────────────────────

        assert (run_dir / "evals" / "seed_0").exists()
        assert (run_dir / "evals" / "seed_1").exists()
        assert (run_dir / "evals" / "iter_1").exists()

        # meta.json must be present in each eval dir
        for name in ("seed_0", "seed_1", "iter_1"):
            meta_path = run_dir / "evals" / name / "meta.json"
            assert meta_path.exists(), f"meta.json missing for {name}"
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            assert "overall_score" in meta

        # ── Restore pool from checkpoint + program files ──────────────────

        # Restore random state from checkpoint
        rng_state = pickle.loads(base64.b64decode(checkpoint["random_state"].encode("ascii")))
        random.setstate(rng_state)

        programs_dir = run_dir / "programs"
        restored_pool = ProgramPool(strategy=SoftmaxSelection(temperature=0.15))
        for entry_dict in checkpoint["pool"]:
            name = entry_dict["name"]
            prog_file = programs_dir / f"{name}.py"
            assert prog_file.exists(), f"programs/{name}.py must exist"
            raw_source = prog_file.read_text(encoding="utf-8")
            source_code = _strip_header(raw_source)
            pool_entry = deserialize_pool_entry(entry_dict, source_code)
            restored_pool.entries.append(pool_entry)

        assert len(restored_pool) == 3

        # Reconstruct EvolutionState from checkpoint
        history = [
            EvolutionRecord(
                iteration=r["iteration"],
                program=next(e.program for e in restored_pool.entries if e.program.hash == r["program_hash"]),
                score=r["score"],
                parent_hash=r.get("parent_hash"),
            )
            for r in checkpoint["score_history"]
        ]
        resumed_state = EvolutionState(
            pool=restored_pool,
            best_score=checkpoint["best_score"],
            history=history,
            total_iterations=checkpoint["last_completed_iteration"],
        )

        # Sanity-check: original scores are preserved
        scores_by_name = {e.name: e.score for e in restored_pool.entries}
        assert scores_by_name["seed_0"] == pytest.approx(0.5)
        assert scores_by_name["seed_1"] == pytest.approx(0.6)
        assert scores_by_name["iter_1"] == pytest.approx(0.7)

        # ── Phase 2: resume for one more iteration ────────────────────────

        child_program_2 = KBProgram(
            source_code=_SEED.replace("Extract facts.", "Index by topic."),
            generation=2,
            parent_hash=child_program.hash,
        )

        evaluator2 = MagicMock(spec=MemoryEvaluator)
        evaluator2.evaluate.side_effect = iter(
            [
                _make_eval_result(0.8),  # iter_2 child
            ]
        )

        reflector2 = MagicMock(spec=Reflector)
        reflector2.reflect_and_mutate.return_value = ReflectionResult(
            program=child_program_2, commit_message="Index by topic"
        )
        reflector2.max_fix_attempts = 0

        output_manager2 = RunOutputManager.from_existing(run_dir)

        loop2 = EvolutionLoop(
            evaluator=evaluator2,
            reflector=reflector2,
            dataset=dataset,
            initial_programs=[seed0, seed1],  # not used in resume path
            max_iterations=2,
            strategy=SoftmaxSelection(temperature=0.15),
            eval_strategy=FullDataset(),
            output_manager=output_manager2,
            start_iteration=1,
            resumed_pool=restored_pool,
            resumed_state=resumed_state,
        )
        state2 = loop2.run()
        output_manager2.close()

        # ── Assert resume results ─────────────────────────────────────────

        # Pool grew by exactly 1
        assert len(state2.pool) == 4, f"Expected 4 entries, got {len(state2.pool)}"

        # Previous entries preserved with original scores
        names_and_scores = {e.name: e.score for e in state2.pool.entries}
        assert names_and_scores["seed_0"] == pytest.approx(0.5)
        assert names_and_scores["seed_1"] == pytest.approx(0.6)
        assert names_and_scores["iter_1"] == pytest.approx(0.7)
        assert names_and_scores["iter_2"] == pytest.approx(0.8)

        # Best score updated
        assert state2.best_score == pytest.approx(0.8)

        # Checkpoint updated to iteration 2
        updated_checkpoint = json.loads(state_file.read_text(encoding="utf-8"))
        assert updated_checkpoint["last_completed_iteration"] == 2
        assert len(updated_checkpoint["pool"]) == 4

        # iter_2 eval dir written
        assert (run_dir / "evals" / "iter_2").exists()
