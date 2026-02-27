"""Routing Memory Adapter — LLM-based memory routing.

Extends MemoryAdapter with a two-step evaluation flow:
1. An LLM selects the most relevant memory entry keys for each query.
2. Only the selected entries are loaded into context for answer generation.

This enables efficient caching: when a mutation only changes one entry's content,
queries routed to other entries produce identical LLM calls and hit the litellm cache.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable

from programmaticmemory.core.adapter import EvaluationBatch
from programmaticmemory.logging import get_logger
from programmaticmemory.memory.memory_adapter import MemoryAdapter, MemoryDataInst, MemoryOutput, MemoryTrajectory
from programmaticmemory.memory.memory_store import MemoryEntry, format_memory_as_markdown, parse_memory_xml

logger = get_logger()

KEY_SELECTION_PROMPT = """\
Select up to {top_k} of the most relevant knowledge entries for answering the query below.

Available entries:
{entry_list}

Query: {query}

Output ONLY the entry numbers, comma-separated. Do not include any explanation. Example: 1, 3"""


class RoutingMemoryAdapter(MemoryAdapter):
    """MemoryAdapter with LLM-based routing — selects relevant entries per query.

    Instead of injecting all memory entries into every LLM call, this adapter
    first asks a (cheap, cached) routing LLM to pick the top-k entries per query,
    then builds per-item system prompts containing only the selected entries.

    Args:
        routing_model: Model for key selection (litellm model name or callable).
            Defaults to task_model.
        route_top_k: Maximum number of entries to select per query.
        **kwargs: Forwarded to MemoryAdapter.__init__.
    """

    EDIT_PROPOSAL_PROMPT = """\
You are optimizing a knowledge memory store used by an AI assistant. \
The memory contains key-value entries that provide the assistant with \
domain knowledge, instructions, and context to answer user queries.

## Current Memory (XML format)
```
<current_memory>
```

## Task Performance Feedback
For each query, the assistant selected and read the most relevant entries from the memory. \
The entries used are listed per example.
```
<feedback_examples>
```

## Your Task
Analyze the feedback and propose a SINGLE edit to improve the memory.

**Default action: CREATE a new entry** that captures a specific lesson or rule learned from the feedback. \
Each entry should address a distinct topic so the memory stays modular and searchable. \
Only UPDATE an existing entry instead if the feedback points to an error or gap in that specific entry's content. \
You can also DELETE entries that are unhelpful or redundant.

Entry keys are used to route queries to relevant entries — clear, descriptive keys improve routing accuracy.

**CREATE** a new entry: Add a new `<entry key="...">...</entry>` block inside the <memory> tags.
**UPDATE** an existing entry: Change the content of an entry to fix errors or add information.
**DELETE** an entry: Remove an entire `<entry key="...">...</entry>` block.
**RENAME** a key: Change an entry's key attribute.

Output a JSON object with exactly two keys:
```json
{"old_string": "exact text to find in the XML", "new_string": "replacement text"}
```

Rules:
- `old_string` must match EXACTLY (including whitespace and newlines) a substring of the current memory XML.
- For CREATE: use `old_string` = `"</memory>"` and `new_string` = `"<entry key=\\"new_key\\">content</entry>\\n</memory>"`.
- For DELETE: set `old_string` to the full `<entry key="...">...</entry>` block and `new_string` to `""`.
- If the memory is empty, create the most useful entry based on the feedback.

Output ONLY the JSON object, no other text."""

    def __init__(
        self,
        *,
        routing_model: str | Callable | None = None,
        route_top_k: int = 3,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.routing_model = routing_model or self.task_model
        if isinstance(self.routing_model, str) and not hasattr(self, "_litellm"):
            import litellm

            self._litellm = litellm
        self.route_top_k = route_top_k

    # ========================================================================
    # Reflective dataset overrides
    # ========================================================================

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[MemoryTrajectory, MemoryOutput],
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build reflective dataset with per-query routing info."""
        reflective_data = super().make_reflective_dataset(candidate, eval_batch, components_to_update)

        if "memory" not in reflective_data or not eval_batch.trajectories:
            return reflective_data

        for example, traj in zip(reflective_data["memory"], eval_batch.trajectories, strict=False):
            example["Entries Used"] = self._extract_keys_from_markdown(traj["memory_markdown"])

        return reflective_data

    @staticmethod
    def _format_feedback_examples(examples: Sequence[Mapping[str, Any]]) -> str:
        """Format reflective examples with per-query entries-used info."""
        parts = []
        for i, ex in enumerate(examples, 1):
            inputs = ex.get("Inputs", {})
            generated = ex.get("Generated Outputs", "")
            feedback = ex.get("Feedback", "")

            section = f"### Example {i}\n"
            section += f"**User Input:** {inputs.get('user_input', '')}\n"
            entries_used = ex.get("Entries Used")
            if entries_used is not None:
                section += f"**Entries Used:** {', '.join(entries_used)}\n"
            section += f"**Expected Answer:** {inputs.get('expected_answer', '')}\n"
            section += f"**Assistant Response:** {generated}\n"
            section += f"**Feedback:** {feedback}"
            parts.append(section)

        return "\n\n".join(parts)

    @staticmethod
    def _extract_keys_from_markdown(memory_markdown: str) -> list[str]:
        """Extract entry keys from rendered memory markdown."""
        return [line[3:].strip() for line in memory_markdown.split("\n") if line.startswith("## ")]

    def _build_batch_contexts(
        self,
        batch: list[MemoryDataInst],
        memory_xml: str,
    ) -> list[tuple[str, str]]:
        """Override: route each query to top-k entries via LLM."""
        try:
            entries = parse_memory_xml(memory_xml)
        except ValueError:
            entries = []

        # If entries fit within top_k, no routing needed — use all entries
        if len(entries) <= self.route_top_k:
            return super()._build_batch_contexts(batch, memory_xml)

        queries = [item["input"] for item in batch]
        per_item_indices = self._route_batch(queries, entries)

        contexts: list[tuple[str, str]] = []
        for selected_indices in per_item_indices:
            selected_entries = [entries[i] for i in selected_indices]
            md = format_memory_as_markdown(selected_entries)
            sp = self._format_system_prompt(md)
            contexts.append((sp, md))

        for idx, (sp, md) in enumerate(contexts):
            keys = self._extract_keys_from_markdown(md)
            logger.debug(f"Item {idx}: routed to {keys}", header="route")

        return contexts

    def _route_batch(
        self,
        queries: list[str],
        entries: list[MemoryEntry],
    ) -> list[list[int]]:
        """Batch LLM key-selection calls."""
        entry_list = "\n".join(f"{i + 1}. {e.key}" for i, e in enumerate(entries))

        messages_list = [
            [
                {
                    "role": "user",
                    "content": KEY_SELECTION_PROMPT.format(
                        top_k=self.route_top_k,
                        entry_list=entry_list,
                        query=query,
                    ),
                }
            ]
            for query in queries
        ]

        logger.debug(
            f"Routing {len(queries)} queries across {len(entries)} entries (top_k={self.route_top_k})",
            header="route",
        )

        # Call routing model
        responses: list[str] = []
        if isinstance(self.routing_model, str):
            try:
                batch_responses = self._litellm.batch_completion(
                    model=self.routing_model,
                    messages=messages_list,
                    temperature=0,
                    num_retries=5,
                    caching=True,
                )
                for resp in batch_responses:
                    if isinstance(resp, Exception):
                        raise resp
                    content = resp.choices[0].message.content  # type: ignore[union-attr]
                    responses.append(content.strip() if content else "")
            except Exception as e:
                logger.log(f"Routing batch completion failed: {e}", header="error")
                # Fallback: select first top_k entries for all queries
                fallback = list(range(min(self.route_top_k, len(entries))))
                return [fallback] * len(queries)
        else:
            for msgs in messages_list:
                try:
                    result = self.routing_model(msgs)
                    responses.append(result if isinstance(result, str) else str(result))
                except Exception as e:
                    logger.log(f"Routing model call failed: {e}", header="error")
                    responses.append("")

        # Parse responses into index lists
        per_item_indices: list[list[int]] = []
        for resp_text in responses:
            indices = self._parse_index_selection(resp_text, len(entries))
            if not indices:
                # Fallback: first top_k entries
                indices = list(range(min(self.route_top_k, len(entries))))
                logger.debug(
                    f"Routing parse returned empty, falling back to first {len(indices)} entries", header="route"
                )
            # Enforce top_k cap — LLM may return more than requested
            indices = indices[: self.route_top_k]
            logger.debug(f"Routed to entries: {indices} from response: {resp_text!r}", header="route")
            per_item_indices.append(indices)

        return per_item_indices

    @staticmethod
    def _parse_index_selection(text: str, num_entries: int) -> list[int]:
        """Parse comma-separated 1-indexed numbers into 0-indexed entry indices."""
        indices: list[int] = []
        for token in re.split(r"[,\n]", text):
            token = token.strip().rstrip(".")
            match = re.match(r"(\d+)", token)
            if match:
                idx = int(match.group(1)) - 1  # 1-indexed → 0-indexed
                if 0 <= idx < num_entries and idx not in indices:
                    indices.append(idx)
        return indices
