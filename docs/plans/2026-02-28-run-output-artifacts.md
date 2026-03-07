# Run Output Artifacts Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Persist three kinds of already-computed data to disk so they can be reviewed after a run: per-iteration program source, per-iteration failed cases, and score history in summary.

**Architecture:** All data already exists in memory at the right points in `loop.py`. We add three small write methods to `RunOutputManager`, call them from `loop.py`, and extend the `summary` dict with two extra fields. No changes to `evaluator.py`, `reflector.py`, or `types.py`.

**Tech Stack:** Python stdlib only (json, pathlib). Tests use pytest + tmp_path fixture, following the pattern in `tests/evolution/test_run_output.py`.

---

### Task 1: `write_program()` — save per-iteration source code

**Files:**
- Modify: `src/programmaticmemory/logging/run_output.py`
- Test: `tests/evolution/test_run_output.py`

**Step 1: Write the failing test**

Add to `TestRunOutputManager` in `tests/evolution/test_run_output.py`:

```python
def test_write_program_creates_py_file(self, tmp_path):
    """write_program should save source code to programs/iter_N.py."""
    manager = RunOutputManager(tmp_path, config={})
    try:
        manager.write_program(iteration=1, source_code="class Memory: pass", accepted=True, score=0.75)

        prog_path = manager.run_dir / "programs" / "iter_1.py"
        assert prog_path.exists()
        content = prog_path.read_text(encoding="utf-8")
        assert "class Memory: pass" in content
        assert "accepted" in content
        assert "0.75" in content
    finally:
        manager.close()

def test_write_program_iter_0(self, tmp_path):
    """write_program at iteration 0 should be labelled 'initial'."""
    manager = RunOutputManager(tmp_path, config={})
    try:
        manager.write_program(iteration=0, source_code="# initial", accepted=True, score=0.5)
        prog_path = manager.run_dir / "programs" / "iter_0.py"
        assert prog_path.exists()
        assert "initial" in prog_path.read_text(encoding="utf-8")
    finally:
        manager.close()
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/evolution/test_run_output.py::TestRunOutputManager::test_write_program_creates_py_file -v
```

Expected: `FAILED` — `AttributeError: 'RunOutputManager' object has no attribute 'write_program'`

**Step 3: Implement `write_program()` in `run_output.py`**

Add after `write_summary()`:

```python
def write_program(self, iteration: int, source_code: str, accepted: bool, score: float) -> None:
    """Save a Memory Program's source code to programs/iter_N.py.

    Args:
        iteration: Evolution iteration number (0 = initial program).
        source_code: Full Python source of the Memory Program.
        accepted: Whether this program was accepted as the new best.
        score: Evaluation score for this program.
    """
    try:
        programs_dir = self.run_dir / "programs"
        programs_dir.mkdir(exist_ok=True)
        label = "initial" if iteration == 0 else ("accepted" if accepted else "rejected")
        header = f"# iter={iteration}  score={score:.4f}  {label}\n\n"
        (programs_dir / f"iter_{iteration}.py").write_text(header + source_code, encoding="utf-8")
    except Exception:
        pass  # logging must never crash the evolution loop
```

**Step 4: Run to verify passing**

```bash
uv run pytest tests/evolution/test_run_output.py::TestRunOutputManager::test_write_program_creates_py_file tests/evolution/test_run_output.py::TestRunOutputManager::test_write_program_iter_0 -v
```

Expected: `PASSED`

**Step 5: Wire into `loop.py`**

In `loop.py`, add two calls — one for the initial program (after initial eval, around line 65) and one for each child (after scoring, around line 107).

After initial eval result:
```python
if self.output_manager:
    self.output_manager.write_program(0, current.source_code, accepted=True, score=best_score)
```

After child scoring (inside the `for` loop, after `accepted = child_score > best_score`):
```python
if self.output_manager:
    self.output_manager.write_program(i, child.source_code, accepted=accepted, score=child_score)
```

**Step 6: Run all run_output tests**

```bash
uv run pytest tests/evolution/test_run_output.py -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add src/programmaticmemory/logging/run_output.py src/programmaticmemory/evolution/loop.py tests/evolution/test_run_output.py
git commit -m "feat: save per-iteration Memory Program source to programs/iter_N.py"
```

---

### Task 2: `write_failed_cases()` — save failed cases per iteration

**Files:**
- Modify: `src/programmaticmemory/logging/run_output.py`
- Test: `tests/evolution/test_run_output.py`

**Step 1: Write the failing test**

Add to `TestRunOutputManager`:

```python
def test_write_failed_cases_creates_json(self, tmp_path):
    """write_failed_cases should write failed_cases.json under llm_calls/iter_N/."""
    manager = RunOutputManager(tmp_path, config={})
    try:
        manager.set_phase(2, "train")  # ensure iter dir exists
        cases = [
            {"question": "Q1", "output": "A", "expected": "B", "score": 0.0, "memory_logs": ["log1"]},
        ]
        manager.write_failed_cases(iteration=2, cases=cases)

        out_path = manager.run_dir / "llm_calls" / "iter_2" / "failed_cases.json"
        assert out_path.exists()
        loaded = json.loads(out_path.read_text(encoding="utf-8"))
        assert loaded[0]["question"] == "Q1"
        assert loaded[0]["memory_logs"] == ["log1"]
    finally:
        manager.close()
```

**Step 2: Run to verify failure**

```bash
uv run pytest tests/evolution/test_run_output.py::TestRunOutputManager::test_write_failed_cases_creates_json -v
```

Expected: `FAILED` — `AttributeError`

**Step 3: Implement `write_failed_cases()` in `run_output.py`**

Add after `write_program()`:

```python
def write_failed_cases(self, iteration: int, cases: list[dict]) -> None:
    """Save failed evaluation cases to llm_calls/iter_N/failed_cases.json.

    Args:
        iteration: Evolution iteration number.
        cases: List of dicts, each with question/output/expected/score/memory_logs.
    """
    try:
        out_path = self._callback._iter_dir(self.run_dir, iteration) / "failed_cases.json"
        out_path.write_text(json.dumps(cases, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass  # logging must never crash the evolution loop
```

**Step 4: Run to verify passing**

```bash
uv run pytest tests/evolution/test_run_output.py::TestRunOutputManager::test_write_failed_cases_creates_json -v
```

Expected: `PASSED`

**Step 5: Wire into `loop.py`**

After each `eval_result` is returned (both initial and child eval), serialize `failed_cases` and write. Add a helper import at top if needed — `dataclasses` is already available.

After initial eval (around line 65, after the `evaluate()` call):
```python
if self.output_manager and eval_result.failed_cases:
    self.output_manager.write_failed_cases(
        0,
        [{"question": fc.question, "output": fc.output, "expected": fc.expected,
          "score": fc.score, "memory_logs": fc.memory_logs} for fc in eval_result.failed_cases],
    )
```

After child eval (around line 103, after `child_result = self.evaluator.evaluate(...)`):
```python
if self.output_manager and child_result.failed_cases:
    self.output_manager.write_failed_cases(
        i,
        [{"question": fc.question, "output": fc.output, "expected": fc.expected,
          "score": fc.score, "memory_logs": fc.memory_logs} for fc in child_result.failed_cases],
    )
```

**Step 6: Run all run_output and loop tests**

```bash
uv run pytest tests/evolution/test_run_output.py tests/evolution/test_loop.py -v
```

Expected: all pass.

**Step 7: Commit**

```bash
git add src/programmaticmemory/logging/run_output.py src/programmaticmemory/evolution/loop.py tests/evolution/test_run_output.py
git commit -m "feat: save per-iteration failed cases to llm_calls/iter_N/failed_cases.json"
```

---

### Task 3: Extend `summary.json` with score history and best program source

This is the "one-liner" task — no new methods needed, just extend the `summary` dict in `loop.py`.

**Files:**
- Modify: `src/programmaticmemory/evolution/loop.py:141-147`
- Test: `tests/evolution/test_loop.py` (or `test_run_output.py`)

**Step 1: Find the existing summary dict in `loop.py` (lines 141-147)**

```python
summary = {
    "best_score": state.best_score,
    "total_iterations": state.total_iterations,
    "best_program_hash": state.best_program.hash,
    "best_program_generation": state.best_program.generation,
}
```

**Step 2: Write a failing test**

Look for an existing loop integration test that checks `write_summary`. If none exists, add to `test_run_output.py`:

```python
def test_write_summary_with_history(self, tmp_path):
    """write_summary should persist arbitrary extra fields like score_history."""
    manager = RunOutputManager(tmp_path, config={})
    try:
        metrics = {
            "best_score": 0.9,
            "score_history": [{"iteration": 0, "score": 0.5, "accepted": True}],
            "best_program_source": "class Memory: pass",
        }
        manager.write_summary(metrics)
        loaded = json.loads((manager.run_dir / "summary.json").read_text())
        assert loaded["score_history"][0]["iteration"] == 0
        assert "class Memory" in loaded["best_program_source"]
    finally:
        manager.close()
```

**Step 3: Run to verify (this should already pass — write_summary is generic)**

```bash
uv run pytest tests/evolution/test_run_output.py::TestRunOutputManager::test_write_summary_with_history -v
```

Expected: `PASSED` (write_summary already accepts any dict).

**Step 4: Extend the `summary` dict in `loop.py`**

```python
summary = {
    "best_score": state.best_score,
    "total_iterations": state.total_iterations,
    "best_program_hash": state.best_program.hash,
    "best_program_generation": state.best_program.generation,
    "score_history": [{"iteration": r.iteration, "score": r.score, "accepted": r.accepted} for r in state.history],
    "best_program_source": state.best_program.source_code,
}
```

**Step 5: Run full test suite**

```bash
uv run pytest tests/evolution/ -m "not llm" -v
```

Expected: all pass.

**Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py tests/evolution/test_run_output.py
git commit -m "feat: add score_history and best_program_source to summary.json"
```
