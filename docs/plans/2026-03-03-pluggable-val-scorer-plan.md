# Pluggable Val Scorer + ALFWorld Env Evaluation — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make the evaluator's val phase pluggable so ALFWorld (and future interactive benchmarks) can provide custom scoring logic, then rewrite ALFWorld to use real environment interaction.

**Architecture:** Split `_evaluate_val` into KB retrieval (shared) + downstream scoring (pluggable via `ValScorer` protocol on `Dataset`). ALFWorld provides an `ALFWorldValScorer` that runs TextWorld episodes. Train phase uses existing offline pipeline with expert trajectory text as `raw_text`.

**Tech Stack:** Python 3.12, alfworld/textworld (optional dep), litellm, pytest + syrupy

---

### Task 1: Add `metadata` field to `DataItem`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:33-44`
- Test: `tests/evolution/test_types.py`

**Step 1: Write the failing test**

Add to `tests/evolution/test_types.py`:

```python
from programmaticmemory.evolution.types import DataItem


class TestDataItemMetadata:
    def test_metadata_defaults_to_empty_dict(self):
        item = DataItem(raw_text="text", question="q", expected_answer="a")
        assert item.metadata == {}

    def test_metadata_accepts_dict(self):
        item = DataItem(raw_text="", question="q", expected_answer="a", metadata={"game_file": "/path/to/game.tw-pddl"})
        assert item.metadata["game_file"] == "/path/to/game.tw-pddl"

    def test_metadata_does_not_share_between_instances(self):
        a = DataItem(raw_text="", question="q1", expected_answer="a1")
        b = DataItem(raw_text="", question="q2", expected_answer="a2")
        a.metadata["key"] = "value"
        assert "key" not in b.metadata
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_types.py::TestDataItemMetadata -v`
Expected: FAIL — `DataItem.__init__() got an unexpected keyword argument 'metadata'`

**Step 3: Write minimal implementation**

In `src/programmaticmemory/evolution/types.py`, change `DataItem` (line 33-44):

```python
@dataclass
class DataItem:
    """A single benchmark data item.

    Train items with raw_text are batch-ingested as observations.
    Train items without raw_text use interactive QA (query->answer->feedback->write).
    Val items always use question+expected_answer for scoring.
    """

    raw_text: str
    question: str
    expected_answer: str
    metadata: dict = field(default_factory=dict)
```

**Step 4: Run test to verify it passes**

Run: `uv run pytest tests/evolution/test_types.py::TestDataItemMetadata -v`
Expected: PASS

**Step 5: Run existing tests to verify no regression**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS (metadata has default, existing code unaffected)

**Step 6: Commit**

```
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add metadata field to DataItem"
```

---

### Task 2: Add `ValScorer` protocol and `Dataset.val_scorer`

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:10-55`
- Test: `tests/evolution/test_types.py`

**Step 1: Write the failing test**

Add to `tests/evolution/test_types.py`:

```python
from programmaticmemory.evolution.types import DataItem, Dataset, ValScorer


class TestValScorer:
    def test_val_scorer_protocol_accepts_conforming_class(self):
        """Any class with score_batch method matching the protocol signature works."""

        class MyScorer:
            def score_batch(
                self,
                items: list[DataItem],
                retrieved: list[str],
                task_model: str,
                instruction_response: str,
            ) -> list[tuple[str, float]]:
                return [("answer", 1.0)] * len(items)

        scorer = MyScorer()
        items = [DataItem(raw_text="", question="q", expected_answer="a")]
        result = scorer.score_batch(items, ["retrieved"], "model", "instruction")
        assert result == [("answer", 1.0)]

    def test_dataset_val_scorer_defaults_to_none(self):
        ds = Dataset(train=[], val=[], test=[])
        assert ds.val_scorer is None

    def test_dataset_accepts_val_scorer(self):
        class MyScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response):
                return []

        ds = Dataset(train=[], val=[], test=[], val_scorer=MyScorer())
        assert ds.val_scorer is not None
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_types.py::TestValScorer -v`
Expected: FAIL — `cannot import name 'ValScorer' from 'programmaticmemory.evolution.types'`

**Step 3: Write minimal implementation**

In `src/programmaticmemory/evolution/types.py`, add after the `Scorer` protocol (after line 13):

```python
class ValScorer(Protocol):
    """Pluggable val scoring strategy.

    Replaces the default LLM answer generation + string-compare scoring.
    Receives items with their KB-retrieved strings and returns (output, score) pairs.
    """

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
    ) -> list[tuple[str, float]]: ...
```

In the `Dataset` dataclass, add after `scorer` (line 54):

```python
    val_scorer: ValScorer | None = None
```

Note: `ValScorer` references `DataItem` which is defined later. This works because of `from __future__ import annotations`.

**Step 4: Run tests to verify pass**

Run: `uv run pytest tests/evolution/test_types.py -v`
Expected: All PASS

**Step 5: Run all non-LLM tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

**Step 6: Commit**

```
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add ValScorer protocol and Dataset.val_scorer"
```

---

### Task 3: Refactor `_evaluate_val` into retrieve + score

This is the core refactor. Extract the shared KB retrieval logic from `_evaluate_val` so it can be reused by both the default LLM answer path and custom `ValScorer` implementations.

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py:163-180` (MemoryEvaluator.__init__) and `525-641` (_evaluate_val)
- Test: `tests/evolution/test_evaluator.py`

**Step 1: Write the failing test for val_scorer path**

Add to `tests/evolution/test_evaluator.py`:

```python
class TestValScorerIntegration:
    """Tests for the pluggable val_scorer path."""

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_receives_retrieved_memory(self, mock_litellm):
        """When val_scorer is set, it receives KB-retrieved strings instead of LLM answering."""
        received_items = []
        received_retrieved = []

        class CapturingScorer:
            def score_batch(self, items, retrieved, task_model, instruction_response):
                received_items.extend(items)
                received_retrieved.extend(retrieved)
                return [("custom_answer", 0.75)] * len(items)

        batch_mock = _make_batch_mock(
            [
                ['{"raw": "The sky is blue."}'],  # train: obs generation
                ['{"raw": "sky query"}'],  # val round 1: query generation
                # No round 2! val_scorer handles scoring, not LLM answer generation.
            ]
        )
        mock_litellm.batch_completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="The sky is blue.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="", question="What color is the sky?", expected_answer="blue")]

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
        # Only 2 batch calls (train obs + val query), NOT 3 (no val answer generation)
        assert len(batch_mock.captured_calls) == 2

    @patch("programmaticmemory.evolution.evaluator.litellm")
    def test_val_scorer_none_uses_default_path(self, mock_litellm, snapshot: SnapshotAssertion):
        """When val_scorer is None, existing LLM answer + scorer path is used."""
        batch_mock = _make_batch_mock(
            [
                ['{"raw": "Paris is capital of France."}'],
                ['{"raw": "capital of France"}'],
                ["Paris"],
            ]
        )
        mock_litellm.batch_completion = batch_mock

        program = KBProgram(source_code=INITIAL_KB_PROGRAM)
        train = [DataItem(raw_text="Paris is capital.", question="q", expected_answer="e")]
        val = [DataItem(raw_text="x", question="Capital of France?", expected_answer="Paris")]

        evaluator = MemoryEvaluator(task_model="mock/model", toolkit_config=_TEST_TOOLKIT_CONFIG)
        result = evaluator.evaluate(program, train, val)

        assert result.score == 1.0
        assert len(batch_mock.captured_calls) == 3  # train + val query + val answer
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestValScorerIntegration -v`
Expected: FAIL — `MemoryEvaluator.__init__() got an unexpected keyword argument 'val_scorer'`

**Step 3: Implement the refactor**

In `evaluator.py`, make these changes:

**3a. Add `val_scorer` param to `__init__` (line 170-180):**

```python
def __init__(
    self,
    scorer: Scorer | None = None,
    *,
    task_model: str,
    toolkit_config: ToolkitConfig,
    val_scorer: ValScorer | None = None,
) -> None:
    self.scorer = scorer or ExactMatchScorer()
    self.task_model = task_model
    self.toolkit_config = toolkit_config
    self.val_scorer = val_scorer
    self.logger = get_logger()
```

Add `ValScorer` to the imports from `types.py`.

**3b. Extract `_retrieve_for_val` from lines 538-558 of current `_evaluate_val`:**

```python
def _retrieve_for_val(
    self,
    kb: Any,
    query_cls: type,
    query_schema: str,
    val_data: list[DataItem],
    logs: list[str],
    *,
    instruction_query: str = "",
    instruction_response: str = "",
) -> list[_QuerySlot | None]:
    """Phase 1 of val: batch query generation + serial KB reads."""
    round1_messages = [
        [{"role": "user", "content": build_query_generation_prompt(item.question, query_schema, instruction_query)}]
        for item in val_data
    ]
    round1_responses = self._batch_llm_call(round1_messages, json_mode=True)
    return self._parse_queries_and_read(
        query_cls,
        kb,
        round1_messages,
        round1_responses,
        logs,
        log_prefix="Val",
        instruction_query=instruction_query,
        instruction_response=instruction_response,
    )
```

**3c. Extract `_default_answer_and_score` from lines 560-641 of current `_evaluate_val`:**

```python
def _default_answer_and_score(
    self,
    slots: list[_QuerySlot | None],
    val_data: list[DataItem],
    logs: list[str],
    toolkit: Toolkit,
    *,
    instruction_response: str = "",
) -> EvalResult:
    """Phase 2 default: batch LLM answer generation + scorer."""
    valid = [(i, s) for i, s in enumerate(slots) if s is not None]
    round2_messages = [
        [
            {"role": "user", "content": s.query_prompt},
            {"role": "assistant", "content": s.query_json},
            {"role": "user", "content": s.retrieved_prompt},
        ]
        for _i, s in valid
    ]
    round2_responses = self._batch_llm_call(round2_messages)

    scores: list[float] = []
    outputs: list[str] = []
    failed_cases: list[FailedCase] = []
    success_cases: list[FailedCase] = []
    log_snapshot = list(toolkit.logger.logs)

    valid_idx = 0
    for i, item in enumerate(val_data):
        slot = slots[i]
        if slot is None:
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(
                FailedCase(
                    question=item.question, output="", expected=item.expected_answer,
                    score=0.0, memory_logs=log_snapshot,
                )
            )
            continue

        answer = round2_responses[valid_idx]
        valid_idx += 1

        if answer is None:
            logs.append("Val answer generation failed (batch error)")
            scores.append(0.0)
            outputs.append("")
            failed_cases.append(
                FailedCase(
                    question=item.question, output="", expected=item.expected_answer,
                    score=0.0, memory_logs=log_snapshot,
                )
            )
            continue

        outputs.append(answer)
        score = self.scorer(answer, item.expected_answer)
        scores.append(score)
        conv = [
            {"role": "user", "content": slot.query_prompt},
            {"role": "assistant", "content": slot.query_json},
            {"role": "user", "content": slot.retrieved_prompt},
            {"role": "assistant", "content": answer},
        ]
        case = FailedCase(
            question=item.question, output=answer, expected=item.expected_answer,
            score=score, conversation_history=conv, memory_logs=log_snapshot,
        )
        if score < 1.0:
            failed_cases.append(case)
        else:
            success_cases.append(case)

    return self._build_eval_result(scores, outputs, failed_cases, success_cases, logs)
```

**3d. Add `_val_scorer_path`:**

```python
def _val_scorer_path(
    self,
    slots: list[_QuerySlot | None],
    val_data: list[DataItem],
    logs: list[str],
    toolkit: Toolkit,
    *,
    instruction_response: str = "",
) -> EvalResult:
    """Phase 2 custom: delegate to val_scorer.score_batch."""
    items = list(val_data)
    retrieved = [s.retrieved_str if s is not None else "" for s in slots]

    results = self.val_scorer.score_batch(items, retrieved, self.task_model, instruction_response)

    scores: list[float] = []
    outputs: list[str] = []
    failed_cases: list[FailedCase] = []
    success_cases: list[FailedCase] = []
    log_snapshot = list(toolkit.logger.logs)

    for i, (output, score) in enumerate(results):
        scores.append(score)
        outputs.append(output)
        case = FailedCase(
            question=val_data[i].question, output=output, expected=val_data[i].expected_answer,
            score=score, memory_logs=log_snapshot,
        )
        if score < 1.0:
            failed_cases.append(case)
        else:
            success_cases.append(case)

    return self._build_eval_result(scores, outputs, failed_cases, success_cases, logs)
```

**3e. Rewrite `_evaluate_val` as orchestrator:**

```python
def _evaluate_val(
    self,
    kb: Any,
    query_cls: type,
    query_schema: str,
    val_data: list[DataItem],
    logs: list[str],
    toolkit: Toolkit,
    *,
    instruction_query: str = "",
    instruction_response: str = "",
) -> EvalResult:
    """Two-phase val: (1) shared KB retrieval, (2) pluggable scoring."""
    if not val_data:
        return self._build_eval_result([], [], [], [], logs)

    # Phase 1: shared KB retrieval
    slots = self._retrieve_for_val(
        kb, query_cls, query_schema, val_data, logs,
        instruction_query=instruction_query, instruction_response=instruction_response,
    )

    # Phase 2: pluggable scoring
    if self.val_scorer:
        result = self._val_scorer_path(
            slots, val_data, logs, toolkit, instruction_response=instruction_response,
        )
    else:
        result = self._default_answer_and_score(
            slots, val_data, logs, toolkit, instruction_response=instruction_response,
        )

    self.logger.log(
        f"Val: complete — score={result.score:.3f}, {len(result.failed_cases)}/{len(val_data)} failed",
        header="EVAL",
    )
    return result
```

**Step 4: Run new tests**

Run: `uv run pytest tests/evolution/test_evaluator.py::TestValScorerIntegration -v`
Expected: PASS

**Step 5: Run ALL existing tests to verify no regression**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS — the refactor is a pure method extraction, LLM call patterns are identical, so snapshot tests should not need updating.

If snapshots fail (unlikely — only method boundaries changed, not call sequences), run:
`uv run pytest tests/evolution/test_evaluator.py --snapshot-update -v`

**Step 6: Commit**

```
git add src/programmaticmemory/evolution/evaluator.py tests/evolution/test_evaluator.py
git commit -m "refactor: split _evaluate_val into retrieve + pluggable scoring"
```

---

### Task 4: Wire `val_scorer` through `__main__.py`

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py:121-128`

**Step 1: Add val_scorer to evaluator construction**

Change lines 121-128:

```python
    # Configure
    scorer = dataset.scorer or ExactMatchScorer()
    toolkit_config = ToolkitConfig(llm_model=args.toolkit_model)
    evaluator = MemoryEvaluator(
        scorer=scorer,
        task_model=args.task_model,
        toolkit_config=toolkit_config,
        val_scorer=dataset.val_scorer,
    )
```

**Step 2: Run existing tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

**Step 3: Commit**

```
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: wire dataset.val_scorer to evaluator in CLI"
```

---

### Task 5: Rewrite ALFWorld benchmark — data loading

**Files:**
- Modify: `src/programmaticmemory/benchmarks/alfworld.py`
- Test: `tests/evolution/test_benchmark.py`

**Context:** The current alfworld.py loads `valid_unseen` and creates static QA items with key-element matching. We need to:
1. Also download `train` split expert trajectories
2. Format trajectories as `raw_text` for the offline pipeline
3. Create val items with `metadata={"game_file": path}` for env interaction

**Step 1: Check what the existing benchmark test looks like**

Read `tests/evolution/test_benchmark.py` to understand the test pattern, then add ALFWorld data loading tests.

**Step 2: Write test for trajectory formatting**

```python
class TestALFWorldTrajectoryFormat:
    def test_format_trajectory_produces_readable_text(self):
        """Expert trajectory should format as ACTION/OBSERVATION pairs."""
        from programmaticmemory.benchmarks.alfworld import _format_trajectory

        actions_and_obs = [
            {"action": "go to countertop 1", "observation": "You arrive at countertop 1. On it, you see a mug 1."},
            {"action": "take mug 1 from countertop 1", "observation": "You pick up the mug 1 from the countertop 1."},
        ]
        text = _format_trajectory("Put a hot mug in the cabinet.", actions_and_obs)
        assert "Task: Put a hot mug in the cabinet." in text
        assert "go to countertop 1" in text
        assert "You pick up the mug 1" in text
```

**Step 3: Implement data loading**

Rewrite `src/programmaticmemory/benchmarks/alfworld.py`. Key changes:
- `ensure_data()`: also download train split data (filtered to task types that have `game.tw-pddl`)
- `_format_trajectory(task_desc, steps)`: format action/observation pairs into readable text
- `_parse_trials()`: return items with `metadata={"game_file": str(pddl_path), "task_type": ...}`
- Train items: `DataItem(raw_text=trajectory_text, question="", expected_answer="")`
- Val items: `DataItem(raw_text="", question=objective, expected_answer="", metadata={...})`
- Remove `_KEY_ELEMENTS` and `_derive_expected` (no longer needed)
- `val_scorer` is set to `ALFWorldValScorer(...)` (implemented in Task 6)

**Step 4: Run test**

Run: `uv run pytest tests/evolution/test_benchmark.py::TestALFWorldTrajectoryFormat -v`
Expected: PASS

**Step 5: Commit**

```
git add src/programmaticmemory/benchmarks/alfworld.py tests/evolution/test_benchmark.py
git commit -m "feat: rewrite alfworld data loading with expert trajectories"
```

---

### Task 6: Implement `ALFWorldValScorer`

**Files:**
- Modify: `src/programmaticmemory/benchmarks/alfworld.py`
- Test: `tests/evolution/test_benchmark.py`

**Context:** This is the environment interaction loop. Each val item runs a TextWorld episode using retrieved KB tips as procedural context.

**Step 1: Write test with mocked environment**

```python
class TestALFWorldValScorer:
    def test_score_batch_with_mock_env(self):
        """ALFWorldValScorer should run episodes and return binary success."""
        from programmaticmemory.benchmarks.alfworld import ALFWorldValScorer

        # Mock env that succeeds after 2 steps
        class MockEnv:
            def __init__(self):
                self.step_count = 0
            def reset(self):
                return "You are in a room.", {"admissible_commands": ["go to desk 1", "look"]}
            def step(self, action):
                self.step_count += 1
                if self.step_count >= 2:
                    return "Task complete.", 1.0, True, {"admissible_commands": []}
                return "You see a desk.", 0.0, False, {"admissible_commands": ["take lamp", "go to shelf 1"]}
            def close(self):
                pass

        scorer = ALFWorldValScorer(max_steps=50)
        items = [DataItem(raw_text="", question="Find the lamp.", expected_answer="", metadata={"game_file": "/fake"})]
        retrieved = ["To find objects, check desks and shelves."]

        # Patch env creation and LLM
        with patch.object(scorer, "_create_env", return_value=MockEnv()):
            with patch.object(scorer, "_select_action", side_effect=["go to desk 1", "take lamp"]):
                results = scorer.score_batch(items, retrieved, "mock/model", "instruction")

        assert len(results) == 1
        output, score = results[0]
        assert score == 1.0

    def test_score_batch_failure_returns_zero(self):
        """Episode that times out (hits max_steps) returns score 0."""
        from programmaticmemory.benchmarks.alfworld import ALFWorldValScorer

        class NeverDoneEnv:
            def reset(self):
                return "Room.", {"admissible_commands": ["look"]}
            def step(self, action):
                return "Nothing.", 0.0, False, {"admissible_commands": ["look"]}
            def close(self):
                pass

        scorer = ALFWorldValScorer(max_steps=3)
        items = [DataItem(raw_text="", question="Do something.", expected_answer="", metadata={"game_file": "/fake"})]
        retrieved = ["No useful tips."]

        with patch.object(scorer, "_create_env", return_value=NeverDoneEnv()):
            with patch.object(scorer, "_select_action", return_value="look"):
                results = scorer.score_batch(items, retrieved, "mock/model", "instruction")

        assert results[0][1] == 0.0
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_benchmark.py::TestALFWorldValScorer -v`
Expected: FAIL — `cannot import name 'ALFWorldValScorer'`

**Step 3: Implement `ALFWorldValScorer`**

Add to `src/programmaticmemory/benchmarks/alfworld.py`:

```python
class ALFWorldValScorer:
    """Runs ALFWorld TextWorld episodes and scores by binary task success."""

    def __init__(self, max_steps: int = 50) -> None:
        self.max_steps = max_steps

    def score_batch(
        self,
        items: list[DataItem],
        retrieved: list[str],
        task_model: str,
        instruction_response: str,
    ) -> list[tuple[str, float]]:
        results: list[tuple[str, float]] = []
        for item, tips in zip(items, retrieved, strict=True):
            game_file = item.metadata["game_file"]
            output, score = self._run_episode(game_file, item.question, tips, task_model)
            results.append((output, score))
        return results

    def _run_episode(
        self, game_file: str, objective: str, tips: str, task_model: str
    ) -> tuple[str, float]:
        """Run a single ALFWorld episode. Returns (trajectory_text, reward)."""
        env = self._create_env(game_file)
        try:
            obs, info = env.reset()
            history: list[str] = [f"OBSERVATION: {obs}"]
            reward = 0.0
            done = False

            for step in range(self.max_steps):
                admissible = info.get("admissible_commands", [])
                if not admissible or done:
                    break
                action = self._select_action(objective, tips, history, admissible, task_model)
                obs, reward, done, info = env.step(action)
                history.append(f"ACTION: {action}")
                history.append(f"OBSERVATION: {obs}")

            trajectory = "\n".join(history)
            score = 1.0 if done and float(reward) == 1.0 else 0.0
            return trajectory, score
        finally:
            env.close()

    def _create_env(self, game_file: str):
        """Create a TextWorld environment for the given game file."""
        import alfworld.agents.environment as environment
        import textworld

        request_infos = textworld.EnvInfos(
            feedback=True, description=True, inventory=True,
            admissible_commands=True, objective=True,
        )
        env = textworld.start(game_file, infos=request_infos)
        return env

    def _select_action(
        self,
        objective: str,
        tips: str,
        history: list[str],
        admissible: list[str],
        task_model: str,
    ) -> str:
        """Call LLM to select an action from admissible commands."""
        import litellm

        history_text = "\n".join(history[-20:])  # keep recent history
        admissible_text = "\n".join(f"- {cmd}" for cmd in admissible)
        prompt = (
            f"You are controlling a text-based ALFWorld environment.\n"
            f"Choose the NEXT action as ONE text command.\n"
            f"You MUST choose from the admissible actions and copy it EXACTLY.\n\n"
            f"Goal: {objective}\n\n"
            f"Procedural tips from knowledge base:\n{tips}\n\n"
            f"Recent interaction history:\n{history_text}\n\n"
            f"Admissible actions (choose exactly ONE):\n{admissible_text}\n\n"
            f"Output exactly one line: the chosen action."
        )
        response = litellm.completion(
            model=task_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=64,
            caching=True,
        )
        action_text = response.choices[0].message.content.strip()
        # Match against admissible: exact match, then substring, then fallback
        for cmd in admissible:
            if cmd == action_text:
                return cmd
        for cmd in admissible:
            if cmd in action_text or action_text in cmd:
                return cmd
        return admissible[0] if admissible else "look"
```

**Step 4: Run tests**

Run: `uv run pytest tests/evolution/test_benchmark.py::TestALFWorldValScorer -v`
Expected: PASS

**Step 5: Commit**

```
git add src/programmaticmemory/benchmarks/alfworld.py tests/evolution/test_benchmark.py
git commit -m "feat: implement ALFWorldValScorer with env interaction loop"
```

---

### Task 7: Add `alfworld` optional dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add optional dependency group**

In `pyproject.toml`, add after the `[project.optional-dependencies]` `full` group:

```toml
alfworld = [
    "alfworld[full]>=0.2.2",
]
```

Also add a pytest marker in `[tool.pytest.ini_options]`:

```toml
markers = [
    "uses_chroma: test requires a real ChromaDB EphemeralClient",
    "llm: tests that call real LLM APIs (use disk cache for replay)",
    "alfworld: tests that require alfworld package",
]
```

**Step 2: Commit**

```
git add pyproject.toml
git commit -m "build: add alfworld as optional dependency"
```

---

### Task 8: Wire ALFWorldValScorer into Dataset and guard imports

**Files:**
- Modify: `src/programmaticmemory/benchmarks/alfworld.py`

**Step 1: Update `load_alfworld` to use ALFWorldValScorer**

In the `load_alfworld()` function, change the return statement:

```python
    # Only use env scorer if alfworld is available
    val_scorer = None
    try:
        import alfworld  # noqa: F401
        val_scorer = ALFWorldValScorer(max_steps=50)
    except ImportError:
        pass  # Fall back to default LLM answer path if alfworld not installed

    return Dataset(
        train=train, val=val, test=[],
        scorer=ExactMatchScorer(),  # fallback scorer for default path
        val_scorer=val_scorer,
        available_categories=all_categories,
    )
```

**Step 2: Guard `_create_env` import**

The `alfworld` and `textworld` imports in `ALFWorldValScorer._create_env` are already inside the method (lazy import), so they only fail when actually calling `score_batch`. This is fine — if someone runs evolution with `--dataset alfworld` without the package installed, they get a clear `ImportError`.

**Step 3: Run all non-LLM tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: All PASS

**Step 4: Commit**

```
git add src/programmaticmemory/benchmarks/alfworld.py
git commit -m "feat: wire ALFWorldValScorer into dataset loading"
```

---

### Task 9: Update CLAUDE.md and memory

**Files:**
- Modify: `CLAUDE.md` (project root)

**Step 1: Update CLAUDE.md**

Update the ALFWorld entry in the benchmarks list in the Architecture section:
- Remove mention of `ExactMatchScorer` for ALFWorld
- Add note about `ValScorer` protocol
- Update `evaluator.py` description to mention pluggable val scoring

Add to the Conventions section:
- `DataItem.metadata` carries benchmark-specific data (e.g., game file paths)
- `Dataset.val_scorer` overrides the default LLM answer + string-compare val scoring
- ALFWorld requires `pip install -e ".[alfworld]"` for real env interaction

**Step 2: Commit**

```
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md for pluggable val scorer and ALFWorld env eval"
```
