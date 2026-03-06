# Add Retrieval Conversation History to ValScorer Path

## Problem

`_val_scorer_path` in `evaluator.py` omits `conversation_history` when building `FailedCase` objects. This means the reflection LLM cannot see the KB query generation or retrieval results for ALFWorld (and any future ValScorer benchmarks). Without this, the reflector cannot distinguish "bad KB retrieval strategy" from "bad agent execution".

The default path (`_default_answer_and_score`) already includes this information.

## Design

**Scope**: `evaluator.py`, `_val_scorer_path` method only (~10 lines changed).

Extract retrieval conversation from the corresponding `_QuerySlot` and include it in `FailedCase.conversation_history`, mirroring the default path:

```python
slot = slots[i]
conv = (
    [
        {"role": "user", "content": slot.query_prompt},
        {"role": "assistant", "content": slot.query_json},
        {"role": "user", "content": slot.retrieved_prompt},
    ]
    if slot is not None
    else []
)
case = FailedCase(..., conversation_history=conv, ...)
```

The default path includes a 4th entry (`{"role": "assistant", "content": answer}`) for the LLM-generated answer. The ValScorer path omits this because the "answer" is the episode transcript, already captured in the `output` field.

## Reflection Prompt Rendering

No changes to `prompts.py`. The existing `build_reflection_user_prompt` already renders `conversation_history` as `<conversation>` inside each `<case>`. Each failed case will now show:

- `<question>`: task objective
- `<expected>`: expected answer
- `<model_generation>`: episode transcript (ACTION/OBSERVATION sequence)
- `<score>`: 0.0 or partial
- `<conversation>`: query generation prompt, query JSON, retrieved KB content

## What Does Not Change

- `types.py` — `FailedCase` already has `conversation_history` field
- `prompts.py` — already handles `conversation_history` rendering
- `reflector.py` — already passes `conversation_history` to prompt builder
- Other benchmarks — unaffected (only ValScorer path is modified)

## Test Impact

- Snapshot updates required for any tests exercising `_val_scorer_path`
- No structural test changes needed
