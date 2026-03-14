"""Prompt templates for the evolution system."""

from __future__ import annotations

import random
from dataclasses import dataclass

from programmaticmemory.evolution.types import PoolEntry, ProgramPool, TrainExample, diff_functions


def _sample_cases(cases: list[dict], k: int, seed: int) -> list[dict]:
    """Weighted random sample of k cases, biased toward lower scores.

    Uses Efraimidis-Spirakis algorithm: key_i = random()^(1/weight_i),
    top-k by key. Weight = 1 - score, so lower scores are more likely.
    """
    if len(cases) <= k:
        return cases
    rng = random.Random(seed)
    weights = [1.0 - case.get("score", 0.0) for case in cases]
    # Efraimidis-Spirakis: key = u^(1/w), select top-k
    keys = [rng.random() ** (1.0 / w) if w > 0 else 0.0 for w in weights]
    indices = sorted(range(len(cases)), key=lambda i: keys[i], reverse=True)[:k]
    # Preserve original order
    return [cases[i] for i in sorted(indices)]


@dataclass
class ReflectionPromptConfig:
    """Controls what content is included in the reflection prompt."""

    max_failed_cases: int = 2
    max_success_cases: int = 1
    max_train_examples: int = 1
    max_memory_log_chars: int = 0  # 0 = exclude memory logs entirely


KB_INTERFACE_SPEC = """\
You are designing a Knowledge Base Program that implements three classes:

1. **KnowledgeItem** (dataclass): Defines what information is captured as knowledge items when writing to the knowledge base.
   - Must be a @dataclass with typed fields
   - An external LLM will populate instances by generating JSON matching your field definitions
   - **Field types MUST be JSON-compatible**: use only str, int, float, bool, list[str], Optional[str]
   - Do NOT use datetime, tuple, bytes, or custom objects — JSON cannot represent them
   - Use `field(metadata={"description": "..."})` to describe fields — descriptions are shown to the LLM that populates instances

2. **Query** (dataclass): Defines what parameters are used when reading from the knowledge base.
   - Must be a @dataclass with typed fields
   - An external LLM will populate instances by generating JSON matching your field definitions
   - Same JSON-compatible type restriction and field description support as KnowledgeItem

3. **KnowledgeBase** (class): The core knowledge base system.
   - `__init__(self, toolkit)`: Receives a Toolkit with:
     - `toolkit.db`: sqlite3.Connection (in-memory SQLite)
     - `toolkit.chroma`: chromadb ephemeral client
     - `toolkit.llm_completion(messages, **kwargs) -> str`: LLM for reasoning, summarization, and information extraction (1 call per write/read invocation)
     - `toolkit.logger.debug(message)`: Debug logging (use liberally — logs are visible during diagnosis and help guide future fixes)
   - `write(self, item: KnowledgeItem, raw_text: str) -> None`: Store information. `raw_text` is the original source text that produced the knowledge item.
   - `read(self, query: Query) -> str`: Retrieve relevant information as a string

Allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb

## Runtime Constraints

These limits are enforced during evaluation. Violating them results in score = 0.

- **`read()` output limit**: `kb.read()` must return at most **1000 characters**. Programs that dump all stored text will fail.
- **`write()` / `read()` timeout**: Each call must complete within **60 seconds**. Avoid expensive computation in these methods.
- **`toolkit.llm_completion()` budget**: At most **1 LLM call per `write()` or `read()` invocation**. The budget resets before each call. This is a powerful AI model capable of reasoning over large amounts of text — use it wisely (e.g., query-focused summarization, re-ranking, or information extraction in `read()`). Deterministic retrieval alone often misses semantic matches; combining it with an LLM call for final synthesis is a strong pattern.

## Instruction Constants (required)

Four module-level string constants provide the natural-language instructions used in task agent prompts:

- INSTRUCTION_KNOWLEDGE_ITEM: Instruction for knowledge item generation. Tells the task LLM what to extract and how to structure it. The KnowledgeItem schema is provided separately — no need to describe field names or types here.
- INSTRUCTION_QUERY: Instruction for query generation. Tells the task LLM how to formulate retrieval queries. The Query schema is provided separately — no need to describe field names or types here.
- INSTRUCTION_RESPONSE: Instruction for answer generation. Controls answer format, length, and style.
- ALWAYS_ON_KNOWLEDGE: Persistent knowledge injected into every task agent prompt. Use this to steer the task agent's behavior and decision-making. Unlike INSTRUCTION_* (output format), this provides always-on context. Can be empty.

INSTRUCTION_KNOWLEDGE_ITEM, INSTRUCTION_QUERY, and INSTRUCTION_RESPONSE must not be empty. ALWAYS_ON_KNOWLEDGE can be empty.
"""

INITIAL_KB_PROGRAM = '''\
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer without explanation."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A summary of what was learnt from the source text."""
    summary: str = field(metadata={"description": "What you have learnt from the text"})


@dataclass
class Query:
    """Raw text query to retrieve from the knowledge base."""
    raw: str = field(metadata={"description": "The query text to search for"})


class KnowledgeBase:
    """Simple append-all / return-all knowledge base."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.summaries: list[str] = []
        self.observations: list[str] = []

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.summaries.append(item.summary)
        self.observations.append(raw_text)
        self.toolkit.logger.debug(f"Stored summary: {item.summary}")

    def read(self, query: Query) -> str:
        self.toolkit.logger.debug(f"Query: {query.raw}, summaries: {len(self.summaries)}, observations: {len(self.observations)}")
        if not self.summaries and not self.observations:
            return "No information stored."
        summary_text = "\\n".join(self.summaries)[:500]
        observation_text = "\\n".join(self.observations)[:500]
        result = summary_text + "\\n" + observation_text
        return result[:1000]
'''


PATCH_FORMAT_SPEC = """\
Before the patch, output a commit message summarizing your changes:

```
*** Commit Message
Title: <one-line summary of what you changed and why>
- <root cause / diagnosis>
- <what you changed>
```

Then output your changes as a V4A patch. The patch is applied to the current program shown in <current_program>.

Format:
```
*** Begin Patch
*** Update File: program.py
@@ <optional context hint>
 context line (1-2 lines before change)
-removed line
+added line
 context line (1-2 lines after change)
*** End Patch
```

Rules:
- Lines prefixed with `-` are removed, `+` are added, ` ` (space) are unchanged context.
- Include 1-2 context lines before and after each change for anchoring.
- Multiple hunks are allowed within one `*** Update File` block.

Example — replacing a return value:
```
*** Commit Message
Title: Truncate read output to respect 1000-char limit
- read() returned all stored text, exceeding the limit
- Added [:1000] truncation to the return value

*** Begin Patch
*** Update File: program.py
@@ return statement
 def read(self, query: Query) -> str:
-    return "\\n".join(self.store)
+    return "\\n".join(self.store[-5:])
*** End Patch
```
"""

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


@dataclass
class ReferenceProgram:
    """A reference program shown to the reflector for cross-program context."""

    source_code: str
    score: float
    relationship: str  # e.g. "best_sibling", "latest_child", "parent"


def build_reflection_user_prompt(
    code: str,
    score: float,
    failed_cases: list[dict],
    iteration: int,
    train_examples: list[TrainExample] | None = None,
    config: ReflectionPromptConfig | None = None,
    success_cases: list[dict] | None = None,
    references: list[ReferenceProgram] | None = None,
    lineage_log: str | None = None,
) -> str:
    """Build the user prompt for the reflection LLM."""
    if config is None:
        config = ReflectionPromptConfig()

    # Apply limits — weighted random sample for failed cases (diversity)
    limited_cases = _sample_cases(failed_cases, config.max_failed_cases, seed=iteration)
    limited_success = (success_cases or [])[: config.max_success_cases]
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
They show how the external LLM generates knowledge items from raw document text and how `memory.write()` is called. \
Read these to understand the format of the source documents.

<write_examples>
{"".join(train_parts)}</write_examples>
"""

    if deduplicated_logs_section:
        deduplicated_logs_section = f"""
The following debug logs were produced by the current Knowledge Base Program during the write examples above. \
These are the outputs of `toolkit.logger.debug()` calls within `write()` and `read()`.

{deduplicated_logs_section}"""

    # Build success cases section
    success_section = ""
    if limited_success:
        success_parts: list[str] = []
        for i, case in enumerate(limited_success, 1):
            case_parts: list[str] = []
            case_parts.append(f"<question>{case.get('question', 'N/A')}</question>\n")
            case_parts.append(f"<expected>{case.get('expected', 'N/A')}</expected>\n")
            case_parts.append(f"<model_generation>{case.get('output', 'N/A')}</model_generation>\n")
            case_parts.append(f"<score>{case.get('score', 0)}</score>\n")
            if case.get("conversation_history"):
                case_parts.append("<conversation>\n")
                case_parts.append(_render_messages(case["conversation_history"], indent="  "))
                case_parts.append("</conversation>\n")
            success_parts.append(f'<case id="{i}">\n{"".join(case_parts)}</case>\n')
        success_section = f"""
The following case(s) show successful performance — the current program handled these correctly. \
Preserve the behavior that makes these work.

<success_cases>
{"".join(success_parts)}</success_cases>
"""

    # Build reference programs section
    reference_section = ""
    if references:
        ref_parts: list[str] = []
        for ref in references:
            label = {
                "best_sibling": "Best program from a different lineage",
                "latest_child": "Latest child derived from the current program",
                "parent": "Parent of the current program",
            }.get(ref.relationship, ref.relationship)
            ref_parts.append(
                f'<reference relationship="{ref.relationship}" label="{label}" '
                f'score="{ref.score:.3f}" current_score="{score:.3f}">\n'
                f"```python\n{ref.source_code}\n```\n"
                f"</reference>\n"
            )
        reference_section = f"""
The following reference programs from the population show what other designs score. \
Study which design patterns (e.g., use of `toolkit.llm_completion()`, ChromaDB vs SQLite, schema granularity, \
retrieval logic) correlate with higher or lower scores. \
If a higher-scoring reference uses a pattern the current program lacks, consider adopting it. \
If the current program has a unique pattern absent from lower-scoring references, preserve it.

<reference_programs>
{"".join(ref_parts)}</reference_programs>
"""

    lineage_section = ""
    if lineage_log:
        lineage_section = f"""
The following is the evolution history of the current program's lineage. \
Each entry shows what was changed, what functions were added/removed, and the resulting score. \
Pay close attention to REGRESSION markers — these indicate changes that hurt performance. \
Do NOT repeat changes that previously caused regressions.

<lineage_log>
{lineage_log}</lineage_log>
"""

    failed_cases_header = """
The following cases show poor performance on the validation set after memory has been written \
(using the same write process shown in the write examples above). \
Each case contains the full retrieval-and-answer conversation trajectory."""

    return f"""\
You are an expert Python programmer specializing in knowledge base system design.

Your task: Given a Knowledge Base Program, its evaluation score, and underperforming cases, \
identify the root cause of each low score and improve the program. \
Improvements are two-fold: (A) **Prompt Optimization** — tune the four instruction constants \
(especially ALWAYS_ON_KNOWLEDGE) to steer the task agent's behavior, \
and (B) **Memory Design** — improve the KnowledgeItem/Query schemas and KnowledgeBase storage/retrieval logic. \
Both dimensions matter and should be considered together.

<interface_spec>
{KB_INTERFACE_SPEC}
</interface_spec>

<rules>
1. Output your diagnosis first, then your changes as a patch using the format below.
2. The code must define exactly three classes (KnowledgeItem, Query, KnowledgeBase) and four module-level string constants (INSTRUCTION_KNOWLEDGE_ITEM, INSTRUCTION_QUERY, INSTRUCTION_RESPONSE, ALWAYS_ON_KNOWLEDGE).
3. KnowledgeBase.__init__ must accept `toolkit`; write takes a KnowledgeItem; read takes a Query and returns str.
4. `read()` must return at most 1000 characters — do not return all stored text.
5. Keep it simple. Make minimal changes that generalize beyond the specific cases shown — no hardcoded word lists or case-specific pattern rules.
6. **Prompt Optimization**: Update INSTRUCTION_* to steer the task LLM's output format. \
Update ALWAYS_ON_KNOWLEDGE with domain strategies, heuristics, and behavioral rules the task agent should always follow \
— this constant is injected into EVERY task agent action/decision prompt and is often the highest-leverage change. \
Study the <model_generation> transcripts in the underperforming cases to identify agent behavioral patterns \
(e.g., looping, inefficient exploration, wrong object selection) that ALWAYS_ON_KNOWLEDGE can fix.
7. **Memory Design**: Improve KnowledgeItem/Query field schemas and KnowledgeBase read()/write() logic \
to store and retrieve more useful information for the task agent.
8. Add clear comments explaining WHY each part of the code works the way it does — this helps future iterations understand and preserve your design decisions.
</rules>

<patch_format>
{PATCH_FORMAT_SPEC}</patch_format>

<current_program iteration="{iteration}">
```python
{code}
```
</current_program>

<evaluation_score>{score:.3f}</evaluation_score>
{lineage_section}{train_section}{deduplicated_logs_section}{success_section}{reference_section}
{failed_cases_header}

<underperforming_cases>
{failed_section}
</underperforming_cases>

<task>
1. Diagnose why these cases scored low — examine both the retrieval conversation AND the \
<model_generation> transcript for agent behavioral issues.
2. Propose improvements along two dimensions:
   (A) **Prompt Optimization**: How should INSTRUCTION_* and ALWAYS_ON_KNOWLEDGE change to steer the task agent better?
   (B) **Memory Design**: How should the schemas or storage/retrieval logic change to provide more useful information?
3. Output your changes as a patch.
</task>"""


def build_lineage_log(pool: ProgramPool, entry: PoolEntry) -> str:
    """Build a git-log-style lineage history for a program entry."""
    hash_to_entry = {e.program.hash: e for e in pool.entries}

    # Walk ancestor chain upward
    ancestors: list[PoolEntry] = []
    current = entry
    while current.program.parent_hash and current.program.parent_hash in hash_to_entry:
        parent = hash_to_entry[current.program.parent_hash]
        ancestors.append(parent)
        current = parent
    ancestors.reverse()

    # Find direct children
    children = [e for e in pool.entries if e.program.parent_hash == entry.program.hash]

    lines: list[str] = []

    def _format_entry(e: PoolEntry, parent_entry: PoolEntry | None, is_current: bool = False) -> None:
        if is_current:
            lines.append(f"\n* current: {e.program.hash} ({e.name}) score={e.score:.3f}  \u2190 you are improving this")
            msg = e.commit_message or "Initial seed program"
            for msg_line in msg.splitlines():
                lines.append(f"  {msg_line}")
            lines.append("")
            return
        header = f"commit {e.program.hash} ({e.name}) score={e.score:.3f}"
        if parent_entry is not None:
            delta = e.score - parent_entry.score
            header += f" (\u0394{delta:+.3f})"
            if delta < 0:
                header += " \u2190 REGRESSION"
        lines.append(header)
        msg = e.commit_message or "Initial seed program"
        for msg_line in msg.splitlines():
            lines.append(f"  {msg_line}")
        if parent_entry is not None:
            added, removed = diff_functions(parent_entry.program.source_code, e.program.source_code)
            if added:
                lines.append(f"  + {', '.join(f'{n}()' for n in added)}")
            if removed:
                lines.append(f"  - {', '.join(f'{n}()' for n in removed)}")
        lines.append("")

    prev: PoolEntry | None = None
    for anc in ancestors:
        _format_entry(anc, prev)
        prev = anc

    _format_entry(entry, prev, is_current=True)

    for child in children:
        _format_entry(child, entry)

    return "\n".join(lines)


def build_knowledge_item_generation_prompt(
    raw_text: str,
    schema: str,
    instruction: str = "Given the following text, create a KnowledgeItem to store this information in memory.",
) -> str:
    """Prompt the task agent LLM to generate a KnowledgeItem from raw text."""
    return f"""\
{instruction}

Text: {raw_text}

The KnowledgeItem must conform to this schema:
{schema}

Output ONLY a valid JSON object matching the schema fields. No explanation."""


def build_query_generation_prompt(
    question: str,
    schema: str,
    instruction: str = "Given the following question, generate a query to retrieve relevant memory.",
) -> str:
    """Prompt the task agent LLM to generate a Query from a question.

    Used as a user message in the multi-turn conversation (Step 1).
    """
    return f"""\
{instruction}

Question: {question}

The query must be a JSON object matching this schema:
{schema}

Respond with the JSON only."""


def build_retrieved_memory_prompt(
    retrieved: str,
    instruction: str = "Based on the above memory and the original question, provide your answer.",
    always_on_knowledge: str = "",
) -> str:
    """Prompt the task agent LLM to answer based on retrieved memory.

    Used as a user message in the multi-turn conversation (Step 2).
    The LLM sees the full conversation history including its own query from Step 1.
    """
    if always_on_knowledge and always_on_knowledge.strip():
        memory_content = f"{always_on_knowledge.strip()}\n\n{retrieved}"
    else:
        memory_content = retrieved
    return f"""\
<retrieved_memory>
{memory_content}
</retrieved_memory>

{instruction}"""


def build_knowledge_item_with_feedback_prompt(
    evaluation_result: str,
    ground_truth: str,
    schema: str,
    instruction: str = "Based on this feedback, generate a knowledge item to write into memory.",
) -> str:
    """Prompt the task agent LLM to generate a KnowledgeItem informed by feedback.

    Used as a user message in Type B train (Step 3).
    The LLM sees the full conversation history including query, retrieval, and answer.
    """
    prompt = f"""\
Evaluation result: {evaluation_result}
Ground truth: {ground_truth}

{instruction}

The knowledge item must be a JSON object matching this schema:
{schema}

Respond with the JSON only."""
    return prompt


def build_compile_fix_prompt(code: str, error_type: str, error_details: str) -> str:
    """Build user prompt for fixing a compile/runtime error."""
    return f"""\
You are an expert Python programmer. A Knowledge Base Program failed to compile or run.
Fix the error and output your fix as a patch.

{KB_INTERFACE_SPEC}

Rules:
1. Output ONLY the fix as a patch. No explanation needed.
2. The code must define exactly three classes (KnowledgeItem, Query, KnowledgeBase) and four module-level string constants (INSTRUCTION_KNOWLEDGE_ITEM, INSTRUCTION_QUERY, INSTRUCTION_RESPONSE, ALWAYS_ON_KNOWLEDGE).
3. Only use allowed imports: json, re, math, hashlib, collections, dataclasses, typing, datetime, textwrap, sqlite3, chromadb.
4. Make minimal changes — fix ONLY the broken line(s). Do NOT add new fields, imports, or restructure the code.

{PATCH_FORMAT_SPEC}
## Broken Code

```python
{code}
```

## Error

**{error_type}**: {error_details}

Fix the error and output your fix as a patch."""
