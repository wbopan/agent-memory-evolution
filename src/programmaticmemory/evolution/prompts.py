"""Prompt templates for the evolution system."""

from __future__ import annotations

from dataclasses import dataclass

from programmaticmemory.evolution.types import TrainExample


@dataclass
class ReflectionPromptConfig:
    """Controls what content is included in the reflection prompt."""

    max_failed_cases: int = 3
    max_train_examples: int = 1
    max_memory_log_chars: int = 0  # 0 = exclude memory logs entirely


MEMORY_INTERFACE_SPEC = """\
You are designing a Memory Program that implements three classes:

1. **Observation** (dataclass): Defines what information is captured when writing to memory.
   - Must be a @dataclass with typed fields
   - An external LLM will populate instances by generating JSON matching your field definitions
   - **Field types MUST be JSON-compatible**: use only str, int, float, bool, list[str], Optional[str]
   - Do NOT use datetime, tuple, bytes, or custom objects — JSON cannot represent them
   - Use `field(metadata={"description": "..."})` to describe fields — descriptions are shown to the LLM that populates instances

2. **Query** (dataclass): Defines what parameters are used when reading from memory.
   - Must be a @dataclass with typed fields
   - An external LLM will populate instances by generating JSON matching your field definitions
   - Same JSON-compatible type restriction and field description support as Observation

3. **Memory** (class): The core memory system.
   - `__init__(self, toolkit)`: Receives a Toolkit with:
     - `toolkit.db`: sqlite3.Connection (in-memory SQLite)
     - `toolkit.chroma`: chromadb ephemeral client
     - `toolkit.llm_completion(messages, **kwargs) -> str`: LLM calls (budget-limited)
     - `toolkit.logger.debug(message)`: Debug logging (use liberally — logs are visible during diagnosis and help guide future fixes)
   - `write(self, observation: Observation) -> None`: Store information
   - `read(self, query: Query) -> str`: Retrieve relevant information as a string

Allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb

## Runtime Constraints

These limits are enforced during evaluation. Violating them results in score = 0.

- **`read()` output limit**: `memory.read()` must return at most **1000 characters**. Programs that dump all stored text will fail.
- **`write()` / `read()` timeout**: Each call must complete within **5 seconds**. Avoid expensive computation in these methods.
- **`toolkit.llm_completion()` budget**: At most **50 LLM calls** per evaluation run. Use LLM calls sparingly in write/read; prefer deterministic retrieval (SQL, text matching) over LLM-based filtering.
"""

INITIAL_MEMORY_PROGRAM = '''\
from dataclasses import dataclass, field


@dataclass
class Observation:
    """Raw text observation to store in memory."""
    raw: str = field(metadata={"description": "The raw text to store"})


@dataclass
class Query:
    """Raw text query to retrieve from memory."""
    raw: str = field(metadata={"description": "The query text to search for"})


class Memory:
    """Simple append-all / return-all memory."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store: list[str] = []

    def write(self, observation: Observation) -> None:
        self.store.append(observation.raw)
        self.toolkit.logger.debug(f"Stored: {observation.raw}")

    def read(self, query: Query) -> str:
        self.toolkit.logger.debug(f"Query: {query.raw}, store size: {len(self.store)}")
        if not self.store:
            return "No information stored."
        result = "\\n".join(self.store)
        return result[:1000]
'''


_MSG_MAX_CHARS = 10_000
_MSG_HEAD = _MSG_MAX_CHARS // 2
_MSG_TAIL = _MSG_MAX_CHARS - _MSG_HEAD


def _truncate_msg(content: str) -> str:
    """Keep head and tail, elide the middle if content exceeds _MSG_MAX_CHARS."""
    if len(content) <= _MSG_MAX_CHARS:
        return content
    omitted = len(content) - _MSG_MAX_CHARS
    return content[:_MSG_HEAD] + f"\n... [{omitted} chars omitted] ...\n" + content[-_MSG_TAIL:]


def _render_messages(messages: list[dict[str, str]], indent: str = "") -> str:
    """Render a message list with truncation: [{role}]: {content}\\n per message."""
    parts = []
    for msg in messages:
        parts.append(f"{indent}[{msg.get('role', '?')}]: {_truncate_msg(msg.get('content', ''))}\n")
    return "".join(parts)


def _truncate_memory_logs(logs: list[str], max_chars: int) -> str:
    """Render memory logs with a character budget, keeping head and tail."""
    if max_chars <= 0:
        return ""
    full = "".join(f"  - {log}\n" for log in logs)
    if len(full) <= max_chars:
        return full
    head = max_chars // 2
    tail = max_chars - head
    omitted = len(full) - max_chars
    return full[:head] + f"\n  ... [{omitted} chars omitted] ...\n" + full[-tail:]


def build_reflection_user_prompt(
    code: str,
    score: float,
    failed_cases: list[dict],
    iteration: int,
    train_examples: list[TrainExample] | None = None,
    config: ReflectionPromptConfig | None = None,
) -> str:
    """Build the user prompt for the reflection LLM."""
    if config is None:
        config = ReflectionPromptConfig()

    # Apply limits
    limited_cases = failed_cases[: config.max_failed_cases]
    limited_examples = (train_examples or [])[: config.max_train_examples]

    # Detect log deduplication: check if all cases share identical memory_logs
    deduplicated_logs_section = ""
    logs_are_deduplicated = False
    cases_with_logs = [c for c in limited_cases if c.get("memory_logs")]
    if len(cases_with_logs) >= 2:
        first_logs = cases_with_logs[0]["memory_logs"]
        if all(c["memory_logs"] == first_logs for c in cases_with_logs[1:]):
            # All cases have identical logs — render once as standalone section
            rendered = _truncate_memory_logs(first_logs, config.max_memory_log_chars)
            if rendered:
                deduplicated_logs_section = f"\n<memory_debug_logs>\n{rendered}</memory_debug_logs>\n"
            logs_are_deduplicated = True

    failed_parts: list[str] = []
    for i, case in enumerate(limited_cases, 1):
        case_parts: list[str] = []
        case_parts.append(f"<question>{case.get('question', 'N/A')}</question>\n")
        case_parts.append(f"<expected>{case.get('expected', 'N/A')}</expected>\n")
        case_parts.append(f"<model_generation>{case.get('output', 'N/A')}</model_generation>\n")
        case_parts.append(f"<score>{case.get('score', 0)}</score>\n")
        if case.get("conversation_history"):
            case_parts.append("<conversation>\n")
            case_parts.append(_render_messages(case["conversation_history"], indent="  "))
            case_parts.append("</conversation>\n")
        if case.get("memory_logs") and not logs_are_deduplicated:
            rendered = _truncate_memory_logs(case["memory_logs"], config.max_memory_log_chars)
            if rendered:
                case_parts.append(f"<memory_logs>\n{rendered}</memory_logs>\n")
        failed_parts.append(f'<case id="{i}">\n{"".join(case_parts)}</case>\n')
    failed_section = "\n".join(failed_parts)

    train_section = ""
    if limited_examples:
        train_parts: list[str] = []
        for i, example in enumerate(limited_examples, 1):
            train_parts.append(f'<conversation id="{i}">\n{_render_messages(example.messages)}</conversation>\n')
        train_section = f"""
The following are example write trajectories from the evaluation. \
They show how the external LLM generates Observations from raw document text and how `memory.write()` is called. \
Read these to understand the format of the source documents.

<write_examples>
{"".join(train_parts)}</write_examples>
"""

    if deduplicated_logs_section:
        deduplicated_logs_section = f"""
The following debug logs were produced by the current Memory Program during the write examples above. \
These are the outputs of `toolkit.logger.debug()` calls within `write()` and `read()`.

{deduplicated_logs_section}"""

    failed_cases_header = """
The following cases show poor performance on the validation set after memory has been written \
(using the same write process shown in the write examples above). \
Each case contains the full retrieval-and-answer conversation trajectory."""

    return f"""\
You are an expert Python programmer specializing in memory system design.

Your task: Given a Memory Program (Python code defining Observation, Query, and Memory classes), \
its evaluation score, and failed cases, diagnose the issues and fix them.

<interface_spec>
{MEMORY_INTERFACE_SPEC}
</interface_spec>

<rules>
1. Output your diagnosis first, then the complete fixed code in a ```python``` block.
2. The code must define exactly three classes: Observation, Query, Memory.
3. Memory.__init__ must accept `toolkit`; write takes an Observation; read takes a Query and returns str.
4. `read()` must return at most 1000 characters — do not return all stored text.
5. Keep it simple. Make minimal, targeted fixes — do not rewrite working parts.
6. Add clear comments explaining WHY each part of the code works the way it does — this helps future iterations understand and preserve your design decisions.
</rules>

<current_program iteration="{iteration}">
```python
{code}
```
</current_program>

<evaluation_score>{score:.3f}</evaluation_score>
{train_section}{deduplicated_logs_section}
{failed_cases_header}

<failed_cases>
{failed_section}
</failed_cases>

<task>
1. Diagnose why these cases fail.
2. Propose specific improvements to the Memory Program.
3. Output the complete improved code in a ```python``` block.
</task>"""


def build_observation_generation_prompt(raw_text: str, schema: str) -> str:
    """Prompt the task agent LLM to generate an Observation from raw text."""
    return f"""\
Given the following text, create an Observation object to store this information in memory.

Text: {raw_text}

The Observation must conform to this schema:
{schema}

Output ONLY a valid JSON object matching the schema fields. No explanation."""


def build_query_generation_prompt(question: str, schema: str) -> str:
    """Prompt the task agent LLM to generate a Query from a question.

    Used as a user message in the multi-turn conversation (Step 1).
    """
    return f"""\
Given the following question, generate a query to retrieve relevant memory.

Question: {question}

The query must be a JSON object matching this schema:
{schema}

Respond with the JSON only."""


def build_retrieved_memory_prompt(retrieved: str) -> str:
    """Prompt the task agent LLM to answer based on retrieved memory.

    Used as a user message in the multi-turn conversation (Step 2).
    The LLM sees the full conversation history including its own query from Step 1.
    """
    return f"""\
<retrieved_memory>
{retrieved}
</retrieved_memory>

Based on the above memory and the original question, provide your answer."""


def build_observation_with_feedback_prompt(
    evaluation_result: str,
    ground_truth: str,
    schema: str,
) -> str:
    """Prompt the task agent LLM to generate an Observation informed by feedback.

    Used as a user message in Type B train (Step 3).
    The LLM sees the full conversation history including query, retrieval, and answer.
    """
    return f"""\
Evaluation result: {evaluation_result}
Ground truth: {ground_truth}

Based on this feedback, generate an observation to write into memory.

The observation must be a JSON object matching this schema:
{schema}

Respond with the JSON only."""


def build_compile_fix_prompt(code: str, error_type: str, error_details: str) -> str:
    """Build user prompt for fixing a compile/runtime error."""
    return f"""\
You are an expert Python programmer. A Memory Program failed to compile or run.
Fix the error and output the complete corrected code in a ```python``` block.

{MEMORY_INTERFACE_SPEC}

Rules:
1. Output ONLY the corrected code in a ```python``` block. No explanation needed.
2. The code must define exactly three classes: Observation, Query, Memory.
3. Only use allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb.
4. Make minimal changes — fix only what's broken.

## Broken Code

```python
{code}
```

## Error

**{error_type}**: {error_details}

Fix the error and output the complete corrected code in a ```python``` block."""
