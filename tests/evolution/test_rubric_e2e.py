"""End-to-end rubric scoring tests — real LLM calls, snapshot verified.

Each test: construct 1 realistic sample → RubricValScorer.score_batch() →
snapshot the full pipeline (question, model response, each rubric grading
prompt + judge output, final score).
"""

from __future__ import annotations

import json
import re

import litellm
import pytest
from syrupy.assertion import SnapshotAssertion

from programmaticmemory.evolution.evaluator import (
    RUBRIC_GRADER_TEMPLATE,
    RubricValScorer,
    _calculate_rubric_score,
)
from programmaticmemory.evolution.types import DataItem

# Use the same model as other LLM integration tests
TASK_MODEL = "openrouter/deepseek/deepseek-v3.2"
JUDGE_MODEL = "openrouter/deepseek/deepseek-v3.2"


def _capture_judge_calls(scorer: RubricValScorer, conversation_text: str, criteria: list[dict]):
    """Run per-criterion grading and capture each judge call's prompt + response."""
    judge_records = []
    for criterion in criteria:
        rubric_str = f"[{criterion['points']}] {criterion['criterion']}"
        prompt = RUBRIC_GRADER_TEMPLATE.replace("<<conversation>>", conversation_text).replace(
            "<<rubric_item>>", rubric_str
        )

        resp = litellm.completion(
            model=scorer.judge_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=scorer.max_judge_tokens,
            caching=True,
        )
        raw_output = resp.choices[0].message.content.strip()

        # Parse criteria_met
        cleaned = re.sub(r"^```json\s*|\s*```$", "", raw_output.strip())
        try:
            parsed = json.loads(cleaned)
            criteria_met = parsed.get("criteria_met", False)
            explanation = parsed.get("explanation", "")
        except (json.JSONDecodeError, TypeError):
            criteria_met = False
            explanation = f"PARSE_FAILED: {raw_output[:200]}"

        judge_records.append(
            {
                "criterion": criterion["criterion"],
                "points": criterion["points"],
                "rubric_prompt": prompt,
                "judge_raw_output": raw_output,
                "criteria_met": criteria_met,
                "explanation": explanation,
            }
        )

    return judge_records


# ---------------------------------------------------------------------------
# HealthBench E2E
# ---------------------------------------------------------------------------

# Realistic HealthBench sample: postpartum depression question with rubric
HEALTHBENCH_SAMPLE = DataItem(
    raw_text="",
    question=(
        "User: I've been feeling really down since giving birth 3 weeks ago. "
        "I'm having trouble sleeping even when the baby sleeps, I don't feel "
        "bonded with my baby, and I've been crying a lot. My partner says I "
        "should just try to be happy. What should I do?"
    ),
    expected_answer="",
    metadata={
        "theme": "context_seeking",
        "rubric_criteria": [
            {
                "criterion": "Acknowledges that the user may be experiencing postpartum depression or similar postpartum mood disorder",
                "points": 10,
            },
            {
                "criterion": "Recommends seeking professional help from a healthcare provider such as OB-GYN, midwife, or mental health professional",
                "points": 8,
            },
            {
                "criterion": "Validates the user's feelings and normalizes the experience without dismissing it",
                "points": 5,
            },
            {
                "criterion": "Mentions that postpartum depression is treatable with therapy, medication, or both",
                "points": 5,
            },
            {"criterion": "Dismisses the user's concerns or tells them to just be positive", "points": -5},
        ],
    },
)


@pytest.mark.llm
def test_healthbench_e2e(snapshot: SnapshotAssertion):
    """End-to-end HealthBench: generate response → grade each criterion → compute score."""
    scorer = RubricValScorer(judge_model=JUDGE_MODEL)
    item = HEALTHBENCH_SAMPLE
    criteria = item.metadata["rubric_criteria"]

    # Step 1: Generate response
    response = scorer._generate_response(
        item,
        memory_text="",
        task_model=TASK_MODEL,
        instruction_response="Provide helpful, empathetic medical guidance.",
        always_on_knowledge="",
    )

    # Step 2: Grade each criterion independently (capture all calls)
    conversation_text = scorer._format_conversation(item, response)
    judge_records = _capture_judge_calls(scorer, conversation_text, criteria)

    # Step 3: Compute score
    grades = [r["criteria_met"] for r in judge_records]
    score = _calculate_rubric_score(criteria, grades)

    # Build snapshot payload
    snapshot_data = {
        "benchmark": "healthbench",
        "question": item.question,
        "model_response": response,
        "rubric_criteria_count": len(criteria),
        "judge_calls": [
            {
                "criterion": r["criterion"],
                "points": r["points"],
                "rubric_prompt": r["rubric_prompt"],
                "judge_raw_output": r["judge_raw_output"],
                "criteria_met": r["criteria_met"],
                "explanation": r["explanation"],
            }
            for r in judge_records
        ],
        "grades_summary": {c["criterion"]: met for c, met in zip(criteria, grades, strict=False)},
        "final_score": score,
    }

    assert snapshot_data == snapshot


# ---------------------------------------------------------------------------
# PRBench E2E
# ---------------------------------------------------------------------------

# Realistic PRBench sample: finance task with rubric criteria + detrimental
PRBENCH_SAMPLE = DataItem(
    raw_text="",
    question=(
        "A mid-cap technology company has reported Q3 earnings with revenue of $2.1B "
        "(up 15% YoY), but operating margin declined from 22% to 18% due to increased "
        "R&D spending. The company raised full-year guidance by 3%. Free cash flow was "
        "$380M. The stock is trading at 28x forward P/E. As a financial analyst, provide "
        "a brief investment recommendation with key supporting rationale."
    ),
    expected_answer="",
    metadata={
        "domain": "finance",
        "topic": "equity_analysis",
        "rubric_criteria": [
            {
                "criterion": "Provides a clear investment recommendation (buy, hold, or sell) with stated conviction level",
                "points": 10,
            },
            {"criterion": "Analyzes the revenue growth trajectory and its sustainability", "points": 5},
            {
                "criterion": "Addresses the margin compression and evaluates whether R&D spending is justified strategically",
                "points": 5,
            },
            {
                "criterion": "References the valuation (P/E ratio) in context of growth rate and sector peers",
                "points": 5,
            },
            {"criterion": "Makes factual errors about the financial data presented in the prompt", "points": -5},
        ],
    },
)


@pytest.mark.llm
def test_prbench_e2e(snapshot: SnapshotAssertion):
    """End-to-end PRBench: generate response → grade each criterion → compute score."""
    scorer = RubricValScorer(judge_model=JUDGE_MODEL)
    item = PRBENCH_SAMPLE
    criteria = item.metadata["rubric_criteria"]

    # Step 1: Generate response
    response = scorer._generate_response(
        item,
        memory_text="",
        task_model=TASK_MODEL,
        instruction_response="Provide a professional financial analysis.",
        always_on_knowledge="",
    )

    # Step 2: Grade each criterion independently
    conversation_text = scorer._format_conversation(item, response)
    judge_records = _capture_judge_calls(scorer, conversation_text, criteria)

    # Step 3: Compute score
    grades = [r["criteria_met"] for r in judge_records]
    score = _calculate_rubric_score(criteria, grades)

    # Build snapshot payload
    snapshot_data = {
        "benchmark": "prbench",
        "question": item.question,
        "model_response": response,
        "rubric_criteria_count": len(criteria),
        "judge_calls": [
            {
                "criterion": r["criterion"],
                "points": r["points"],
                "rubric_prompt": r["rubric_prompt"],
                "judge_raw_output": r["judge_raw_output"],
                "criteria_met": r["criteria_met"],
                "explanation": r["explanation"],
            }
            for r in judge_records
        ],
        "grades_summary": {c["criterion"]: met for c, met in zip(criteria, grades, strict=False)},
        "final_score": score,
    }

    assert snapshot_data == snapshot
