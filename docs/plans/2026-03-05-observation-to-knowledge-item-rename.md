# Observation → KnowledgeItem Rename Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rename `Observation` to `KnowledgeItem` across all prompts, code, and documentation to better match the knowledge base concept and guide the task LM toward more flexible information creation.

**Architecture:** Systematic rename touching 4 source files, 6 test files, 4 snapshot files, and 2 docs. The rename cascades from types → sandbox → prompts → evaluator → tests → snapshots → docs.

**Tech Stack:** Python, pytest, syrupy snapshots

---

## Name Mapping

| Old | New | Context |
|-----|-----|---------|
| `class Observation` | `class KnowledgeItem` | In KB programs (LLM-generated code) |
| `INSTRUCTION_OBSERVATION` | `INSTRUCTION_KNOWLEDGE_ITEM` | Module-level constant in KB programs |
| `obs_cls` | `ki_cls` | Variable/field for the compiled class |
| `obs_schema` | `ki_schema` | Variable for the dataclass JSON schema |
| `instruction_observation` | `instruction_knowledge_item` | Parameter name throughout |
| `build_observation_generation_prompt` | `build_knowledge_item_generation_prompt` | Function in prompts.py |
| `build_observation_with_feedback_prompt` | `build_knowledge_item_with_feedback_prompt` | Function in prompts.py |
| `obs = obs_cls(...)` | `ki = ki_cls(...)` | Local variable in evaluator |
| `CompiledProgram.obs_cls` | `CompiledProgram.ki_cls` | Dataclass field |
| `CompiledProgram.instruction_observation` | `CompiledProgram.instruction_knowledge_item` | Dataclass field |

Prompt text changes (in KB_INTERFACE_SPEC, INITIAL_KB_PROGRAM, reflection prompt, compile-fix prompt):
- "Observation" → "KnowledgeItem" in class/concept references
- "observation" → "knowledge item" in natural language descriptions
- "Observations" → "KnowledgeItems" / "knowledge items" in natural language

---

### Task 1: Rename in types.py

**Files:**
- Modify: `src/programmaticmemory/evolution/types.py`

**Step 1: Update references**

Changes needed:
- `KBProgram` docstring: "Observation" → "KnowledgeItem"
- `DataItem` docstring: "observations" → "knowledge items"
- `TrainExample` docstring: "Observation" → "KnowledgeItem"

**Step 2: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/types.py`

---

### Task 2: Rename in sandbox.py

**Files:**
- Modify: `src/programmaticmemory/evolution/sandbox.py`

**Step 1: Update CompiledProgram**

- `obs_cls: type` → `ki_cls: type`
- `instruction_observation: str` → `instruction_knowledge_item: str`

**Step 2: Update compile_kb_program**

- Required classes set: `"Observation"` → `"KnowledgeItem"`
- `REQUIRED_CONSTANTS`: `"INSTRUCTION_OBSERVATION"` → `"INSTRUCTION_KNOWLEDGE_ITEM"`
- Namespace extraction: `namespace["Observation"]` → `namespace["KnowledgeItem"]`
- Namespace extraction: `namespace["INSTRUCTION_OBSERVATION"]` → `namespace["INSTRUCTION_KNOWLEDGE_ITEM"]`
- Return: `obs_cls=...` → `ki_cls=...`, `instruction_observation=...` → `instruction_knowledge_item=...`

**Step 3: Update smoke_test**

- `obs_cls` variable → `ki_cls`
- `obs_fields` → `ki_fields`
- `obs = obs_cls(...)` → `ki = ki_cls(...)`

**Step 4: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/sandbox.py`

---

### Task 3: Rename in prompts.py

**Files:**
- Modify: `src/programmaticmemory/evolution/prompts.py`

**Step 1: Update KB_INTERFACE_SPEC**

- All references to "Observation" class/concept → "KnowledgeItem"
- `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM`
- Natural language: "observation" → "knowledge item"

**Step 2: Update INITIAL_KB_PROGRAM**

- `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM`
- `class Observation:` → `class KnowledgeItem:`
- Docstring, parameter types, variable names referencing Observation

**Step 3: Rename functions**

- `build_observation_generation_prompt` → `build_knowledge_item_generation_prompt`
- `build_observation_with_feedback_prompt` → `build_knowledge_item_with_feedback_prompt`
- Update parameter default strings and docstrings

**Step 4: Update build_reflection_user_prompt**

- Text references: "Observation" → "KnowledgeItem", "Observations" → "KnowledgeItems"
- `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM` in rules/instructions

**Step 5: Update build_compile_fix_prompt**

- Text references: "Observation" → "KnowledgeItem"
- `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM`

**Step 6: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/prompts.py`

---

### Task 4: Rename in evaluator.py

**Files:**
- Modify: `src/programmaticmemory/evolution/evaluator.py`

**Step 1: Update imports**

- `build_observation_generation_prompt` → `build_knowledge_item_generation_prompt`
- `build_observation_with_feedback_prompt` → `build_knowledge_item_with_feedback_prompt`

**Step 2: Update all methods**

- `obs_cls` → `ki_cls` (parameter names and local variables)
- `obs_schema` → `ki_schema`
- `instruction_observation` → `instruction_knowledge_item` (all parameter declarations and calls)
- `obs = obs_cls(...)` → `ki = ki_cls(...)`
- Log messages: "observation" → "knowledge item"
- `compiled.obs_cls` → `compiled.ki_cls`
- `compiled.instruction_observation` → `compiled.instruction_knowledge_item`

**Step 3: Run lint**

Run: `uv run ruff check src/programmaticmemory/evolution/evaluator.py`

---

### Task 5: Rename in test files

**Files:**
- Modify: `tests/evolution/test_sandbox.py`
- Modify: `tests/evolution/test_evaluator.py`
- Modify: `tests/evolution/test_prompts.py`
- Modify: `tests/evolution/test_reflector.py`
- Modify: `tests/evolution/test_patcher.py`
- Modify: `tests/evolution/test_llm_integration.py`

**Step 1: Update all test files**

Apply the same name mapping to all test files:
- Inline KB program strings: `class Observation` → `class KnowledgeItem`, `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM`
- Variable references: `obs_cls` → `ki_cls`, `instruction_observation` → `instruction_knowledge_item`
- Function references: `build_observation_*` → `build_knowledge_item_*`
- String assertions referencing "Observation" → "KnowledgeItem"

**Step 2: Run lint**

Run: `uv run ruff check tests/evolution/`

---

### Task 6: Regenerate snapshots

**Step 1: Update unit test snapshots (no API key needed)**

Run: `uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v`

**Step 2: Update LLM integration snapshots (needs API key or cache hit)**

Run: `uv run pytest tests/evolution/test_llm_integration.py --snapshot-update -v`
Note: LLM cache keys include message content, so all cached responses will miss. This step may require an API key.

---

### Task 7: Update CLAUDE.md and memory

**Files:**
- Modify: `CLAUDE.md` (project root)
- Modify: `/home/grads/bopan5/.claude/projects/-home-grads-bopan5-Repos-programmaticmemory/memory/MEMORY.md`

**Step 1: Update CLAUDE.md**

- All references to "Observation" class → "KnowledgeItem"
- `INSTRUCTION_OBSERVATION` → `INSTRUCTION_KNOWLEDGE_ITEM`
- `obs_cls` → `ki_cls`
- `instruction_observation` → `instruction_knowledge_item`
- Natural language: "observation" → "knowledge item" where appropriate

**Step 2: Update MEMORY.md**

- Update cached knowledge about naming conventions
