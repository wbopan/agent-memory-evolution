# Design: Train Write Examples in Reflection Prompt

## Problem

The reflector doesn't know that `write()` is called **once per conversation** in offline evaluation. It sees only failed validation cases (query→answer turns) with no visibility into the training phase. This causes it to propose schemas that assume multiple writes per conversation (e.g., `speaker/content` per turn), which destroys performance — as observed in the 2026-02-28 experiment where a reflected program dropped from score 0.111 → 0.009 because only 1 speaker turn was stored per conversation instead of the full text.

## Solution

Capture the actual LLM message turns that occur during offline training writes, and include them in the reflection prompt so the reflector can see: "this full conversation → one write() call → this Observation JSON".

## Design

### Data Model (`types.py`)

New type:

```python
@dataclass
class TrainExample:
    """One training write: the full message exchange that generated an Observation."""
    messages: list[dict[str, str]]  # [{"role":"user",...}, {"role":"assistant",...}]
```

`EvalResult` gets a new field:

```python
train_examples: list[TrainExample] = field(default_factory=list)
```

### Capture (`evaluator.py` — batch path only)

In `_evaluate_offline`, after the batch LLM call and before `_evaluate_val`:

```python
train_examples = []
for msgs, content in list(zip(all_messages, responses))[:3]:
    if content is not None:
        train_examples.append(
            TrainExample(messages=[*msgs, {"role": "assistant", "content": content}])
        )
result = self._evaluate_val(...)
result.train_examples = train_examples
return result
```

Sequential path (`--no-batch`) keeps `train_examples=[]` — no changes needed.
Online mode excluded for now (write happens at end of multi-turn feedback loop; different semantics).

### Prompt Section (`prompts.py`)

`build_reflection_user_prompt` gets a new optional parameter:

```python
def build_reflection_user_prompt(
    code: str,
    score: float,
    failed_cases: list[dict],
    iteration: int,
    train_examples: list[dict] | None = None,
) -> str:
```

New section inserted between "Evaluation Score" and "Failed Cases":

```
## Training Write Examples

### Write Example 1
[user]: <full observation generation prompt, _truncate_msg applied>
[assistant]: <generated obs JSON>

### Write Example 2
...
```

Uses the existing `_truncate_msg` helper (10,000 char cap per message), consistent with how `conversation_history` is rendered in failed cases.

If `train_examples` is `None` or empty, the section is omitted.

### Reflector (`reflector.py`)

```python
train_example_dicts = [{"messages": te.messages} for te in eval_result.train_examples]
user_prompt = build_reflection_user_prompt(
    code=current.source_code,
    score=eval_result.score,
    failed_cases=failed_dicts,
    iteration=iteration,
    train_examples=train_example_dicts,
)
```

## Files Modified

| File | Change |
|------|--------|
| `src/programmaticmemory/evolution/types.py` | Add `TrainExample` dataclass; add `train_examples` to `EvalResult` |
| `src/programmaticmemory/evolution/evaluator.py` | Capture first 3 train examples in `_evaluate_offline` batch path |
| `src/programmaticmemory/evolution/prompts.py` | Add `train_examples` param; add new prompt section |
| `src/programmaticmemory/evolution/reflector.py` | Serialize and pass `train_examples` to prompt builder |
| `tests/evolution/test_prompts.py` | Add `test_includes_train_examples` snapshot test |
| `tests/evolution/__snapshots__/test_prompts.ambr` | Regenerated via `--snapshot-update` |
| `tests/evolution/__snapshots__/test_reflector.ambr` | Regenerated via `--snapshot-update` |

## Verification

```bash
uv run pytest tests/evolution/ -m "not llm" --snapshot-update -v
uv run pytest tests/evolution/ -m "not llm" -v
uv run ruff check src/ && uv run ruff format src/
```
