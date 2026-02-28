# Batch Evaluation Design

**Date:** 2026-02-28
**Status:** Approved

## Problem

The current `MemoryEvaluator` processes each `DataItem` sequentially, issuing one LLM call at a time. For offline benchmarks with 30+ training items, this is the dominant bottleneck — LLM calls are independent across items but are artificially serialized.

## Solution

Add a `batch_process: bool = True` flag to `MemoryEvaluator` that, when enabled, fans out all independent LLM calls in a single `litellm.batch_completion` invocation. Memory operations (`write`/`read`) remain serial because the underlying stores (SQLite, ChromaDB) are not thread-safe for concurrent writes.

## API Change

```python
class MemoryEvaluator:
    def __init__(
        self,
        scorer: Scorer | None = None,
        task_model: str = "openrouter/deepseek/deepseek-v3.2",
        toolkit_config: ToolkitConfig | None = None,
        batch_process: bool = True,   # NEW
    ) -> None:
```

`batch_process=False` preserves the current sequential behavior exactly (useful for debugging and test snapshots).

## Per-Phase Behavior

### Offline Train (`_evaluate_offline`)

**Sequential (current):** `for item in train_data: llm_call() → memory.write()`

**Batched (`batch_process=True`):**
1. Build all N observation prompts
2. `batch_completion(messages=[...N prompts...])` → N responses (parallel)
3. Parse each response → `obs_cls(**data)`, collect errors
4. Serial `memory.write(obs)` for each valid observation

### Validation (`_evaluate_val`, shared by offline and online)

**Sequential (current):** `for item: llm_call(query) → memory.read() → llm_call(answer) → score()`

**Batched (`batch_process=True`):**
1. Round 1: `batch_completion` for all M query-generation prompts → M responses
2. Parse all queries; serial `memory.read(query)` for each → retrieved strings
3. Round 2: `batch_completion` for all M answer prompts (with retrieved memory) → M responses
4. Score all answers serially

Total LLM round-trips: 2 (instead of 2M).

### Online Train (`_evaluate_online`)

**Sequential (current):** `for item: llm_call(query) → memory.read() → llm_call(answer) → llm_call(obs) → memory.write()`

**Batched (`batch_process=True`):**
1. Round 1: `batch_completion` for all N query-generation prompts
2. Parse all queries; serial `memory.read(query)` for each
3. Round 2: `batch_completion` for all N answer prompts → N answers + scores
4. Round 3: `batch_completion` for all N obs-with-feedback prompts
5. Parse all observations; serial `memory.write(obs)` for each valid obs

**Trade-off:** All writes happen after all reads, so items within the batch cannot see each other's observations. This is an acceptable simplification — the alternative (sub-batching) adds complexity without clear benefit for current benchmarks.

## Implementation Notes

- Use `litellm.batch_completion(model, messages=[[...], [...], ...])` — returns `List[ModelResponse | Exception]`
- Check each result for `isinstance(result, Exception)` before calling `.choices[0].message.content`
- No `max_workers` needed — default (100) is sufficient for our batch sizes; litellm uses `ThreadPoolExecutor` internally
- CLI flag `--no-batch` maps to `batch_process=False` (passed through `__main__.py` → `MemoryEvaluator`)

## Files to Change

1. `src/programmaticmemory/evolution/evaluator.py` — main change
2. `src/programmaticmemory/evolution/__main__.py` — add `--no-batch` CLI flag
3. `tests/evolution/test_evaluator.py` — update tests; sequential path must still be tested
