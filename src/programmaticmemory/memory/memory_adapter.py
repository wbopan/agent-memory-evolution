"""Memory Adapter — Optimizes a set of memory entries via edit-based mutation.

Evolves a key-value memory store using find-and-replace edits proposed by a
reflection LLM. The memory is injected into a base system prompt and used to
answer tasks evaluated by a user-supplied evaluator.
"""

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Callable, Protocol, TypedDict

from typing_extensions import NotRequired

from programmaticmemory.core.adapter import Adapter, EvaluationBatch
from programmaticmemory.logging import get_logger
from programmaticmemory.memory.memory_store import (
    EditOperation,
    apply_edit,
    format_memory_as_markdown,
    parse_memory_xml,
)

logger = get_logger()


# ============================================================================
# Type Definitions
# ============================================================================


class MemoryDataInst(TypedDict):
    """Dataset item for memory-based task evaluation.

    Attributes:
        input: The user's question or request.
        answer: The reference/expected answer.
        additional_context: Optional extra context for the evaluator.
    """

    input: str
    answer: str
    additional_context: NotRequired[dict[str, str]]


class MemoryTrajectory(TypedDict):
    """Execution trace for a memory-augmented task invocation."""

    data: MemoryDataInst
    system_prompt: str
    memory_markdown: str
    full_assistant_response: str
    score: float
    feedback: str


class MemoryOutput(TypedDict):
    """Output from memory-augmented evaluation."""

    full_assistant_response: str


class EvaluationResult(TypedDict):
    """Result from the user-supplied evaluator."""

    score: float
    feedback: str


class Evaluator(Protocol):
    """Protocol for user-supplied evaluators."""

    def __call__(self, data: MemoryDataInst, response: str) -> EvaluationResult: ...


# ============================================================================
# Memory Adapter
# ============================================================================


class MemoryAdapter(Adapter[MemoryDataInst, MemoryTrajectory, MemoryOutput]):
    """Adapter for optimizing a memory store via edit-based mutation.

    This adapter evolves a set of memory entries (key -> content) stored in XML
    format. Instead of full-text rewrite, it uses find-and-replace edits proposed
    by a reflection LLM. The memory is rendered as markdown and appended to a
    base system prompt for task execution.

    Example:
        >>> adapter = MemoryAdapter(
        ...     task_model="gpt-4o-mini",
        ...     reflection_model="gpt-4o",
        ...     evaluator=my_evaluator,
        ...     base_system_prompt="You are a helpful assistant.",
        ... )
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
The assistant used the above memory to answer the following queries. \
Here is how it performed:
```
<feedback_examples>
```

## Your Task
Analyze the feedback and propose a SINGLE edit to improve the memory.

**Default action: CREATE a new entry** that captures a specific lesson or rule learned from the feedback. \
Each entry should address a distinct topic so the memory stays modular and searchable. \
Only UPDATE an existing entry instead if the feedback points to an error or gap in that specific entry's content. \
You can also DELETE entries that are unhelpful or redundant.

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
        task_model: str | Callable,
        reflection_model: str | Callable,
        evaluator: Evaluator,
        base_system_prompt: str = "You are a helpful assistant.",
        max_entries: int = 50,
        max_retries: int = 2,
        failure_score: float = 0.0,
    ):
        """Initialize MemoryAdapter.

        Args:
            task_model: Model for task execution (litellm model name or callable).
            reflection_model: Model for proposing edits (litellm model name or callable).
            evaluator: Scoring function: (data, response) -> EvaluationResult.
            base_system_prompt: Base system prompt (memory is appended to this).
            max_entries: Maximum number of memory entries allowed.
            max_retries: Number of retries for failed edit proposals.
            failure_score: Score assigned when execution fails.
        """
        self.base_system_prompt = base_system_prompt
        self.max_entries = max_entries
        self.max_retries = max_retries
        self.failure_score = failure_score
        self.evaluator = evaluator

        # Setup task model
        if isinstance(task_model, str):
            import litellm

            self._litellm = litellm
        self.task_model = task_model

        # Setup reflection model
        if isinstance(reflection_model, str):
            if not hasattr(self, "_litellm"):
                import litellm

                self._litellm = litellm
        self.reflection_model = reflection_model

        # Register custom proposal function
        self.propose_new_texts = self._propose_new_texts  # type: ignore[assignment]

    # ========================================================================
    # Adapter protocol methods
    # ========================================================================

    def evaluate(
        self,
        batch: list[MemoryDataInst],
        candidate: dict[str, str],
        capture_traces: bool = False,
    ) -> EvaluationBatch[MemoryTrajectory, MemoryOutput]:
        """Evaluate candidate memory on a batch of data."""
        memory_xml = candidate.get("memory", "<memory>\n</memory>")
        per_item_contexts = self._build_batch_contexts(batch, memory_xml)

        logger.log(
            f"Evaluating batch of {len(batch)} items (capture_traces={capture_traces})",
            header="evaluate",
        )
        logger.debug(
            f"Memory entries: {len(parse_memory_xml(memory_xml)) if memory_xml.strip() else 0}", header="evaluate"
        )
        logger.debug(f"System prompt:\n{per_item_contexts[0][0] if per_item_contexts else ''}", header="prompt")

        outputs: list[MemoryOutput] = []
        scores: list[float] = []
        trajectories: list[MemoryTrajectory] | None = [] if capture_traces else None

        # Build messages for batch
        messages_list = [
            [
                {"role": "system", "content": ctx[0]},
                {"role": "user", "content": item["input"]},
            ]
            for item, ctx in zip(batch, per_item_contexts, strict=True)
        ]

        # Call task LLM
        responses = self._call_task_model(messages_list)

        # Score each response
        for idx, (item, response_text) in enumerate(zip(batch, responses, strict=True)):
            try:
                eval_result = self.evaluator(item, response_text)
                score = eval_result["score"]
                feedback = eval_result["feedback"]
            except Exception as e:
                logger.log(f"Evaluator failed for item {idx}: {e}", header="error")
                score = self.failure_score
                feedback = f"Evaluation error: {e!s}"
                response_text = response_text if response_text else ""

            logger.debug(
                f"Item {idx}: input={item['input'][:80]!r} score={score:.3f} feedback={feedback[:120]!r}",
                header="score",
            )
            logger.debug(f"Item {idx} response: {response_text[:200]!r}", header="score")

            outputs.append({"full_assistant_response": response_text})
            scores.append(score)

            if capture_traces and trajectories is not None:
                trajectories.append(
                    {
                        "data": item,
                        "system_prompt": per_item_contexts[idx][0],
                        "memory_markdown": per_item_contexts[idx][1],
                        "full_assistant_response": response_text,
                        "score": score,
                        "feedback": feedback,
                    }
                )

        avg_score = sum(scores) / len(scores) if scores else 0.0
        logger.log(
            f"Batch scores: avg={avg_score:.3f} sum={sum(scores):.3f} [{', '.join(f'{s:.2f}' for s in scores)}]",
            header="score",
        )

        return EvaluationBatch(outputs=outputs, scores=scores, trajectories=trajectories)

    def make_reflective_dataset(
        self,
        candidate: dict[str, str],
        eval_batch: EvaluationBatch[MemoryTrajectory, MemoryOutput],
        components_to_update: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        """Build reflective dataset for memory refinement."""
        reflective_data: dict[str, list[dict[str, Any]]] = {}

        if "memory" not in components_to_update:
            logger.debug(f"Skipping reflective dataset: 'memory' not in {components_to_update}", header="reflect")
            return reflective_data

        num_trajectories = len(eval_batch.trajectories) if eval_batch.trajectories else 0
        logger.log(f"Building reflective dataset from {num_trajectories} trajectories", header="reflect")

        examples: list[dict[str, Any]] = []
        for traj, _output in zip(
            eval_batch.trajectories or [],
            eval_batch.outputs,
            strict=False,
        ):
            examples.append(
                {
                    "Inputs": {
                        "user_input": traj["data"]["input"],
                        "expected_answer": traj["data"]["answer"],
                    },
                    "Generated Outputs": traj["full_assistant_response"],
                    "Feedback": traj["feedback"],
                }
            )

        reflective_data["memory"] = examples

        for i, ex in enumerate(examples):
            logger.debug(
                f"Reflective example {i}: input={ex['Inputs']['user_input'][:60]!r} "
                f"score_feedback={ex['Feedback'][:100]!r}",
                header="reflect",
            )

        return reflective_data

    # ========================================================================
    # Custom proposal (replaces default InstructionProposalSignature)
    # ========================================================================

    def _propose_new_texts(
        self,
        candidate: dict[str, str],
        reflective_dataset: Mapping[str, Sequence[Mapping[str, Any]]],
        components_to_update: list[str],
    ) -> dict[str, str]:
        """Propose a new memory XML via edit-based mutation."""
        if "memory" not in components_to_update or "memory" not in reflective_dataset:
            logger.debug("Skipping proposal: 'memory' not in components or reflective dataset", header="propose")
            return {}

        memory_xml = candidate["memory"]
        feedback_examples = reflective_dataset["memory"]

        logger.log(
            f"Proposing edit from {len(feedback_examples)} feedback examples (max_retries={self.max_retries})",
            header="propose",
        )
        logger.debug(f"Current memory XML:\n{memory_xml}", header="propose")

        # Format feedback for the prompt
        formatted_feedback = self._format_feedback_examples(feedback_examples)

        # Build the proposal prompt
        prompt = self.EDIT_PROPOSAL_PROMPT.replace("<current_memory>", memory_xml)
        prompt = prompt.replace("<feedback_examples>", formatted_feedback)

        logger.debug(f"Full proposal prompt:\n{prompt}", header="prompt")

        last_error: Exception | None = None
        for attempt in range(1 + self.max_retries):
            try:
                logger.debug(f"Proposal attempt {attempt + 1}/{1 + self.max_retries}", header="propose")
                lm_output = self._call_reflection_model(prompt)

                logger.debug(f"Reflection LLM response:\n{lm_output}", header="propose")

                edit = self._parse_edit_response(lm_output)
                logger.debug(
                    f"Parsed edit: old_string={edit.old_string[:100]!r} new_string={edit.new_string[:100]!r}",
                    header="propose",
                )

                new_xml = apply_edit(memory_xml, edit)

                # Check entry count
                new_entries = parse_memory_xml(new_xml)
                if len(new_entries) > self.max_entries:
                    raise ValueError(
                        f"Edit would create {len(new_entries)} entries, exceeding max_entries={self.max_entries}"
                    )

                logger.log(
                    f"Edit accepted: {len(new_entries)} entries after edit",
                    header="propose",
                )
                logger.debug(f"New memory XML:\n{new_xml}", header="propose")

                return {"memory": new_xml}

            except Exception as e:
                last_error = e
                logger.log(f"Edit proposal attempt {attempt + 1} failed: {e}", header="error")

        raise RuntimeError(f"All {1 + self.max_retries} edit proposal attempts failed. Last error: {last_error}")

    # ========================================================================
    # Private helpers
    # ========================================================================

    def _build_batch_contexts(
        self,
        batch: list[MemoryDataInst],
        memory_xml: str,
    ) -> list[tuple[str, str]]:
        """Build (system_prompt, memory_markdown) for each item in the batch.

        Subclasses override this to implement per-item routing.
        Default: same context for all items.
        """
        system_prompt, memory_markdown = self._build_evaluation_context(memory_xml)
        return [(system_prompt, memory_markdown)] * len(batch)

    def _build_evaluation_context(self, memory_xml: str) -> tuple[str, str]:
        """Build the full system prompt by appending memory to the base prompt."""
        try:
            entries = parse_memory_xml(memory_xml)
        except ValueError:
            entries = []

        memory_markdown = format_memory_as_markdown(entries)
        return self._format_system_prompt(memory_markdown), memory_markdown

    def _format_system_prompt(self, memory_markdown: str) -> str:
        """Build the full system prompt by appending memory markdown to the base prompt."""
        if memory_markdown:
            return f"{self.base_system_prompt}\n\n# Knowledge Memory\n{memory_markdown}"
        return self.base_system_prompt

    def _call_task_model(self, messages_list: list[list[dict[str, str]]]) -> list[str]:
        """Call the task model for a batch of message lists."""
        responses: list[str] = []
        model_name = self.task_model if isinstance(self.task_model, str) else "callable"
        logger.debug(f"Calling task model ({model_name}) with {len(messages_list)} messages", header="llm")

        if isinstance(self.task_model, str):
            try:
                batch_responses = self._litellm.batch_completion(
                    model=self.task_model,
                    messages=messages_list,
                    num_retries=5,
                    caching=True,
                )
                for resp in batch_responses:
                    if isinstance(resp, Exception):
                        raise resp
                    content = resp.choices[0].message.content  # type: ignore[union-attr]
                    responses.append(content.strip() if content else "")
            except Exception as e:
                logger.log(f"Batch completion failed: {e}", header="error")
                responses = [f"ERROR: {e!s}"] * len(messages_list)
        else:
            for messages in messages_list:
                try:
                    result = self.task_model(messages)
                    responses.append(result if isinstance(result, str) else str(result))
                except Exception as e:
                    logger.log(f"Task model call failed: {e}", header="error")
                    responses.append(f"ERROR: {e!s}")

        logger.debug(f"Task model returned {len(responses)} responses", header="llm")
        return responses

    def _call_reflection_model(self, prompt: str) -> str:
        """Call the reflection model with a single prompt."""
        messages = [{"role": "user", "content": prompt}]
        model_name = self.reflection_model if isinstance(self.reflection_model, str) else "callable"
        logger.debug(f"Calling reflection model ({model_name})", header="llm")

        if isinstance(self.reflection_model, str):
            response = self._litellm.completion(
                model=self.reflection_model,
                messages=messages,
                num_retries=5,
            )
            content = response.choices[0].message.content  # type: ignore[union-attr]
            return content.strip() if content else ""
        else:
            result = self.reflection_model(messages)
            return result if isinstance(result, str) else str(result)

    @staticmethod
    def _parse_edit_response(lm_output: str) -> EditOperation:
        """Parse LLM output into an EditOperation."""
        text = lm_output.strip()

        # Try raw JSON first
        parsed = None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            pass

        # Fallback: extract JSON from code fences
        if parsed is None:
            fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
            if fence_match:
                try:
                    parsed = json.loads(fence_match.group(1).strip())
                except json.JSONDecodeError:
                    pass

        if parsed is None:
            raise ValueError(f"Could not parse JSON from LLM output: {text[:200]}")

        if "old_string" not in parsed or "new_string" not in parsed:
            raise ValueError(f"LLM output missing 'old_string' or 'new_string' keys: {parsed}")

        return EditOperation(old_string=parsed["old_string"], new_string=parsed["new_string"])

    @staticmethod
    def _format_feedback_examples(examples: Sequence[Mapping[str, Any]]) -> str:
        """Format reflective dataset examples into readable text for the proposal prompt."""
        parts = []
        for i, ex in enumerate(examples, 1):
            inputs = ex.get("Inputs", {})
            generated = ex.get("Generated Outputs", "")
            feedback = ex.get("Feedback", "")

            section = f"### Example {i}\n"
            section += f"**User Input:** {inputs.get('user_input', '')}\n"
            section += f"**Expected Answer:** {inputs.get('expected_answer', '')}\n"
            section += f"**Assistant Response:** {generated}\n"
            section += f"**Feedback:** {feedback}"
            parts.append(section)

        return "\n\n".join(parts)
