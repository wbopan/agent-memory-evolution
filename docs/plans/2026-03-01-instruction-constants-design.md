# Instruction Constants in Memory Programs

## Problem

The task agent performs three operations — observe, query, answer — with hardcoded prompts in `prompts.py`. The evolved Memory Program controls schemas and storage logic, but cannot control *how the task agent interprets* those schemas. Consequences:

- Models generate overly verbose answers (hurting ExactMatch/TokenF1 scores)
- Observation extraction misses domain-specific cues ("focus on dates and names")
- Query construction uses paraphrases instead of exact entity names

The `general_tips` pattern in `INITIAL_MEMORY_PROGRAM` shows the intent — but it's buried inside `read()` return values, not injected into the LLM prompts that generate observations, queries, and answers.

## Solution

Add three **required module-level string constants** to every Memory Program:

```python
INSTRUCTION_OBSERVATION = "..."  # Appended to observation generation prompt
INSTRUCTION_QUERY = "..."        # Appended to query generation prompt
INSTRUCTION_RESPONSE = "..."     # Appended to answer generation prompt
```

These are appended to the respective task agent prompts, giving the evolved program control over all three LLM operations.

## Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Location | Module-level constants | Clean, easy to extract via `namespace['INSTRUCTION_*']`. Doesn't pollute class definitions. |
| Injection | Appended to prompt | Placed after the main prompt content. LLMs weight later content more heavily. |
| Validation | Required (compile error) | Ensures reflector always generates them. No silent fallback to empty instructions. |

## Module Changes

### 1. `sandbox.py` — Validate and return instruction constants

After the existing class checks, verify all three constants exist as strings in the exec namespace:

```python
REQUIRED_CONSTANTS = {"INSTRUCTION_OBSERVATION", "INSTRUCTION_QUERY", "INSTRUCTION_RESPONSE"}

# Inside compile_memory_program, after class check:
missing_consts = REQUIRED_CONSTANTS - set(namespace.keys())
if missing_consts:
    return CompileError(
        message=f"Missing required constant(s): {', '.join(sorted(missing_consts))}",
        details="...",
    )
```

Change the success return type from `tuple[type, type, type]` to include the three instruction strings. Options:

- Return `tuple[type, type, type, str, str, str]` (simple but positional)
- Return a `CompiledProgram` dataclass with named fields (cleaner)

**Recommended**: `CompiledProgram` dataclass:

```python
@dataclass
class CompiledProgram:
    obs_cls: type
    query_cls: type
    memory_cls: type
    instruction_observation: str
    instruction_query: str
    instruction_response: str
```

`compile_memory_program()` returns `CompiledProgram | CompileError`.

### 2. `prompts.py` — Accept instruction parameter

Add `instruction: str = ""` to the four task agent prompt builders. Append if non-empty:

- `build_observation_generation_prompt(raw_text, schema, instruction="")`
- `build_query_generation_prompt(question, schema, instruction="")`
- `build_retrieved_memory_prompt(retrieved, instruction="")`
- `build_observation_with_feedback_prompt(evaluation_result, ground_truth, schema, instruction="")`

Pattern:

```python
def build_observation_generation_prompt(raw_text: str, schema: str, instruction: str = "") -> str:
    prompt = f"""\
Given the following text, create an Observation object...
...
Output ONLY a valid JSON object matching the schema fields. No explanation."""
    if instruction:
        prompt += f"\n\n{instruction}"
    return prompt
```

### 3. `prompts.py` — Update `INITIAL_MEMORY_PROGRAM`

Add the three constants to the seed program. Move `general_tips` behavior into `INSTRUCTION_RESPONSE`:

```python
INITIAL_MEMORY_PROGRAM = '''\
from dataclasses import dataclass, field

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = "When answering questions, give a short and direct answer and nothing else."

@dataclass
class Observation:
    ...

class Memory:
    # Remove general_tips class variable — now in INSTRUCTION_RESPONSE
    ...
'''
```

### 4. `prompts.py` — Update `MEMORY_INTERFACE_SPEC`

Add a section documenting the three constants:

```
## Instruction Constants (required)

Three module-level string constants control how the task agent LLM behaves:

- INSTRUCTION_OBSERVATION: Appended to the observation generation prompt.
  Use to guide what information to extract and how to structure it.
- INSTRUCTION_QUERY: Appended to the query generation prompt.
  Use to guide how to formulate retrieval queries.
- INSTRUCTION_RESPONSE: Appended to the answer generation prompt.
  Use to control answer format, length, and style.

Set to empty string "" if no special instruction is needed.
```

### 5. `evaluator.py` — Thread instructions through evaluation

After `compile_memory_program()` succeeds, extract instructions from `CompiledProgram` and pass to all `build_*_prompt()` calls:

```python
compiled = compile_memory_program(program.source_code)
if isinstance(compiled, CompileError):
    return EvalResult(score=0.0, ...)

# Use named fields
obs_schema = extract_dataclass_schema(compiled.obs_cls)
query_schema = extract_dataclass_schema(compiled.query_cls)
memory = compiled.memory_cls(toolkit)

# Pass instructions to prompt builders
build_observation_generation_prompt(item.raw_text, obs_schema, compiled.instruction_observation)
build_query_generation_prompt(item.question, query_schema, compiled.instruction_query)
build_retrieved_memory_prompt(retrieved, compiled.instruction_response)
```

### 6. `prompts.py` — Update reflection prompt

Update `build_reflection_user_prompt` and `build_compile_fix_prompt` to mention the constants in the rules section, so the reflector knows to generate them. The `<rules>` section should include:

```
- The code must define three module-level string constants:
  INSTRUCTION_OBSERVATION, INSTRUCTION_QUERY, INSTRUCTION_RESPONSE.
```

### 7. Tests

- **`test_sandbox.py` / `test_toolkit.py`**: Test that `compile_memory_program` returns `CompileError` when constants are missing, and `CompiledProgram` with correct values when present.
- **`test_prompts.py`**: Snapshot tests for prompt builders with non-empty instruction parameter.
- **`test_evaluator.py`**: Update all mock programs to include the three constants. Verify instructions are threaded through to prompt builders.
- **`test_reflector.py`**: Update snapshots for reflection prompt changes.
- **`test_llm_integration.py`**: May need snapshot updates if reflection prompts change.

## Data Flow

```
MemoryProgram.source_code
    │
    ▼
compile_memory_program()
    │
    ▼
CompiledProgram {obs_cls, query_cls, memory_cls, instruction_*}
    │
    ├── instruction_observation → build_observation_generation_prompt(..., instruction)
    ├── instruction_query      → build_query_generation_prompt(..., instruction)
    └── instruction_response   → build_retrieved_memory_prompt(..., instruction)
                                  │
                                  ▼
                              Task agent LLM prompt (appended)
```

## Migration

- All existing saved/cached programs lack the constants → will fail `compile_memory_program` validation
- This is acceptable: programs are ephemeral (regenerated each evolution run)
- `INITIAL_MEMORY_PROGRAM` is the only persistent seed, and it gets updated
- LLM cache entries that return old-format programs will trigger compile-fix loop, which will add the constants
