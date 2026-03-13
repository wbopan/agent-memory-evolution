# Experiment Readiness Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable running all Table 1 and Table 2 experiments without code changes.

**Architecture:** Six independent changes: two CLI flags (`--freeze-instructions`, `--max-fix-attempts`), one baseline file, one Dataset field (`extra_scorers`), one seed directory, and one LaTeX edit. Task 4 depends on Task 1 (both modify `types.py`).

**Tech Stack:** Python 3.12, pytest, ruff, regex for constant replacement.

**Spec:** `docs/superpowers/specs/2026-03-13-experiment-readiness-design.md`

---

### Task 1: Add `extra_scorers` to Dataset and LoCoMo

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py:87-97` (Dataset dataclass)
- Modify: `src/programmaticmemory/benchmarks/locomo.py:110` (Dataset construction)
- Test: `tests/evolution/test_test_split.py` (existing tests use Dataset — verify no breakage)

- [ ] **Step 1: Add `extra_scorers` field to Dataset**

In `src/programmaticmemory/evolution/types.py`, add to the `Dataset` dataclass after `available_categories`:

```python
extra_scorers: dict[str, Scorer] = field(default_factory=dict)
```

Note: `Scorer` is already defined in `types.py` as a Protocol.

- [ ] **Step 2: Add `extra_scorers` to LoCoMo dataset**

In `src/programmaticmemory/benchmarks/locomo.py`, change the return statement at line 110:

```python
# Before:
return Dataset(train=train, val=val, test=[], scorer=TokenF1Scorer(), available_categories=all_categories)

# After:
from programmaticmemory.evolution.evaluator import ExactMatchScorer
return Dataset(train=train, val=val, test=[], scorer=TokenF1Scorer(), available_categories=all_categories, extra_scorers={"em": ExactMatchScorer()})
```

- [ ] **Step 3: Run existing tests to verify no breakage**

Run: `.venv/bin/pytest tests/evolution/test_test_split.py tests/evolution/test_strategies.py -v -m "not llm"`
Expected: All tests PASS (Dataset construction with default empty dict is backward compatible)

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/types.py src/programmaticmemory/benchmarks/locomo.py
git commit -m "feat: add extra_scorers to Dataset, set EM for LoCoMo"
```

---

### Task 2: Expose `--max-fix-attempts` CLI flag

**Files:**
- Modify: `src/programmaticmemory/evolution/__main__.py` (after `--test-train-ratio` arg, and Reflector construction)

- [ ] **Step 1: Add CLI argument**

In `__main__.py`, after the `--test-train-ratio` argument block, add:

```python
parser.add_argument(
    "--max-fix-attempts",
    type=int,
    default=3,
    help="Max compile-fix attempts per reflection (default: 3). Set to 0 to disable fix loop.",
)
```

- [ ] **Step 2: Pass to Reflector constructor**

Change the Reflector construction (line 308):

```python
# Before:
reflector = Reflector(model=args.reflect_model, prompt_config=prompt_config)

# After:
reflector = Reflector(model=args.reflect_model, prompt_config=prompt_config, max_fix_attempts=args.max_fix_attempts)
```

- [ ] **Step 3: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/__main__.py`
Expected: All checks passed

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/evolution/__main__.py
git commit -m "feat: expose --max-fix-attempts CLI flag"
```

---

### Task 3: Add `freeze_instruction_constants()` to sandbox and wire into loop

**Files:**
- Modify: `src/programmaticmemory/evolution/sandbox.py` (add helper function + import `re` at top)
- Modify: `src/programmaticmemory/evolution/loop.py:54-77` (add `freeze_instructions` param to `__init__`)
- Modify: `src/programmaticmemory/evolution/loop.py:150-157` (apply freeze after reflect)
- Modify: `src/programmaticmemory/evolution/__main__.py` (add CLI flag, pass to EvolutionLoop)
- Test: `tests/evolution/test_sandbox.py` (new tests for freeze function)
- Test: `tests/evolution/test_loop.py` (new test for freeze in loop)

- [ ] **Step 1: Write test for `freeze_instruction_constants`**

Create or add to `tests/evolution/test_sandbox.py`:

```python
from programmaticmemory.evolution.sandbox import freeze_instruction_constants

class TestFreezeInstructionConstants:
    def test_replaces_all_four_constants(self):
        parent_source = '''
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = "Parent KI instruction"
INSTRUCTION_QUERY = "Parent query instruction"
INSTRUCTION_RESPONSE = "Parent response instruction"
ALWAYS_ON_KNOWLEDGE = "Parent always-on"

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
'''
        child_source = parent_source.replace("Parent KI instruction", "Child KI instruction")
        child_source = child_source.replace("Parent query instruction", "Child query instruction")
        child_source = child_source.replace("Parent response instruction", "Child response instruction")
        child_source = child_source.replace("Parent always-on", "Child always-on")

        frozen = freeze_instruction_constants(parent_source, child_source)

        assert 'INSTRUCTION_KNOWLEDGE_ITEM = "Parent KI instruction"' not in frozen or "Parent KI instruction" in frozen
        # More precisely: compile the frozen source and check constants match parent
        from programmaticmemory.evolution.sandbox import compile_kb_program
        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
        assert frozen_compiled.instruction_query == parent_compiled.instruction_query
        assert frozen_compiled.instruction_response == parent_compiled.instruction_response
        assert frozen_compiled.always_on_knowledge == parent_compiled.always_on_knowledge

    def test_preserves_non_constant_code(self):
        parent_source = '''
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = "Parent KI"
INSTRUCTION_QUERY = "Parent Q"
INSTRUCTION_RESPONSE = "Parent R"
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
'''
        child_source = parent_source.replace("Parent KI", "Child KI").replace(
            "Parent Q", "Child Q").replace("Parent R", "Child R").replace(
            'def read(self, query): return ""',
            'def read(self, query): return "child logic"',
        )

        frozen = freeze_instruction_constants(parent_source, child_source)

        # Non-constant code preserved from child
        assert 'return "child logic"' in frozen
        # Constants restored to parent values
        from programmaticmemory.evolution.sandbox import compile_kb_program
        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item

    def test_handles_triple_quoted_constants(self):
        parent_source = '''
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = """Parent
multiline KI"""
INSTRUCTION_QUERY = "Parent Q"
INSTRUCTION_RESPONSE = "Parent R"
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
'''
        child_source = parent_source.replace("Parent\nmultiline KI", "Child KI changed")

        frozen = freeze_instruction_constants(parent_source, child_source)

        from programmaticmemory.evolution.sandbox import compile_kb_program
        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/evolution/test_sandbox.py::TestFreezeInstructionConstants -v`
Expected: FAIL (freeze_instruction_constants not yet defined)

- [ ] **Step 3: Implement `freeze_instruction_constants` in sandbox.py**

First, add `import re` as a module-level import at the top of `sandbox.py` (after the existing stdlib imports like `import ast`, `import concurrent.futures`, etc.):

```python
import re
```

Then add the function at the end of `sandbox.py`:

```python
def freeze_instruction_constants(parent_source: str, child_source: str) -> str:
    """Replace instruction constants in child source with values from parent.

    Compiles parent to extract the four constant values, then regex-replaces
    the corresponding assignments in the child source.
    """
    parent_result = compile_kb_program(parent_source)
    if isinstance(parent_result, CompileError):
        raise ValueError(f"Parent source failed to compile: {parent_result.message}")

    constant_values = {
        "INSTRUCTION_KNOWLEDGE_ITEM": parent_result.instruction_knowledge_item,
        "INSTRUCTION_QUERY": parent_result.instruction_query,
        "INSTRUCTION_RESPONSE": parent_result.instruction_response,
        "ALWAYS_ON_KNOWLEDGE": parent_result.always_on_knowledge,
    }

    result = child_source
    for name, value in constant_values.items():
        # Match: CONSTANT_NAME = "..." or """...""" or (...) at start of line
        pattern = re.compile(
            rf'^({name}\s*=\s*)('
            r'"{3}[\s\S]*?"{3}'   # triple-double-quoted
            r"|'{3}[\s\S]*?'{3}"  # triple-single-quoted
            r'|\([\s\S]*?\)'      # parenthesized (multiline concat)
            r'|"(?:[^"\\]|\\.)*"' # double-quoted
            r"|'(?:[^'\\]|\\.)*'" # single-quoted
            r')',
            re.MULTILINE,
        )
        replacement = rf'\g<1>{repr(value)}'
        result = pattern.sub(replacement, result, count=1)

    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/evolution/test_sandbox.py::TestFreezeInstructionConstants -v`
Expected: PASS

- [ ] **Step 5: Add `freeze_instructions` to EvolutionLoop and wire in loop.py**

Add module-level imports at top of `loop.py` (these are new — `loop.py` currently doesn't import from sandbox directly, but `freeze_instruction_constants` is a new utility that logically belongs here):

```python
from programmaticmemory.evolution.sandbox import CompileError, compile_kb_program, freeze_instruction_constants, smoke_test
```

Modify `EvolutionLoop.__init__` — add `freeze_instructions: bool = False` parameter and `self.freeze_instructions = freeze_instructions`.

In the loop body, after `child = self.reflector.reflect_and_mutate(parent, parent_eval, i)` and after the existing `if child is None: ... continue` block, insert:

```python
# Freeze instruction constants if requested
if self.freeze_instructions:
    frozen_source = freeze_instruction_constants(parent.source_code, child.source_code)
    compile_result = compile_kb_program(frozen_source)
    if isinstance(compile_result, CompileError):
        self.logger.log(f"Frozen child failed compilation: {compile_result.message}", header="EVOLUTION")
        state.history.append(
            EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
        )
        state.total_iterations = i
        continue
    smoke = smoke_test(frozen_source)
    if not smoke.success:
        self.logger.log(f"Frozen child failed smoke test: {smoke.error}", header="EVOLUTION")
        state.history.append(
            EvolutionRecord(iteration=i, program=parent, score=parent_entry.score, parent_hash=parent.hash)
        )
        state.total_iterations = i
        continue
    child = KBProgram(
        source_code=frozen_source,
        generation=child.generation,
        parent_hash=child.parent_hash,
    )
```

This skip pattern matches the existing `child is None` skip (lines 151-157 of current loop.py).

- [ ] **Step 6: Add `--freeze-instructions` CLI flag in `__main__.py`**

Add after `--max-fix-attempts`:

```python
parser.add_argument(
    "--freeze-instructions",
    action="store_true",
    default=False,
    help="Freeze instruction constants during evolution (ablation: only memory design evolves)",
)
```

Pass to EvolutionLoop construction (find the `EvolutionLoop(...)` call and add `freeze_instructions=args.freeze_instructions`).

- [ ] **Step 7: Write loop test for freeze_instructions**

Add to `tests/evolution/test_loop.py`:

```python
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.sandbox import compile_kb_program

class TestFreezeInstructions:
    def test_freeze_restores_parent_constants(self):
        """When freeze_instructions=True, child's instruction constants match parent's."""
        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)

        # Create child source with changed instruction constant
        child_source = INITIAL_KB_PROGRAM.replace(
            'INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."',
            'INSTRUCTION_KNOWLEDGE_ITEM = "CHANGED BY REFLECTOR"',
        )
        mock_reflector = Mock()
        mock_reflector.reflect_and_mutate.return_value = KBProgram(
            source_code=child_source, generation=1, parent_hash=parent.hash
        )
        mock_reflector.max_fix_attempts = 3

        mock_evaluator = Mock()
        mock_evaluator.evaluate.return_value = EvalResult(score=0.5)

        dataset = Dataset(train=[], val=[], test=[], scorer=None)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=1,
            freeze_instructions=True,
        )
        state = loop.run()

        # The child in the pool should have parent's constants, not "CHANGED"
        children = [e for e in state.pool.entries if e.name != "seed_0"]
        assert len(children) == 1
        child_compiled = compile_kb_program(children[0].program.source_code)
        parent_compiled = compile_kb_program(INITIAL_KB_PROGRAM)
        assert child_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
        assert "CHANGED BY REFLECTOR" not in children[0].program.source_code
```

Note: `INITIAL_KB_PROGRAM` is imported from `programmaticmemory.evolution.prompts`. Check what imports already exist in `test_loop.py` and add any missing ones. `KBProgram`, `EvalResult`, `Dataset`, `EvolutionLoop` should already be imported.

- [ ] **Step 8: Run all modified tests**

Run: `.venv/bin/pytest tests/evolution/test_sandbox.py::TestFreezeInstructionConstants tests/evolution/test_loop.py::TestFreezeInstructions -v`
Expected: All PASS

- [ ] **Step 9: Run full test suite**

Run: `.venv/bin/pytest tests/evolution/ -v -m "not llm"`
Expected: All PASS

- [ ] **Step 10: Commit**

```bash
git add src/programmaticmemory/evolution/sandbox.py src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/__main__.py tests/evolution/test_sandbox.py tests/evolution/test_loop.py
git commit -m "feat: add --freeze-instructions flag for instruction constants ablation"
```

---

### Task 4: Extra scorers in loop summary output

**Depends on:** Task 1 (which adds `extra_scorers` to `Dataset` in `types.py`)

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py` (add fields to EvolutionState)
- Modify: `src/programmaticmemory/evolution/loop.py:227-282` (final eval + test eval + summary)
- Test: `tests/evolution/test_loop.py` (new test)

- [ ] **Step 1: Add extra metrics fields to EvolutionState**

In `src/programmaticmemory/evolution/types.py`, add to `EvolutionState`:

```python
final_extra_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
test_extra_metrics: dict[str, dict[str, float]] = field(default_factory=dict)
```

These map `program_hash → {scorer_name → score}`.

- [ ] **Step 2: Write test for extra_scorers in summary**

Add to `tests/evolution/test_loop.py`:

```python
from programmaticmemory.evolution.evaluator import ExactMatchScorer

class TestExtraScorers:
    def test_extra_scorers_in_test_eval(self):
        """Extra scorers compute additional metrics from per_case_outputs."""
        mock_evaluator = Mock()
        # Seed eval returns basic result
        seed_eval = EvalResult(score=0.5)
        # Test eval returns per_case_outputs
        test_eval = EvalResult(
            score=0.8,
            per_case_outputs=["alice", "wrong"],
            per_case_scores=[1.0, 0.6],
        )
        mock_evaluator.evaluate.side_effect = [seed_eval, test_eval]

        test_items = [
            DataItem(raw_text="", question="q1", expected_answer="alice"),
            DataItem(raw_text="", question="q2", expected_answer="bob"),
        ]
        dataset = Dataset(
            train=[], val=[], test=test_items,
            scorer=None,
            extra_scorers={"em": ExactMatchScorer()},
        )

        mock_strategy = Mock()
        mock_strategy.select.return_value = ([], [])
        mock_strategy.final_eval_data.return_value = None
        mock_strategy.test_eval_data.return_value = ([], test_items)
        mock_strategy.final_candidates.return_value = []

        mock_reflector = Mock()
        mock_reflector.max_fix_attempts = 3

        parent = KBProgram(source_code=INITIAL_KB_PROGRAM)
        loop = EvolutionLoop(
            evaluator=mock_evaluator,
            reflector=mock_reflector,
            dataset=dataset,
            initial_programs=[parent],
            max_iterations=0,
            eval_strategy=mock_strategy,
        )
        state = loop.run()

        # EM: "alice" contains "alice" → 1.0, "wrong" does not contain "bob" → 0.0
        # Average EM = 0.5
        assert len(state.test_extra_metrics) == 1
        program_hash = list(state.test_extra_metrics.keys())[0]
        assert state.test_extra_metrics[program_hash]["em"] == 0.5
```

Note: import `INITIAL_KB_PROGRAM` from `programmaticmemory.evolution.prompts` and `DataItem` from `programmaticmemory.evolution.types` if not already imported in the test file.

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/evolution/test_loop.py::TestExtraScorers -v`
Expected: FAIL (test_extra_metrics not on EvolutionState or not populated)

- [ ] **Step 4: Implement extra scorers in loop.py**

In the **test eval block** (after `state.test_scores[best_entry.program.hash] = test_result.score`), add:

```python
# Compute extra metrics
if self.dataset.extra_scorers and test_result.per_case_outputs:
    test_items = test_data[1]
    state.test_extra_metrics[best_entry.program.hash] = {}
    for name, scorer in self.dataset.extra_scorers.items():
        scores = [
            scorer(out, item.expected_answer)
            for out, item in zip(test_result.per_case_outputs, test_items)
        ]
        avg = sum(scores) / len(scores) if scores else 0.0
        state.test_extra_metrics[best_entry.program.hash][name] = avg
        self.logger.log(f"Test extra metric '{name}': {avg:.3f}", header="EVOLUTION")
```

In the **final eval block** (inside the `for entry in candidates:` loop, after `state.final_scores[entry.program.hash] = final_result.score`), add:

```python
if self.dataset.extra_scorers and final_result.per_case_outputs:
    final_items = final_data[1]
    state.final_extra_metrics[entry.program.hash] = {}
    for name, scorer in self.dataset.extra_scorers.items():
        scores = [
            scorer(out, item.expected_answer)
            for out, item in zip(final_result.per_case_outputs, final_items)
        ]
        avg = sum(scores) / len(scores) if scores else 0.0
        state.final_extra_metrics[entry.program.hash][name] = avg
        self.logger.log(f"Final extra metric '{name}' for {entry.program.hash}: {avg:.3f}", header="EVOLUTION")
```

Update the summary dict — change `test_evaluation` and `final_evaluation` to include extra_metrics:

```python
"final_evaluation": {
    "strategy": self.eval_strategy.__class__.__name__,
    "candidates": [{"hash": h, "final_score": s} for h, s in state.final_scores.items()],
    "extra_metrics": dict(state.final_extra_metrics),
}
if state.final_scores
else None,
"test_evaluation": {
    "scores": dict(state.test_scores.items()),
    "extra_metrics": dict(state.test_extra_metrics),
}
if state.test_scores
else None,
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/evolution/test_loop.py -v -m "not llm"`
Expected: All PASS

- [ ] **Step 6: Commit**

```bash
git add src/programmaticmemory/evolution/loop.py src/programmaticmemory/evolution/types.py tests/evolution/test_loop.py
git commit -m "feat: compute extra_scorers metrics in final/test eval summary"
```

---

### Task 5: No-memory baseline and single-seed directory

**Files:**
- Create: `src/programmaticmemory/baselines/no_memory.py`
- Create: `seeds/single/llm_summarizer.py` (copy of `seeds/llm_summarizer.py`)

- [ ] **Step 1: Create no-memory baseline**

Create `src/programmaticmemory/baselines/no_memory.py`:

```python
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Formulate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer without explanation."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Minimal knowledge item — discarded by no-memory KB."""

    text: str = field(metadata={"description": "Any text"})


@dataclass
class Query:
    """Minimal query — returns nothing from no-memory KB."""

    text: str = field(metadata={"description": "Any text"})


class KnowledgeBase:
    """No-memory baseline: stores nothing, retrieves nothing."""

    def __init__(self, toolkit):
        pass

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        pass

    def read(self, query: Query) -> str:
        return ""
```

- [ ] **Step 2: Verify baseline compiles and passes smoke test**

Run: `.venv/bin/python -c "from programmaticmemory.evolution.sandbox import compile_kb_program, smoke_test; src = open('src/programmaticmemory/baselines/no_memory.py').read(); r = compile_kb_program(src); print(type(r).__name__); s = smoke_test(src); print(f'smoke: {s.success}')"`
Expected: `CompiledProgram` and `smoke: True`

- [ ] **Step 3: Create single-seed directory**

```bash
mkdir -p seeds/single
cp seeds/llm_summarizer.py seeds/single/llm_summarizer.py
```

- [ ] **Step 4: Commit**

```bash
git add src/programmaticmemory/baselines/no_memory.py seeds/single/llm_summarizer.py
git commit -m "feat: add no-memory baseline and single-seed directory for ablations"
```

---

### Task 6: Update Table 1 LaTeX

**Files:**
- Modify: `/Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos/paper/main.tex` (Table 1, lines ~143-166)

**Note:** This file is in a separate git repo (`Repos/paper/`), not in the `programmaticmemory` repo. Commit must be done from within that repo.

- [ ] **Step 1: Update Table 1 to remove Progress column and add EM**

Replace the Table 1 block with:

```latex
\begin{table}[thbp]
    \centering
    \small
    \caption{\textbf{Main results.} Performance of \textsc{Engram} versus baselines across benchmarks. Best results per column in \textbf{bold}. \scaffold{Fill with real numbers after experiments.}}
    \vspace{-0.5em}
    \label{tab:main-results}
    \setlength{\tabcolsep}{4pt}
    \begin{tabular}{lcccc}
        \toprule
        \rowcolor{gray!10}
         & \multicolumn{2}{c}{\textbf{LoCoMo}} & \textbf{ALFWorld} & \\
        \cmidrule(lr){2-3} \cmidrule(lr){4-4}
        \rowcolor{gray!10}
        \textbf{Method} & \textbf{EM} & \textbf{F1} & \textbf{Success} & \cellcolor{green!10}\textbf{Avg.} \\
        \midrule
        No Memory          & 0.00 & 0.00 & 0.00 & \cellcolor{green!10}0.00 \\
        Vanilla RAG        & 0.00 & 0.00 & 0.00 & \cellcolor{green!10}0.00 \\
        \midrule
        \rowcolor{green!10}
        \textbf{Ours}      & \textbf{0.00} & \textbf{0.00} & \textbf{0.00} & \textbf{0.00} \\
        \bottomrule
    \end{tabular}%
    \vspace{-1.5em}
\end{table}
```

- [ ] **Step 2: Commit from paper repo**

```bash
cd /Users/panwenbo/Documents/Projects/ProgrammaticMemory/Repos/paper
git add main.tex
git commit -m "docs: update Table 1 layout — remove Progress, add EM column"
```
