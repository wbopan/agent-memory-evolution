# Val Scorer Conversation History Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Include KB retrieval conversation in `FailedCase.conversation_history` for the ValScorer path, so the reflection LLM can diagnose retrieval vs execution failures.

**Architecture:** Modify `_val_scorer_path` to extract query/retrieval conversation from `_QuerySlot` and pass it to `FailedCase`, mirroring the existing `_default_answer_and_score` pattern. Add a test that verifies conversation_history is populated in the ValScorer path.

**Tech Stack:** Python, pytest, syrupy snapshots

---

### Task 1: Write failing test for conversation_history in ValScorer path

**Files:**
- Modify: `tests/evolution/test_evaluator.py:852-900` (TestValScorerIntegration class)

**Step 1: Write the failing test**

Add a new test method to `TestValScorerIntegration` after the existing `test_val_scorer_receives_retrieved_memory` test (line ~900). Insert before the `test_val_scorer_none_uses_default_path` method.

```python
    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_path_includes_conversation_history(self, mock_litellm):
        """ValScorer path should include retrieval conversation in failed_cases."""

        class FailingScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response):
                return [("episode transcript: FAIL", 0.0)] * len(items)

        batch_mock = _make_batch_mock(
            [
                ['{"raw": "The sky is blue."}'],  # train: obs generation
                ['{"raw": "sky query"}'],  # val round 1: query generation
            ]
        )
        mock_litellm.batch_completion = batch_mock

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
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestValScorerIntegration::test_val_scorer_path_includes_conversation_history -v`
Expected: FAIL — `assert len(fc.conversation_history) == 3` fails because `conversation_history` is currently `[]`.

**Step 3: Commit failing test**

```bash
git add tests/evolution/test_evaluator.py
git commit -m "test: add failing test for conversation_history in ValScorer path"
```

---

### Task 2: Implement the fix in `_val_scorer_path`

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:713-725`

**Step 1: Modify `_val_scorer_path` to include retrieval conversation**

In `_val_scorer_path`, replace lines 713-725 (the loop body) with:

```python
        for i, (output, score) in enumerate(results):
            scores.append(score)
            outputs.append(output)
            # Include retrieval conversation so reflection LLM can diagnose
            # whether failures stem from poor KB retrieval or poor execution.
            slot = slots[i]
            conv = (
                [
                    {"role": "user", "content": slot.query_prompt},
                    {"role": "assistant", "content": slot.query_json},
                    {"role": "user", "content": slot.retrieved_prompt},
                ]
                if slot is not None
                else []
            )
            case = FailedCase(
                question=val_data[i].question,
                output=output,
                expected=val_data[i].expected_answer,
                score=score,
                conversation_history=conv,
                memory_logs=log_snapshot,
            )
```

This replaces the old comment block ("conversation_history intentionally omitted...") and adds the 3-message retrieval conversation from the `_QuerySlot`.

**Step 2: Run the new test to verify it passes**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestValScorerIntegration::test_val_scorer_path_includes_conversation_history -v`
Expected: PASS

**Step 3: Run all evaluator tests to check for regressions**

Run: `uv run pytest tests/evolution/test_evaluator.py -v`
Expected: All pass. The existing `test_val_scorer_receives_retrieved_memory` test doesn't assert on `conversation_history`, so it should still pass.

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py
git commit -m "fix: include retrieval conversation in ValScorer path failed cases"
```

---

### Task 3: Run full test suite and update snapshots if needed

**Files:**
- Possibly update: `tests/evolution/__snapshots__/test_evaluator.ambr`

**Step 1: Run full test suite**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All pass. Snapshot tests should not be affected since the ValScorer integration tests don't use snapshots for the conversation_history assertion.

**Step 2: If any snapshot tests fail, update them**

Run: `uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v`
Review the snapshot diff to confirm changes are expected (only conversation_history additions).

**Step 3: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/evaluator.py && uv run ruff format --check src/programmaticmemory/evolution/evaluator.py`
Expected: Clean

**Step 4: Final commit if snapshots changed**

```bash
git add tests/evolution/__snapshots__/
git commit -m "test: update snapshots for val scorer conversation history"
```
