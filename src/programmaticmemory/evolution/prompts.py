"""Prompt templates for the evolution system."""

from __future__ import annotations

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
        return "\\n".join(self.store)
'''


def build_reflection_user_prompt(
    code: str,
    score: float,
    failed_cases: list[dict],
    iteration: int,
) -> str:
    """Build the user prompt for the reflection LLM."""
    failed_section = ""
    for i, case in enumerate(failed_cases, 1):
        failed_section += f"\n### Failed Case {i}\n"
        failed_section += f"Question: {case.get('question', 'N/A')}\n"
        failed_section += f"Expected: {case.get('expected', 'N/A')}\n"
        failed_section += f"Got: {case.get('output', 'N/A')}\n"
        failed_section += f"Score: {case.get('score', 0)}\n"
        if case.get("conversation_history"):
            failed_section += "Conversation:\n"
            for msg in case["conversation_history"]:
                failed_section += f"  [{msg.get('role', '?')}]: {msg.get('content', '')}\n"
        if case.get("memory_logs"):
            failed_section += "Memory logs:\n"
            for log in case["memory_logs"]:
                failed_section += f"  - {log}\n"

    return f"""\
You are an expert Python programmer specializing in memory system design.

Your task: Given a Memory Program (Python code defining Observation, Query, and Memory classes), \
its evaluation score, and failed cases, diagnose the issues and fix them.

{MEMORY_INTERFACE_SPEC}

## Rules
1. Output your diagnosis first, then the complete fixed code in a ```python``` block.
2. The code must define exactly three classes: Observation, Query, Memory.
3. Memory.__init__ must accept `toolkit`; write takes an Observation; read takes a Query and returns str.
4. Keep it simple. Make minimal, targeted fixes — do not rewrite working parts.

## Current Memory Program (iteration {iteration})

```python
{code}
```

## Evaluation Score: {score:.3f}

## Failed Cases
{failed_section}

## Task
1. Diagnose why these cases fail.
2. Propose specific improvements to the Memory Program.
3. Output the complete improved code in a ```python``` block."""


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
