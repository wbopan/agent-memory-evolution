# Compile-Fix Loop Design

## Problem

When the Reflector generates a new Memory Program that fails to compile or crashes during smoke test, the current evolution loop simply skips the iteration (`continue`). The error information is logged but never fed back to the LLM. This wastes iterations and the LLM may repeat the same mistakes.

## Solution: Reflector-Internal Fix Loop (Option B)

Add a compile-fix loop inside `Reflector.reflect_and_mutate`. After generating code, validate it with `compile_memory_program` + `smoke_test`. On failure, call the LLM again with a dedicated, concise fix prompt (code + error only, no reflection context). Retry up to 3 times.

## Flow

```
reflect_and_mutate(current, eval_result, iteration):
    1. Normal reflection LLM call -> extract code
    2. compile_memory_program(code)
       - If CompileError -> go to fix loop
    3. smoke_test(code, toolkit_config)
       - If failure -> go to fix loop
    4. Return MemoryProgram (guaranteed compiled + smoke-tested)

    Fix loop (max 3 attempts):
       - Build fix prompt: code + error_type + error_details + MEMORY_INTERFACE_SPEC
       - LLM call (independent, no reflection context)
       - Extract code -> back to step 2
       - If 3 attempts exhausted -> return None
```

## Changes

### prompts.py
- Add `COMPILE_FIX_SYSTEM_PROMPT`: concise system prompt for fixing compile/runtime errors
- Add `build_compile_fix_prompt(code, error_type, error_details)`: user prompt with code + error

### reflector.py
- `__init__` adds `max_fix_attempts: int = 3` and `toolkit_config: ToolkitConfig | None = None`
- `reflect_and_mutate` internally calls `compile_memory_program` + `smoke_test`
- On failure, enters fix loop with dedicated fix prompt
- Return value semantic: returned `MemoryProgram` is guaranteed to pass compile + smoke_test

### loop.py
- Remove the `smoke_test` call and its failure handling (lines 97-103)
- Pass `toolkit_config` to `Reflector.__init__`
- The `child is None` branch now also covers "3 fix attempts exhausted"

### Reflector.__init__ signature
```python
class Reflector:
    def __init__(
        self,
        model: str = "openai/gpt-4o",
        temperature: float = 0.7,
        max_fix_attempts: int = 3,
        toolkit_config: ToolkitConfig | None = None,
    ): ...
```

## Fix Prompt Design

The fix prompt is intentionally minimal to save tokens:
- System: MEMORY_INTERFACE_SPEC + "fix the error, output complete corrected code"
- User: the broken code + error type + error details

No reflection context (failed cases, eval score, etc.) is included — this is purely a "make it compile and run" step.

## Scope

Covers two failure types:
1. **CompileError**: syntax error, missing classes, import whitelist violation, exec error
2. **SmokeTestResult failure**: runtime error during basic write/read cycle, timeout
