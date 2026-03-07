# IRT-Guided Case Selection Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the "first-come, first-served" case truncation in the reflection prompt with IRT-guided selection that picks the most diagnostically informative failed and success cases.

**Architecture:** A new `ResponseMatrix` dataclass accumulates binary scores across generations. A new `selection.py` module computes classical IRT statistics (difficulty, discrimination, item information) and selects the highest-information cases. The reflector calls `select_cases()` before building case dicts for the prompt. The loop creates and maintains the response matrix.

**Tech Stack:** Pure Python (math, statistics), no new dependencies. Pytest + syrupy for testing.

**Design doc:** `docs/plans/2026-03-06-irt-case-selection-design.md`

---

### Task 1: ResponseMatrix dataclass — core data structure

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py` (add after `EvolutionState`, ~line 129)
- Test: `tests/evolution/test_types.py`

**Step 1: Write failing tests for ResponseMatrix**

Add to `tests/evolution/test_types.py`:

```python
import math

from programmaticmemory.evolution.types import ResponseMatrix


class TestResponseMatrix:
    def test_construction(self):
        rm = ResponseMatrix(num_items=5)
        assert rm.num_items == 5
        assert rm.rows == []
        assert rm.num_rows == 0

    def test_append_binarizes_scores(self):
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.5, 0.0])
        assert rm.rows == [[1, 0, 0]]

    def test_append_threshold_default_is_1(self):
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.99, 1.0])
        assert rm.rows == [[1, 0, 1]]

    def test_append_custom_threshold(self):
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.5, 0.0], threshold=0.5)
        assert rm.rows == [[1, 1, 0]]

    def test_num_rows(self):
        rm = ResponseMatrix(num_items=2)
        rm.append([1.0, 0.0])
        rm.append([0.0, 1.0])
        assert rm.num_rows == 2

    def test_item_difficulty(self):
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.0, 0.0])  # [1, 0, 0]
        rm.append([1.0, 0.0, 1.0])  # [1, 0, 1]
        rm.append([1.0, 1.0, 0.0])  # [1, 1, 0]
        # mean col0=1.0, col1=0.33, col2=0.33
        # difficulty = 1 - mean
        d = rm.item_difficulty()
        assert len(d) == 3
        assert d[0] == pytest.approx(0.0, abs=0.01)      # always correct -> easy
        assert d[1] == pytest.approx(0.667, abs=0.01)     # usually wrong -> hard
        assert d[2] == pytest.approx(0.667, abs=0.01)

    def test_item_discrimination(self):
        rm = ResponseMatrix(num_items=3)
        # respondent 1: total=1, scores=[1, 0, 0]
        # respondent 2: total=2, scores=[1, 0, 1]
        # respondent 3: total=2, scores=[1, 1, 0]
        # respondent 4: total=3, scores=[1, 1, 1]
        rm.append([1.0, 0.0, 0.0])
        rm.append([1.0, 0.0, 1.0])
        rm.append([1.0, 1.0, 0.0])
        rm.append([1.0, 1.0, 1.0])
        disc = rm.item_discrimination()
        assert len(disc) == 3
        # Item 0: always 1 regardless of total -> 0 variance -> disc=0
        assert disc[0] == pytest.approx(0.0, abs=0.01)
        # Items 1 and 2 have positive correlation with total
        assert disc[1] > 0
        assert disc[2] > 0

    def test_item_discrimination_constant_column_returns_zero(self):
        rm = ResponseMatrix(num_items=2)
        rm.append([1.0, 0.0])
        rm.append([1.0, 0.0])
        rm.append([1.0, 0.0])
        disc = rm.item_discrimination()
        # Column 0 always 1, column 1 always 0 -> both have zero variance
        assert disc[0] == 0.0
        assert disc[1] == 0.0

    def test_item_information(self):
        rm = ResponseMatrix(num_items=2)
        rm.append([1.0, 0.0])
        rm.append([1.0, 0.0])
        rm.append([1.0, 1.0])
        rm.append([0.0, 1.0])
        info = rm.item_information(theta=0.5)
        assert len(info) == 2
        # Item information should be non-negative
        assert all(i >= 0 for i in info)

    def test_item_information_zero_discrimination(self):
        """Items with zero discrimination should have zero information."""
        rm = ResponseMatrix(num_items=2)
        rm.append([1.0, 0.0])
        rm.append([1.0, 0.0])
        rm.append([1.0, 0.0])
        info = rm.item_information(theta=0.5)
        # Both columns have zero variance -> disc=0 -> info=0
        assert info[0] == 0.0
        assert info[1] == 0.0
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_types.py::TestResponseMatrix -v`
Expected: FAIL with `ImportError: cannot import name 'ResponseMatrix'`

**Step 3: Implement ResponseMatrix**

Add to `src/programmaticmemory/evolution/types.py` after the `EvolutionState` class:

```python
@dataclass
class ResponseMatrix:
    """Accumulated binary response matrix for IRT parameter estimation.

    Rows = program generations (respondents), columns = val cases (items).
    Used to compute classical IRT statistics for informative case selection.
    """

    num_items: int
    rows: list[list[int]] = field(default_factory=list)

    def append(self, per_case_scores: list[float], threshold: float = 1.0) -> None:
        """Binarize scores and append as a new row."""
        self.rows.append([1 if s >= threshold else 0 for s in per_case_scores])

    @property
    def num_rows(self) -> int:
        """Number of accumulated respondents (generations)."""
        return len(self.rows)

    def item_difficulty(self) -> list[float]:
        """Compute difficulty for each item: 1 - mean(correct)."""
        if not self.rows:
            return [0.5] * self.num_items
        result = []
        for j in range(self.num_items):
            col = [row[j] for row in self.rows]
            result.append(1.0 - (sum(col) / len(col)))
        return result

    def item_discrimination(self) -> list[float]:
        """Compute point-biserial correlation for each item vs total score."""
        import math

        if len(self.rows) < 2:
            return [0.0] * self.num_items

        totals = [sum(row) for row in self.rows]
        n = len(self.rows)
        mean_total = sum(totals) / n
        var_total = sum((t - mean_total) ** 2 for t in totals) / n

        if var_total == 0:
            return [0.0] * self.num_items

        sd_total = math.sqrt(var_total)
        result = []
        for j in range(self.num_items):
            col = [row[j] for row in self.rows]
            col_mean = sum(col) / n
            col_var = sum((x - col_mean) ** 2 for x in col) / n
            if col_var == 0:
                result.append(0.0)
                continue
            cov = sum((col[i] - col_mean) * (totals[i] - mean_total) for i in range(n)) / n
            result.append(cov / (math.sqrt(col_var) * sd_total))
        return result

    def item_information(self, theta: float) -> list[float]:
        """Compute 2PL item information at given ability level."""
        import math

        disc = self.item_discrimination()
        diff = self.item_difficulty()
        result = []
        for j in range(self.num_items):
            a = disc[j]
            if a <= 0:
                result.append(0.0)
                continue
            b = 1.0 - diff[j]  # convert difficulty to "easiness" for IRT scale
            logit = a * (theta - b)
            logit = max(-500, min(500, logit))  # clamp to avoid overflow
            p = 1.0 / (1.0 + math.exp(-logit))
            result.append(a**2 * p * (1 - p))
        return result
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_types.py::TestResponseMatrix -v`
Expected: all PASS

**Step 5: Commit**

```bash
git add src/programmaticmemory/evolution/types.py tests/evolution/test_types.py
git commit -m "feat: add ResponseMatrix dataclass for IRT parameter estimation"
```

---

### Task 2: select_cases() function — IRT-based selection logic

**Files:**
- Create: `src/programmaticmemory/evolution/selection.py`
- Create: `tests/evolution/test_selection.py`

**Step 1: Write failing tests for select_cases**

Create `tests/evolution/test_selection.py`:

```python
"""Tests for evolution/selection.py — IRT-guided case selection."""

from __future__ import annotations

from programmaticmemory.evolution.selection import select_cases
from programmaticmemory.evolution.types import FailedCase, ResponseMatrix


def _fc(question: str, score: float) -> FailedCase:
    """Shorthand for creating a FailedCase."""
    return FailedCase(question=question, output="o", expected="e", score=score)


class TestSelectCasesFallback:
    """When response_matrix has < min_respondents rows, use score-based fallback."""

    def test_no_matrix_returns_score_ascending(self):
        cases = [_fc("q1", 0.5), _fc("q2", 0.0), _fc("q3", 0.3)]
        result = select_cases(cases, response_matrix=None, max_n=2)
        # Sorted by score ascending: q2(0.0), q3(0.3)
        assert [c.question for c in result] == ["q2", "q3"]

    def test_empty_matrix_returns_score_ascending(self):
        rm = ResponseMatrix(num_items=3)
        cases = [_fc("q1", 0.5), _fc("q2", 0.0), _fc("q3", 0.3)]
        result = select_cases(cases, response_matrix=rm, max_n=2)
        assert [c.question for c in result] == ["q2", "q3"]

    def test_insufficient_rows_returns_score_ascending(self):
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.0, 0.5])
        rm.append([0.0, 1.0, 0.0])
        # 2 rows < min_respondents=3 (default)
        cases = [_fc("q1", 0.5), _fc("q2", 0.0), _fc("q3", 0.3)]
        result = select_cases(cases, response_matrix=rm, max_n=2)
        assert [c.question for c in result] == ["q2", "q3"]

    def test_max_n_larger_than_cases(self):
        cases = [_fc("q1", 0.0)]
        result = select_cases(cases, response_matrix=None, max_n=5)
        assert len(result) == 1

    def test_empty_cases_returns_empty(self):
        result = select_cases([], response_matrix=None, max_n=2)
        assert result == []


class TestSelectCasesIRT:
    """When response_matrix has >= min_respondents rows, use IRT selection."""

    def _make_matrix_4_respondents(self) -> ResponseMatrix:
        """Create a 4-respondent matrix with 5 items of varying difficulty/discrimination."""
        rm = ResponseMatrix(num_items=5)
        # Item 0: always correct (easy, low disc)
        # Item 1: good programs get it, bad don't (high disc)
        # Item 2: random/noisy (low disc)
        # Item 3: always wrong (hard, low disc)
        # Item 4: moderate difficulty, high disc
        rm.append([1.0, 0.0, 1.0, 0.0, 0.0])  # bad program  (total=2)
        rm.append([1.0, 0.0, 0.0, 0.0, 0.0])  # bad program  (total=1)
        rm.append([1.0, 1.0, 0.0, 0.0, 1.0])  # good program (total=3)
        rm.append([1.0, 1.0, 1.0, 0.0, 1.0])  # good program (total=4)
        return rm

    def test_irt_selects_high_information_cases(self):
        rm = self._make_matrix_4_respondents()
        # All 5 items as "failed" cases with case_indices mapping to matrix columns
        cases = [_fc(f"q{i}", 0.0) for i in range(5)]
        case_indices = [0, 1, 2, 3, 4]
        result = select_cases(cases, response_matrix=rm, max_n=2, case_indices=case_indices)
        # High-disc items (1, 4) should be preferred over low-disc items (0, 2, 3)
        selected_questions = {c.question for c in result}
        assert "q1" in selected_questions or "q4" in selected_questions

    def test_irt_filters_negative_discrimination(self):
        rm = ResponseMatrix(num_items=3)
        # Item 0: positive disc (good programs pass, bad fail)
        # Item 1: negative disc (bad programs pass, good fail — noisy)
        # Item 2: zero disc
        rm.append([0.0, 1.0, 1.0])  # total=2
        rm.append([0.0, 1.0, 0.0])  # total=1
        rm.append([1.0, 0.0, 0.0])  # total=1
        rm.append([1.0, 0.0, 1.0])  # total=2
        cases = [_fc("q0", 0.0), _fc("q1", 0.0), _fc("q2", 0.0)]
        case_indices = [0, 1, 2]
        result = select_cases(cases, response_matrix=rm, max_n=3, case_indices=case_indices)
        # Items with disc <= 0 are filtered out
        questions = [c.question for c in result]
        assert "q1" not in questions  # negative discrimination

    def test_case_indices_map_cases_to_matrix_columns(self):
        rm = ResponseMatrix(num_items=5)
        rm.append([1.0, 0.0, 0.0, 1.0, 0.0])
        rm.append([1.0, 1.0, 0.0, 0.0, 0.0])
        rm.append([1.0, 0.0, 1.0, 0.0, 1.0])
        rm.append([1.0, 1.0, 1.0, 1.0, 1.0])
        # Only pass 2 cases that map to columns 1 and 4
        cases = [_fc("q_col1", 0.0), _fc("q_col4", 0.0)]
        case_indices = [1, 4]
        result = select_cases(cases, response_matrix=rm, max_n=2, case_indices=case_indices)
        assert len(result) <= 2

    def test_min_respondents_override(self):
        rm = ResponseMatrix(num_items=2)
        rm.append([1.0, 0.0])
        rm.append([0.0, 1.0])
        cases = [_fc("q0", 0.0), _fc("q1", 0.5)]
        case_indices = [0, 1]
        # With min_respondents=2, 2 rows is enough for IRT
        result = select_cases(
            cases, response_matrix=rm, max_n=1,
            case_indices=case_indices, min_respondents=2,
        )
        assert len(result) == 1

    def test_no_case_indices_inferred_from_position(self):
        """When case_indices is None, cases map to columns 0..N-1."""
        rm = ResponseMatrix(num_items=3)
        rm.append([1.0, 0.0, 0.0])
        rm.append([1.0, 0.0, 1.0])
        rm.append([1.0, 1.0, 0.0])
        rm.append([1.0, 1.0, 1.0])
        cases = [_fc("q0", 0.0), _fc("q1", 0.0), _fc("q2", 0.0)]
        # No case_indices -> inferred as [0, 1, 2]
        result = select_cases(cases, response_matrix=rm, max_n=2)
        assert len(result) <= 2
```

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_selection.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'programmaticmemory.evolution.selection'`

**Step 3: Implement select_cases**

Create `src/programmaticmemory/evolution/selection.py`:

```python
"""IRT-guided case selection for reflection prompts."""

from __future__ import annotations

from programmaticmemory.evolution.types import FailedCase, ResponseMatrix

_DEFAULT_MIN_RESPONDENTS = 3


def select_cases(
    cases: list[FailedCase],
    response_matrix: ResponseMatrix | None,
    max_n: int,
    case_indices: list[int] | None = None,
    min_respondents: int = _DEFAULT_MIN_RESPONDENTS,
) -> list[FailedCase]:
    """Select the most informative cases for the reflection prompt.

    When the response matrix has enough rows (>= min_respondents), uses IRT
    item information to rank cases. Otherwise, falls back to score-ascending
    sort (worst scores first).

    Args:
        cases: Failed or success cases from the current evaluation.
        response_matrix: Accumulated binary response matrix, or None.
        max_n: Maximum number of cases to return.
        case_indices: Maps each case to its column index in the response matrix.
            If None, uses positional indices [0, 1, 2, ...].
        min_respondents: Minimum rows in response_matrix to activate IRT.
    """
    if not cases:
        return []

    if max_n >= len(cases):
        return list(cases)

    # Fallback: sort by score ascending (worst first)
    if response_matrix is None or response_matrix.num_rows < min_respondents:
        return sorted(cases, key=lambda c: c.score)[:max_n]

    # IRT path
    if case_indices is None:
        case_indices = list(range(len(cases)))

    # Estimate current program ability from the latest row
    latest_row = response_matrix.rows[-1]
    theta = sum(latest_row) / len(latest_row) if latest_row else 0.5

    # Compute item information for all val items
    info = response_matrix.item_information(theta)
    disc = response_matrix.item_discrimination()

    # Score each case by its item information, filtering out non-discriminating items
    scored: list[tuple[float, int]] = []
    for i, case in enumerate(cases):
        col_idx = case_indices[i]
        if col_idx < len(disc) and disc[col_idx] > 0:
            scored.append((info[col_idx], i))

    # Sort by information descending
    scored.sort(key=lambda x: x[0], reverse=True)

    result = [cases[idx] for _, idx in scored[:max_n]]

    # If we filtered out too many (all negative disc), fill from score-ascending fallback
    if len(result) < max_n:
        selected_indices = {idx for _, idx in scored[:max_n]}
        remaining = [
            (cases[i].score, i) for i in range(len(cases)) if i not in selected_indices
        ]
        remaining.sort()
        for _, idx in remaining:
            if len(result) >= max_n:
                break
            result.append(cases[idx])

    return result
```

**Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/evolution/test_selection.py -v`
Expected: all PASS

**Step 5: Run linting**

Run: `uv run ruff check src/programmaticmemory/evolution/selection.py tests/evolution/test_selection.py`
Expected: no errors

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/selection.py tests/evolution/test_selection.py
git commit -m "feat: add IRT-guided case selection for reflection prompts"
```

---

### Task 3: Integrate into reflector.py — accept and use ResponseMatrix

**Files:**
- Modify: `src/programmaticmemory/evolution/reflector.py:91-128` (signature + case selection)
- Test: `tests/evolution/test_reflector.py`

**Step 1: Write failing test for reflector with response_matrix**

Add to `tests/evolution/test_reflector.py`, inside `class TestReflector`:

```python
    @patch("programmaticmemory.evolution.reflector.litellm")
    def test_reflection_with_response_matrix_selects_cases(self, mock_litellm, snapshot: SnapshotAssertion):
        """When response_matrix is provided with enough rows, IRT selection is used."""
        captured_messages = []

        def capture_completion(*args, **kwargs):
            captured_messages.append(kwargs.get("messages", []))
            mock_resp = MagicMock()
            mock_resp.choices = [MagicMock()]
            mock_resp.choices[0].message.content = "No code."
            return mock_resp

        mock_litellm.completion = capture_completion

        current = KBProgram(source_code="code here")

        # Create 5 failed cases
        failed = [
            FailedCase(question=f"Q{i}?", output=f"wrong_{i}", expected=f"right_{i}", score=0.0)
            for i in range(5)
        ]

        eval_result = EvalResult(
            score=0.2,
            per_case_scores=[0.0] * 5,
            failed_cases=failed,
        )

        # Response matrix with enough rows to trigger IRT
        rm = ResponseMatrix(num_items=5)
        rm.append([1.0, 0.0, 0.0, 1.0, 0.0])
        rm.append([1.0, 1.0, 0.0, 0.0, 0.0])
        rm.append([1.0, 0.0, 1.0, 0.0, 1.0])
        rm.append([1.0, 1.0, 1.0, 1.0, 1.0])

        config = ReflectionPromptConfig(max_failed_cases=2)
        reflector = Reflector(model="mock/model", prompt_config=config)
        reflector.reflect_and_mutate(current, eval_result, iteration=1, response_matrix=rm)

        assert len(captured_messages) == 1
        user_content = captured_messages[0][0]["content"]
        # Should contain exactly 2 failed cases (max_failed_cases=2)
        # The specific cases selected depend on IRT, but we verify count
        case_count = user_content.count('<case id="')
        assert case_count == 2
        assert captured_messages == snapshot
```

Also add the `ResponseMatrix` import to the file:

```python
from programmaticmemory.evolution.types import EvalResult, FailedCase, KBProgram, ResponseMatrix
```

**Step 2: Run test to verify it fails**

Run: `uv run pytest tests/evolution/test_reflector.py::TestReflector::test_reflection_with_response_matrix_selects_cases -v`
Expected: FAIL (signature doesn't accept `response_matrix` yet)

**Step 3: Modify reflector.py**

In `src/programmaticmemory/evolution/reflector.py`:

1. Add import at top (after existing imports):

```python
from programmaticmemory.evolution.selection import select_cases
from programmaticmemory.evolution.types import EvalResult, KBProgram, ResponseMatrix
```

2. Update the `reflect_and_mutate` signature (line 91-96):

```python
    @weave.op()
    def reflect_and_mutate(
        self,
        current: KBProgram,
        eval_result: EvalResult,
        iteration: int,
        response_matrix: ResponseMatrix | None = None,
    ) -> KBProgram | None:
```

3. Replace the case dict building (lines 102-128) to use `select_cases`:

```python
        # Select the most informative cases via IRT (or fallback)
        max_failed = (self.prompt_config or ReflectionPromptConfig()).max_failed_cases
        max_success = (self.prompt_config or ReflectionPromptConfig()).max_success_cases

        # Build case_indices: failed/success cases map to val_data positions
        # EvalResult stores cases in val_data order, so case index = position in
        # per_case_scores. We need to recover each case's original val index.
        # Failed cases are those with score < 1.0, in order; success >= 1.0, in order.
        failed_indices = [i for i, s in enumerate(eval_result.per_case_scores) if s < 1.0]
        success_indices = [i for i, s in enumerate(eval_result.per_case_scores) if s >= 1.0]

        selected_failed = select_cases(
            eval_result.failed_cases,
            response_matrix=response_matrix,
            max_n=max_failed,
            case_indices=failed_indices if response_matrix else None,
        )
        selected_success = select_cases(
            eval_result.success_cases,
            response_matrix=response_matrix,
            max_n=max_success,
            case_indices=success_indices if response_matrix else None,
        )

        # Build dicts for the prompt
        failed_dicts = []
        for fc in selected_failed:
            failed_dicts.append(
                {
                    "question": fc.question,
                    "output": fc.output,
                    "expected": fc.expected,
                    "score": fc.score,
                    "conversation_history": fc.conversation_history,
                    "memory_logs": fc.memory_logs,
                }
            )

        success_dicts = []
        for sc in selected_success:
            success_dicts.append(
                {
                    "question": sc.question,
                    "output": sc.output,
                    "expected": sc.expected,
                    "score": sc.score,
                    "conversation_history": sc.conversation_history,
                    "memory_logs": sc.memory_logs,
                }
            )
```

**Step 4: Run the new test**

Run: `uv run pytest tests/evolution/test_reflector.py::TestReflector::test_reflection_with_response_matrix_selects_cases -v --snapshot-update`
Expected: PASS, new snapshot created

**Step 5: Run ALL reflector tests to check nothing broke**

Run: `uv run pytest tests/evolution/test_reflector.py -v --snapshot-update`
Expected: all PASS. Some existing snapshots may update because the case selection path changed — review diffs to confirm only the case-building logic changed, not the prompt structure.

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/reflector.py tests/evolution/test_reflector.py tests/evolution/__snapshots__/
git commit -m "feat: integrate IRT case selection into reflector"
```

---

### Task 4: Integrate into loop.py — create and maintain ResponseMatrix

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py:10-16,78,112` (import, create matrix, append rows, pass to reflector)
- Test: `tests/evolution/test_loop.py`

**Step 1: Write failing test for loop passing response_matrix**

Add to `tests/evolution/test_loop.py`:

```python
class TestEvolutionLoopResponseMatrix:
    """Tests for ResponseMatrix integration in the evolution loop."""

    def test_response_matrix_passed_to_reflector(self):
        """Loop should create a ResponseMatrix and pass it to reflect_and_mutate."""
        dataset = _make_dataset()
        child_program = KBProgram(source_code="improved", generation=1)

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, per_case_scores=[0.3], failed_cases=[
                FailedCase(question="q", output="o", expected="e", score=0.3)
            ]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.return_value = child_program
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=1,
        )
        loop.run()

        # Verify response_matrix was passed to reflect_and_mutate
        call_kwargs = reflector.reflect_and_mutate.call_args
        assert "response_matrix" in call_kwargs.kwargs
        rm = call_kwargs.kwargs["response_matrix"]
        assert rm is not None
        assert rm.num_rows == 1  # One row from initial evaluation

    def test_response_matrix_accumulates_rows(self):
        """ResponseMatrix should accumulate one row per evaluation."""
        dataset = _make_dataset()

        evaluator = MagicMock(spec=MemoryEvaluator)
        evaluator.evaluate.side_effect = [
            EvalResult(score=0.3, per_case_scores=[0.3], failed_cases=[
                FailedCase(question="q", output="o", expected="e", score=0.3)
            ]),
            EvalResult(score=0.5, per_case_scores=[0.5], failed_cases=[
                FailedCase(question="q", output="o2", expected="e", score=0.5)
            ]),
            EvalResult(score=0.8, per_case_scores=[0.8]),
        ]

        child1 = KBProgram(source_code="v1", generation=1)
        child2 = KBProgram(source_code="v2", generation=2)

        reflector = MagicMock(spec=Reflector)
        reflector.reflect_and_mutate.side_effect = [child1, child2]
        reflector.max_fix_attempts = 3

        loop = EvolutionLoop(
            evaluator=evaluator,
            reflector=reflector,
            dataset=dataset,
            max_iterations=2,
        )
        loop.run()

        # Second call should have 2 rows (initial + child1)
        second_call_kwargs = reflector.reflect_and_mutate.call_args_list[1]
        rm = second_call_kwargs.kwargs["response_matrix"]
        assert rm.num_rows == 2
```

Also add `ResponseMatrix` to the imports in test_loop.py if needed (it shouldn't be needed since we're checking via the mock).

**Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/evolution/test_loop.py::TestEvolutionLoopResponseMatrix -v`
Expected: FAIL (`response_matrix` not in call kwargs)

**Step 3: Modify loop.py**

In `src/programmaticmemory/evolution/loop.py`:

1. Add `ResponseMatrix` to the import from types (line 10-16):

```python
from programmaticmemory.evolution.types import (
    Dataset,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    ResponseMatrix,
)
```

2. After the initial evaluation (after line 81, `best_score = eval_result.score`), create and populate the response matrix:

```python
        # Create response matrix for IRT-guided case selection
        response_matrix = ResponseMatrix(num_items=len(ds.val))
        response_matrix.append(eval_result.per_case_scores)
```

3. Pass `response_matrix` to `reflect_and_mutate` (line 112):

```python
            child = self.reflector.reflect_and_mutate(
                current, eval_result, i, response_matrix=response_matrix
            )
```

4. After child evaluation (after line 146, `child_score = child_result.score`), append child's scores to the matrix:

```python
            response_matrix.append(child_result.per_case_scores)
```

**Step 4: Run the new tests**

Run: `uv run pytest tests/evolution/test_loop.py::TestEvolutionLoopResponseMatrix -v`
Expected: all PASS

**Step 5: Run ALL loop tests to check nothing broke**

Run: `uv run pytest tests/evolution/test_loop.py -v`
Expected: all PASS. Existing tests use `MagicMock(spec=Reflector)`, which accepts any kwargs.

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_loop.py
git commit -m "feat: create and pass ResponseMatrix through evolution loop"
```

---

### Task 5: Run full test suite and update snapshots

**Files:**
- Test: all `tests/evolution/test_*.py`
- Snapshots: `tests/evolution/__snapshots__/`

**Step 1: Run all non-LLM tests**

Run: `uv run pytest tests/evolution/ -m "not llm" -v`
Expected: all PASS

**Step 2: Update snapshots if needed**

Run: `uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v`
Expected: all PASS, any snapshot diffs reviewed

**Step 3: Run lint on all changed files**

Run: `uv run ruff check src/programmaticmemory/evolution/types.py src/programmaticmemory/evolution/selection.py src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/loop.py`
Expected: no errors

Run: `uv run ruff format src/programmaticmemory/evolution/types.py src/programmaticmemory/evolution/selection.py src/programmaticmemory/evolution/reflector.py src/programmaticmemory/evolution/loop.py`
Expected: no changes (or apply formatting)

**Step 4: Commit any snapshot updates**

```bash
git add tests/evolution/__snapshots__/
git commit -m "test: update snapshots for IRT case selection integration"
```

---

### Summary of all changes

| File | Action | What |
|---|---|---|
| `src/programmaticmemory/evolution/types.py` | Modify | Add `ResponseMatrix` dataclass |
| `src/programmaticmemory/evolution/selection.py` | Create | `select_cases()` + IRT parameter computation |
| `src/programmaticmemory/evolution/reflector.py` | Modify | Accept `response_matrix`, call `select_cases()` |
| `src/programmaticmemory/evolution/loop.py` | Modify | Create `ResponseMatrix`, append rows, pass to reflector |
| `tests/evolution/test_types.py` | Modify | Add `TestResponseMatrix` tests |
| `tests/evolution/test_selection.py` | Create | Tests for `select_cases()` |
| `tests/evolution/test_reflector.py` | Modify | Add test for response_matrix integration |
| `tests/evolution/test_loop.py` | Modify | Add tests for ResponseMatrix flow |
| `tests/evolution/__snapshots__/` | Update | Snapshot updates from reflector changes |
| `src/programmaticmemory/evolution/evaluator.py` | No change | |
| `src/programmaticmemory/evolution/prompts.py` | No change | |
