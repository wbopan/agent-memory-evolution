"""mstar KB-aware agent. Subclass of VanillaAgent that injects mstar KB output
as an additional system message before the task conversation begins.
"""

from __future__ import annotations

from typing import Any, Callable

from agents.base import AgentRuntimeContext
from agents.vanilla import VanillaAgent
from state_bench.client import LLMClient, PooledLLMClient


class MstarKBAgent(VanillaAgent):
    """Agent variant that injects a per-task KB context block.

    The KB context (`kb_text`) is computed by mstar before the task starts —
    typically via `kb.read(item)` against a compiled `KBProgram` — and passed
    in at construction. The agent prepends it as an extra system message via
    `prepare_conversation`, which STATE-Bench's orchestrator calls once at
    the top of each conversation.
    """

    def __init__(
        self,
        client: LLMClient | PooledLLMClient,
        system_prompt: str,
        tools: list[dict[str, Any]],
        tool_handlers: dict[str, Callable],
        runtime_context: AgentRuntimeContext | None = None,
        *,
        kb_text: str = "",
    ):
        super().__init__(
            client=client,
            system_prompt=system_prompt,
            tools=tools,
            tool_handlers=tool_handlers,
            runtime_context=runtime_context,
        )
        self._kb_text = kb_text or ""

    def prepare_conversation(self, conversation: list[Any]) -> list[Any]:
        if not self._kb_text:
            return conversation
        kb_msg = {
            "role": "system",
            "content": f"# Retrieved knowledge from past episodes\n\n{self._kb_text}",
        }
        # Insert AFTER the last contiguous leading system message so persona/policy stays first.
        idx = 0
        while idx < len(conversation) and isinstance(conversation[idx], dict) and conversation[idx].get("role") == "system":
            idx += 1
        return list(conversation[:idx]) + [kb_msg] + list(conversation[idx:])
