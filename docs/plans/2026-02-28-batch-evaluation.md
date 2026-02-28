# Batch Evaluation Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add `batch_process: bool = True` to `MemoryEvaluator` so all independent LLM calls in a phase are issued in parallel via `litellm.batch_completion` instead of one-by-one.

**Architecture:** `litellm.batch_completion(model, messages=[list-of-message-lists])` returns `List[ModelResponse | Exception]` using a `ThreadPoolExecutor` internally. We replace per-item `_llm_call` with a single `batch_completion` call per phase-round. Memory operations (`write`/`read`) remain serial. Online train runs 3 batch rounds (query → answer → obs-with-feedback) then serial writes.

**Tech Stack:** `litellm.batch_completion` (already a dependency), Python 3.12, pytest + syrupy snapshots.

---

## Task 1: Add `batch_process` param to `MemoryEvaluator.__init__`

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:126-135`

**Step 1: Add `batch_process` param**

In `MemoryEvaluator.__init__`, add the new parameter and store it:

```python
def __init__(
    self,
    scorer: Scorer | None = None,
    task_model: str = "openrouter/deepseek/deepseek-v3.2",
    toolkit_config: ToolkitConfig | None = None,
    batch_process: bool = True,
) -> None:
    self.scorer = scorer or ExactMatchScorer()
    self.task_model = task_model
    self.toolkit_config = toolkit_config
    self.batch_process = batch_process
    self.logger = get_logger()
```

**Step 2: Run tests to confirm nothing breaks yet**

```bash
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

Expected: all pass (no behaviour changed yet, just a new parameter added).

**Step 3: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py
git commit -m "feat: add batch_process param to MemoryEvaluator (no-op)"
```

---

## Task 2: Update existing tests to use `batch_process=False`

The existing tests mock `litellm.completion`. When we flip the default to `True` in later tasks, they'll break. Fix this now by explicitly opting them into the sequential path.

**Files:**
- Modify: `tests/evolution/test_evaluator.py` (all `MemoryEvaluator(task_model="mock/model")` calls)

**Step 1: Add `batch_process=False` to every existing test instantiation**

There are exactly these instantiation sites — replace each one:

```python
# Before:
evaluator = MemoryEvaluator(task_model="mock/model")
# After:
evaluator = MemoryEvaluator(task_model="mock/model", batch_process=False)
```

Occurrences (search for `MemoryEvaluator(task_model="mock/model")`):
- `TestMemoryEvaluatorOffline.test_basic_offline_evaluation`
- `TestMemoryEvaluatorOffline.test_offline_wrong_answer`
- `TestMemoryEvaluatorOffline.test_offline_val_uses_multiturn`
- `TestMemoryEvaluatorOnline.test_online_train_messages_accumulate`
- `TestMemoryEvaluatorOnline.test_online_step1_output_parses_to_query`
- `TestMemoryEvaluatorOnline.test_online_step3_output_parses_to_observation`
- `TestMemoryEvaluatorOnline.test_online_write_called_and_memory_updates`
- `TestMemoryEvaluatorOnline.test_online_step3_includes_feedback_and_ground_truth`
- `TestValidationPipeline.test_val_only_step1_and_step2`
- `TestValidationPipeline.test_val_does_not_call_write`
- `TestValidationPipeline.test_val_conversation_history_in_failed_cases`
- `TestValidationPipeline.test_val_multiturn_messages_structure`
- `TestEvaluatorEdgeCases.test_observation_generation_failure_skips_item`
- `TestEvaluatorEdgeCases.test_query_generation_failure_scores_zero`
- The `evaluator.evaluate(...)` call inside `TestEvaluatorEdgeCases.test_empty_val_data`

Also find the `MemoryEvaluator()` call (no args) in `test_compile_error_returns_zero` — add `batch_process=False` there too.

**Step 2: Verify tests still pass**

```bash
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

Expected: all pass (behaviour identical, snapshots unchanged).

**Step 3: Commit**

```bash
git add tests/evolution/test_evaluator.py
git commit -m "test: pin existing evaluator tests to batch_process=False"
```

---

## Task 3: Add `_batch_llm_call` helper and refactor `_evaluate_offline` (batched path)

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py`

**Step 1: Add `_batch_llm_call` helper** (below `_llm_call`):

```python
def _batch_llm_call(self, all_messages: list[list[dict]]) -> list[str | None]:
    """Fan out independent LLM calls via litellm.batch_completion.

    Returns a list of content strings (same length as all_messages).
    Failed entries are None (error already logged).
    """
    if not all_messages:
        return []
    responses = litellm.batch_completion(model=self.task_model, messages=all_messages)
    results: list[str | None] = []
    for resp in responses:
        if isinstance(resp, Exception):
            self.logger.log(f"Batch LLM call failed: {resp}", header="EVAL")
            results.append(None)
        else:
            results.append(resp.choices[0].message.content)
    return results
```

**Step 2: Refactor `_evaluate_offline` to dispatch on `self.batch_process`**

Replace the body of `_evaluate_offline` with:

```python
def _evaluate_offline(self, memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit):
    """Offline: Batch ingest train (LLM generates observations), then evaluate val."""
    logs: list[str] = []

    if self.batch_process:
        # Build all prompts at once
        all_messages = [
            [{"role": "user", "content": build_observation_generation_prompt(item.raw_text, obs_schema)}]
            for item in train_data
        ]
        responses = self._batch_llm_call(all_messages)
        for item, content in zip(train_data, responses):
            if content is None:
                logs.append(f"Failed to generate observation for: {item.raw_text[:60]}")
                continue
            try:
                obs = obs_cls(**_parse_json_from_llm(content))
                memory.write(obs)
            except Exception as e:
                logs.append(f"Obs parse/write failed: {e}")
    else:
        for item in train_data:
            obs = self._generate_observation_standalone(item.raw_text, obs_cls, obs_schema)
            if obs is None:
                logs.append(f"Failed to generate observation for: {item.raw_text[:60]}")
                continue
            try:
                memory.write(obs)
            except Exception as e:
                logs.append(f"Write failed: {e}")

    return self._evaluate_val(memory, query_cls, query_schema, val_data, logs, toolkit)
```

**Step 3: Write failing tests for the batched offline path**

Add a new test class to `tests/evolution/test_evaluator.py`:

```python
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
        batch_mock = self._make_batch_mock([
            ['{"raw": "France capital is Paris."}', '{"raw": "Germany capital is Berlin."}'],  # train batch
            ['{"raw": "capital of France"}', '{"raw": "capital of Germany"}'],  # val round 1: queries
            ["Paris", "Berlin"],  # val round 2: answers
        ])
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
    def test_offline_train_exception_in_batch_skips_item(self, mock_litellm):
        """If one batch response is an Exception, that item is skipped gracefully."""
        call_idx = [0]

        def mock_batch_completion(*args, **kwargs):
            call_idx[0] += 1
            messages = kwargs.get("messages", [])
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
```

**Step 4: Run new tests (expect failure)**

```bash
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch -v
```

Expected: FAIL — `batch_completion` not yet called by code.

**Step 5: Run all tests to confirm existing ones still pass**

```bash
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

Expected: only `TestMemoryEvaluatorBatch` tests fail; everything else passes.

**Step 6: Run `--snapshot-update` to capture new snapshots**

```bash
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch::test_offline_train_single_batch_call --snapshot-update -v
```

**Step 7: Run all tests**

```bash
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

Expected: all pass.

**Step 8: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py tests/evolution/__snapshots__/
git commit -m "feat: batch offline train observations via litellm.batch_completion"
```

---

## Task 4: Refactor `_evaluate_val` to support batched path

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:310-435`

**Step 1: Refactor `_evaluate_val` with batched and sequential paths**

The batched path collects all query prompts, issues one `batch_completion`, does serial reads, then issues one more `batch_completion` for answers.

Replace `_evaluate_val` with:

```python
def _evaluate_val(self, memory, query_cls, query_schema, val_data, logs, toolkit):
    """Validation: query → read → answer → score. No writes."""
    if self.batch_process:
        return self._evaluate_val_batched(memory, query_cls, query_schema, val_data, logs, toolkit)
    return self._evaluate_val_sequential(memory, query_cls, query_schema, val_data, logs, toolkit)

def _evaluate_val_batched(self, memory, query_cls, query_schema, val_data, logs, toolkit):
    scores: list[float] = []
    outputs: list[str] = []
    failed_cases: list[FailedCase] = []

    if not val_data:
        logs.append("Val score: 0.000 (0 cases)")
        return EvalResult(score=0.0, per_case_scores=[], per_case_outputs=[], failed_cases=[], logs=logs)

    # Round 1: batch all query generation prompts
    query_messages = [
        [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema)}]
        for item in val_data
    ]
    query_responses = self._batch_llm_call(query_messages)

    # Parse queries + serial reads
    parsed_queries: list[object | None] = []
    query_json_strs: list[str | None] = []
    retrieved_strs: list[str] = []

    for item, content in zip(val_data, query_responses):
        if content is None:
            parsed_queries.append(None)
            query_json_strs.append(None)
            retrieved_strs.append("")
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, memory_logs=list(toolkit.logger.logs),
            ))
            continue
        try:
            query = query_cls(**_parse_json_from_llm(content))
            parsed_queries.append(query)
            query_json_strs.append(content)
        except Exception as e:
            self.logger.log(f"Val query parse failed: {e}", header="EVAL")
            parsed_queries.append(None)
            query_json_strs.append(content)
            retrieved_strs.append("")
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, memory_logs=list(toolkit.logger.logs),
            ))
            continue

        try:
            retrieved = memory.read(query)
            retrieved_strs.append(str(retrieved) if retrieved is not None else "")
        except Exception as e:
            retrieved_strs.append(f"Read error: {e}")
            logs.append(f"Val read failed: {e}")

    # Round 2: batch all answer prompts (only for items that had valid queries)
    answer_messages: list[list[dict] | None] = []
    for item, query_json, retrieved in zip(val_data, query_json_strs, retrieved_strs):
        if query_json is None or len(retrieved_strs) <= val_data.index(item):
            answer_messages.append(None)
        else:
            msgs = [
                {"role": "user", "content": build_query_generation_prompt(item.question, query_schema)},
                {"role": "assistant", "content": query_json},
                {"role": "user", "content": build_retrieved_memory_prompt(retrieved)},
            ]
            answer_messages.append(msgs)

    valid_answer_messages = [m for m in answer_messages if m is not None]
    valid_indices = [i for i, m in enumerate(answer_messages) if m is not None]
    answer_responses = self._batch_llm_call(valid_answer_messages)

    # Map responses back; items that failed query parse already have 0.0
    answer_map: dict[int, str | None] = {idx: resp for idx, resp in zip(valid_indices, answer_responses)}

    # Rebuild scores/outputs for items that got through query parsing
    # (reset and rebuild cleanly from scratch)
    scores = []
    outputs = []
    failed_cases = []
    score_idx = 0
    for i, item in enumerate(val_data):
        if i not in answer_map:
            # Already failed at query stage — handled above but we need to re-add
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, memory_logs=list(toolkit.logger.logs),
            ))
            continue

        answer_content = answer_map[i]
        if answer_content is None:
            self.logger.log("Val answer generation failed (batch error)", header="EVAL")
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, conversation_history=answer_messages[i],
                memory_logs=list(toolkit.logger.logs),
            ))
            continue

        outputs.append(answer_content)
        score = self.scorer(answer_content, item.expected_answer)
        scores.append(score)
        if score < 1.0:
            failed_cases.append(FailedCase(
                question=item.question, output=answer_content, expected=item.expected_answer,
                score=score, conversation_history=answer_messages[i],
                memory_logs=list(toolkit.logger.logs),
            ))

    avg_score = sum(scores) / len(scores) if scores else 0.0
    logs.append(f"Val score: {avg_score:.3f} ({len(scores)} cases)")
    return EvalResult(
        score=avg_score, per_case_scores=scores, per_case_outputs=outputs,
        failed_cases=failed_cases, logs=logs,
    )
```

And rename the existing `_evaluate_val` body to `_evaluate_val_sequential` (the existing implementation, just renamed).

> **Note:** The `answer_map` reconstruction above is complex because items that fail query parsing are skipped for round 2. A simpler approach is to track a sentinel list in parallel. See note in step below.

**Step 1 (simpler rewrite):** Actually, let's use a cleaner index-aware approach. Replace the above with:

```python
def _evaluate_val_batched(self, memory, query_cls, query_schema, val_data, logs, toolkit):
    """Two-round batched val: all query prompts → serial reads → all answer prompts."""
    if not val_data:
        logs.append("Val score: 0.000 (0 cases)")
        return EvalResult(score=0.0, per_case_scores=[], per_case_outputs=[], failed_cases=[], logs=logs)

    # Round 1: batch all query generation
    round1_messages = [
        [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema)}]
        for item in val_data
    ]
    round1_responses = self._batch_llm_call(round1_messages)

    # Parse queries and do serial memory reads
    # slot[i] = (query_obj, query_json_str, retrieved_str) or None if failed
    slots: list[tuple | None] = []
    for item, content in zip(val_data, round1_responses):
        if content is None:
            slots.append(None)
            continue
        try:
            query = query_cls(**_parse_json_from_llm(content))
        except Exception as e:
            self.logger.log(f"Val query parse failed: {e}", header="EVAL")
            slots.append(None)
            continue
        try:
            retrieved = memory.read(query)
            retrieved_str = str(retrieved) if retrieved is not None else ""
        except Exception as e:
            retrieved_str = f"Read error: {e}"
            logs.append(f"Val read failed: {e}")
        slots.append((query, content, retrieved_str))

    # Round 2: batch answer generation only for successful slots
    valid = [(i, s) for i, s in enumerate(slots) if s is not None]
    round2_messages = [
        [
            {"role": "user", "content": build_query_generation_prompt(val_data[i].question, query_schema)},
            {"role": "assistant", "content": s[1]},
            {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
        ]
        for i, s in valid
    ]
    round2_responses = self._batch_llm_call(round2_messages)

    # Assemble results
    scores: list[float] = []
    outputs: list[str] = []
    failed_cases: list[FailedCase] = []

    valid_idx = 0
    for i, item in enumerate(val_data):
        slot = slots[i]
        if slot is None:
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, memory_logs=list(toolkit.logger.logs),
            ))
            continue

        answer = round2_responses[valid_idx]
        valid_idx += 1

        if answer is None:
            self.logger.log("Val answer generation failed (batch error)", header="EVAL")
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(FailedCase(
                question=item.question, output="", expected=item.expected_answer,
                score=0.0, memory_logs=list(toolkit.logger.logs),
            ))
            continue

        outputs.append(answer)
        score = self.scorer(answer, item.expected_answer)
        scores.append(score)
        if score < 1.0:
            conv = [
                {"role": "user", "content": build_query_generation_prompt(item.question, query_schema)},
                {"role": "assistant", "content": slot[1]},
                {"role": "user", "content": build_retrieved_memory_prompt(slot[2])},
                {"role": "assistant", "content": answer},
            ]
            failed_cases.append(FailedCase(
                question=item.question, output=answer, expected=item.expected_answer,
                score=score, conversation_history=conv,
                memory_logs=list(toolkit.logger.logs),
            ))

    avg_score = sum(scores) / len(scores) if scores else 0.0
    logs.append(f"Val score: {avg_score:.3f} ({len(scores)} cases)")
    return EvalResult(
        score=avg_score, per_case_scores=scores, per_case_outputs=outputs,
        failed_cases=failed_cases, logs=logs,
    )
```

**Step 2: Add tests for batched val to `TestMemoryEvaluatorBatch`**

```python
@patch("programmaticmemory.evolution.evaluator.litellm")
def test_val_two_batch_rounds(self, mock_litellm, snapshot: SnapshotAssertion):
    """Val with batch_process=True uses exactly 2 batch_completion rounds."""
    batch_mock = self._make_batch_mock([
        ['{"raw": "obs1"}'],                              # offline train (1 item)
        ['{"raw": "q1"}', '{"raw": "q2"}'],              # val round 1: both queries
        ["correct1", "correct2"],                         # val round 2: both answers
    ])
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
```

**Step 3: Run new test (expect failure), then snapshot-update**

```bash
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch::test_val_two_batch_rounds -v
# Expected: FAIL

uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch --snapshot-update -v
# After implementation: update snapshots
```

**Step 4: Run all tests**

```bash
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

Expected: all pass.

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py tests/evolution/__snapshots__/
git commit -m "feat: batch val evaluation via two batch_completion rounds"
```

---

## Task 5: Refactor `_evaluate_online` to support batched path

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py`

**Step 1: Refactor `_evaluate_online` with batched path (3 batch rounds then serial writes)**

Add a dispatching wrapper and implement the batched path:

```python
def _evaluate_online(self, memory, obs_cls, query_cls, obs_schema, query_schema, train_data, val_data, toolkit):
    logs: list[str] = []
    if self.batch_process:
        self._online_train_batched(memory, obs_cls, query_cls, obs_schema, query_schema, train_data, logs)
    else:
        self._online_train_sequential(memory, obs_cls, query_cls, obs_schema, query_schema, train_data, logs)
    return self._evaluate_val(memory, query_cls, query_schema, val_data, logs, toolkit)
```

The existing `_evaluate_online` body becomes `_online_train_sequential`. Add `_online_train_batched`:

```python
def _online_train_batched(self, memory, obs_cls, query_cls, obs_schema, query_schema, train_data, logs):
    """Online train batched: 3 rounds of batch_completion, then serial writes."""
    if not train_data:
        return

    # Round 1: query generation for all items
    round1_messages = [
        [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema)}]
        for item in train_data
    ]
    round1_responses = self._batch_llm_call(round1_messages)

    # Parse queries + serial reads
    # slot[i] = (query_obj, query_json_str, retrieved_str) or None
    slots: list[tuple | None] = []
    for item, content in zip(train_data, round1_responses):
        if content is None:
            logs.append(f"Train query generation failed (batch error)")
            slots.append(None)
            continue
        try:
            query = query_cls(**_parse_json_from_llm(content))
        except Exception as e:
            logs.append(f"Train query parse failed: {e}")
            slots.append(None)
            continue
        try:
            retrieved = memory.read(query)
            retrieved_str = str(retrieved) if retrieved is not None else ""
        except Exception as e:
            retrieved_str = f"Read error: {e}"
            logs.append(f"Train read failed: {e}")
        slots.append((query, content, retrieved_str))

    # Round 2: answer generation for valid slots
    valid = [(i, s) for i, s in enumerate(slots) if s is not None]
    round2_messages = [
        [
            {"role": "user", "content": build_query_generation_prompt(train_data[i].question, query_schema)},
            {"role": "assistant", "content": s[1]},
            {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
        ]
        for i, s in valid
    ]
    round2_responses = self._batch_llm_call(round2_messages)

    # Score answers for feedback; build (item, slot, answer, score) tuples
    answered: list[tuple] = []  # (item, slot, messages_so_far, answer, score)
    for (i, s), answer in zip(valid, round2_responses):
        item = train_data[i]
        if answer is None:
            logs.append("Train answer generation failed (batch error)")
            continue
        score = self.scorer(answer, item.expected_answer)
        evaluation_result = f"Score: {score:.1f} ({'correct' if score >= 1.0 else 'incorrect'})"
        msgs_so_far = [
            {"role": "user", "content": build_query_generation_prompt(item.question, query_schema)},
            {"role": "assistant", "content": s[1]},
            {"role": "user", "content": build_retrieved_memory_prompt(s[2])},
            {"role": "assistant", "content": answer},
        ]
        answered.append((item, s, msgs_so_far, answer, score, evaluation_result))

    # Round 3: observation generation with feedback
    round3_messages = [
        msgs + [{"role": "user", "content": build_observation_with_feedback_prompt(
            evaluation_result, item.expected_answer, obs_schema
        )}]
        for item, s, msgs, answer, score, evaluation_result in answered
    ]
    round3_responses = self._batch_llm_call(round3_messages)

    # Serial writes
    for (item, s, msgs, answer, score, ev), obs_content in zip(answered, round3_responses):
        if obs_content is None:
            logs.append("Train observation generation failed (batch error)")
            continue
        try:
            obs = obs_cls(**_parse_json_from_llm(obs_content))
            memory.write(obs)
        except Exception as e:
            logs.append(f"Train observation parse/write failed: {e}")
```

Rename the existing `_evaluate_online` body into `_online_train_sequential` (it receives the same args).

**Step 2: Add test for online batched train**

```python
@patch("programmaticmemory.evolution.evaluator.litellm")
def test_online_train_three_batch_rounds(self, mock_litellm, snapshot: SnapshotAssertion):
    """Online train batch_process=True: 3 rounds for train + 2 rounds for val."""
    batch_mock = self._make_batch_mock([
        ['{"raw": "q"}'],           # online train round 1: query gen
        ["my answer"],              # online train round 2: answer gen
        ['{"raw": "obs stored"}'],  # online train round 3: obs gen
        ['{"raw": "vq"}'],          # val round 1: query gen
        ["obs stored"],             # val round 2: answer
    ])
    mock_litellm.batch_completion = batch_mock

    program = MemoryProgram(source_code=INITIAL_MEMORY_PROGRAM)
    train = [DataItem(raw_text="fact", question="Q?", expected_answer="A")]
    val = [DataItem(raw_text="x", question="VQ?", expected_answer="obs stored")]

    evaluator = MemoryEvaluator(task_model="mock/model", batch_process=True)
    result = evaluator.evaluate(program, train, val, eval_mode=EvalMode.ONLINE)

    assert len(batch_mock.captured_calls) == 5
    assert len(batch_mock.captured_calls[0]) == 1  # round 1: 1 query prompt
    assert len(batch_mock.captured_calls[2]) == 1  # round 3: 1 obs prompt (5 msgs each)
    assert len(batch_mock.captured_calls[2][0]) == 5  # full multi-turn context
    assert result.score == 1.0
    assert batch_mock.captured_calls == snapshot
```

**Step 3: Run new test (expect failure), implement, run + snapshot-update**

```bash
# Verify test fails before implementation
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch::test_online_train_three_batch_rounds -v

# After implementation:
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
uv run pytest tests/evolution/test_evaluator.py::TestMemoryEvaluatorBatch --snapshot-update -v
uv run pytest tests/evolution/test_evaluator.py -m "not llm" -v
```

**Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py tests/evolution/__snapshots__/
git commit -m "feat: batch online train via three batch_completion rounds"
```

---

## Task 6: Wire `--no-batch` CLI flag into `__main__.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py:43-73`

**Step 1: Add `--no-batch` argument to the parser**

After line `parser.add_argument("--no-output", ...)`, add:

```python
parser.add_argument("--no-batch", action="store_true", help="Disable batch processing (sequential mode for debugging)")
```

**Step 2: Pass `batch_process` to `MemoryEvaluator`**

Change the `evaluator = MemoryEvaluator(...)` call:

```python
evaluator = MemoryEvaluator(
    scorer=scorer,
    task_model=args.task_model,
    toolkit_config=toolkit_config,
    batch_process=not args.no_batch,
)
```

**Step 3: Verify CLI help shows the new flag**

```bash
uv run python -m programmaticmemory.evolution --help
```

Expected: `--no-batch` appears in help text.

**Step 4: Lint**

```bash
uv run ruff check src/ && uv run ruff format src/
```

Expected: no errors.

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: add --no-batch CLI flag to disable batch processing"
```

---

## Task 7: Final verification

**Step 1: Run full test suite**

```bash
uv run pytest tests/evolution/ -m "not llm" -v
```

Expected: all pass.

**Step 2: Quick smoke test with batch mode on kv_memory**

```bash
uv run python -m programmaticmemory.evolution --iterations 1 --no-weave --no-output num_items=3
```

Expected: completes without error, shows score.

**Step 3: Quick smoke test with `--no-batch`**

```bash
uv run python -m programmaticmemory.evolution --iterations 1 --no-weave --no-output --no-batch num_items=3
```

Expected: completes without error (sequential path).

**Step 4: Final commit if any cleanup needed**

```bash
git add -p
git commit -m "chore: post-batch cleanup"
```
