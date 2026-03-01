# Runtime Guards During Evaluation

## Problem

During full evaluation, Memory Programs can exhibit runtime issues that waste time and tokens:

1. **Slow operations**: `memory.write()` or `memory.read()` calls that take too long (e.g., expensive SQLite queries, complex ChromaDB operations)
2. **Oversized output**: `memory.read()` returning extremely long strings that waste LLM context

Currently, the only validation is static: `compile_memory_program()` (AST checks) + `smoke_test()` (basic write/read with synthetic data). Once a program passes these, it runs full evaluation with zero runtime guards.

## Design

### Approach: Exception-based early abort

Detection via guarded helper functions in `evaluator.py`. On first violation, exception propagates to `evaluate()` which returns a special `EvalResult`. Fix loop in `loop.py` reuses `reflector._try_fix()`.

### Constants

- `MEMORY_OP_TIMEOUT = 5.0` — seconds per write/read call
- `MEMORY_READ_MAX_CHARS = 1000` — max characters from `read()` return value

### Components

#### 1. EvalResult field (`types.py`)

Add `runtime_violation: str | None = None` to `EvalResult`. When set, indicates the eval was aborted due to a runtime constraint violation.

#### 2. RuntimeViolationError + guarded helpers (`evaluator.py`)

New exception `RuntimeViolationError(Exception)`.

Two helpers using `concurrent.futures.ThreadPoolExecutor` (same timeout pattern as `sandbox.smoke_test`):

- `_guarded_write(memory, obs, timeout)` — wraps `memory.write(obs)` with timeout
- `_guarded_read(memory, query, timeout, max_chars)` — wraps `memory.read(query)` with timeout + output length check

#### 3. Replace all bare memory calls (`evaluator.py`)

6 call sites replaced:

| Method | Operation | Line |
|---|---|---|
| `_evaluate_offline` (batch path) | `memory.write(obs)` | 218 |
| `_evaluate_offline` (sequential path) | `memory.write(obs)` | 228 |
| `_online_train_sequential` | `memory.read(query)` | 301 |
| `_online_train_sequential` | `memory.write(obs)` | 350 |
| `_parse_queries_and_read` | `memory.read(query)` | 453 |
| `_evaluate_val_sequential` | `memory.read(query)` | 564 |

#### 4. Early abort catch in `evaluate()` (`evaluator.py`)

Single `try/except RuntimeViolationError` around `_evaluate_offline`/`_evaluate_online`. Returns `EvalResult(score=0.0, runtime_violation=str(e))`.

#### 5. Fix loop in `loop.py`

After `evaluator.evaluate()` returns, if `runtime_violation` is set:

1. Call `reflector._try_fix(code, "Runtime violation", violation_message)`
2. Validate fixed code with `reflector._validate_code()` (compile + smoke_test)
3. Re-evaluate
4. Repeat up to `max_fix_attempts` times
5. If exhausted, treat as failed iteration (score=0, not accepted)

### What does NOT change

- `smoke_test` — unchanged (synthetic data, compile-level check)
- `reflector.reflect_and_mutate` — unchanged
- `reflector._try_fix` — reused as-is (already takes generic error_type + error_details)
- `build_compile_fix_prompt` — reused as-is (already generic)
