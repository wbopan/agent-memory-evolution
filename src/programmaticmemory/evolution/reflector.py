"""Reflector — LLM-driven reflection and code mutation for Memory Programs."""

from __future__ import annotations

import re

import litellm
import weave

from programmaticmemory.evolution.prompts import (
    MEMORY_INTERFACE_SPEC,
    REFLECTION_SYSTEM_PROMPT,
    build_reflection_user_prompt,
)
from programmaticmemory.evolution.types import EvalResult, MemoryProgram
from programmaticmemory.logging.logger import get_logger


def _extract_code_block(text: str) -> str | None:
    """Extract the last Python code block from LLM output."""
    matches = re.findall(r"```python\s*\n(.*?)```", text, re.DOTALL)
    if matches:
        return matches[-1].strip()
    return None


class Reflector:
    """Reflects on evaluation results and mutates Memory Programs."""

    def __init__(
        self,
        model: str = "openai/gpt-4o",
        temperature: float = 0.7,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.logger = get_logger()

    @weave.op()
    def reflect_and_mutate(
        self,
        current: MemoryProgram,
        eval_result: EvalResult,
        iteration: int,
    ) -> MemoryProgram | None:
        """Reflect on failures and produce a mutated Memory Program.

        Returns None if code extraction fails.
        """
        # Build failed case dicts for the prompt
        failed_dicts = []
        for fc in eval_result.failed_cases[:5]:
            failed_dicts.append(
                {
                    "question": fc.question,
                    "output": fc.output,
                    "expected": fc.expected,
                    "score": fc.score,
                    "conversation_history": fc.conversation_history,
                    "memory_logs": fc.memory_logs,
                }
            )

        system_prompt = REFLECTION_SYSTEM_PROMPT.format(interface_spec=MEMORY_INTERFACE_SPEC)
        user_prompt = build_reflection_user_prompt(
            code=current.source_code,
            score=eval_result.score,
            failed_cases=failed_dicts,
            iteration=iteration,
        )

        self.logger.log(f"Reflecting on iteration {iteration}, score={eval_result.score:.3f}", header="REFLECT")

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
        )
        output = response.choices[0].message.content

        # Extract code
        new_code = _extract_code_block(output)
        if new_code is None:
            self.logger.log("Failed to extract code block from reflection output", header="REFLECT")
            return None

        return MemoryProgram(
            source_code=new_code,
            generation=current.generation + 1,
            parent_hash=current.hash,
        )
