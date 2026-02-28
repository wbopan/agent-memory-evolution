"""Reflector — LLM-driven reflection and code mutation for Memory Programs."""

from __future__ import annotations

import re

import litellm
import weave

from programmaticmemory.evolution.prompts import (
    build_compile_fix_prompt,
    build_reflection_user_prompt,
)
from programmaticmemory.evolution.sandbox import CompileError, compile_memory_program, smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
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
        model: str = "openrouter/deepseek/deepseek-v3.2",
        temperature: float = 0.7,
        max_fix_attempts: int = 3,
        toolkit_config: ToolkitConfig | None = None,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_fix_attempts = max_fix_attempts
        self.toolkit_config = toolkit_config
        self.logger = get_logger()

    def _validate_code(self, code: str) -> tuple[str, str] | None:
        """Compile and smoke-test code. Return (error_type, error_details) or None if valid."""
        result = compile_memory_program(code)
        if isinstance(result, CompileError):
            return (result.message, result.details)

        st = smoke_test(code, self.toolkit_config)
        if not st.success:
            return ("Smoke test error", st.error)

        return None

    def _try_fix(self, code: str, error_type: str, error_details: str) -> str | None:
        """Ask LLM to fix broken code. Return fixed code or None."""
        user_prompt = build_compile_fix_prompt(code=code, error_type=error_type, error_details=error_details)

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            caching=True,
        )
        output = response.choices[0].message.content
        return _extract_code_block(output)

    @weave.op()
    def reflect_and_mutate(
        self,
        current: MemoryProgram,
        eval_result: EvalResult,
        iteration: int,
    ) -> MemoryProgram | None:
        """Reflect on failures and produce a mutated Memory Program.

        Returns None if code extraction fails or compile-fix loop is exhausted.
        Returned MemoryProgram is guaranteed to pass compile + smoke_test.
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
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.temperature,
            caching=True,
        )
        output = response.choices[0].message.content

        # Extract code
        new_code = _extract_code_block(output)
        if new_code is None:
            self.logger.log("Failed to extract code block from reflection output", header="REFLECT")
            return None

        # Validate and fix loop
        validation_error = self._validate_code(new_code)
        if validation_error is None:
            return MemoryProgram(
                source_code=new_code,
                generation=current.generation + 1,
                parent_hash=current.hash,
            )

        for attempt in range(1, self.max_fix_attempts + 1):
            error_type, error_details = validation_error
            self.logger.log(f"Fix attempt {attempt}/{self.max_fix_attempts}: {error_details}", header="REFLECT")
            fixed_code = self._try_fix(new_code, error_type, error_details)
            if fixed_code is None:
                self.logger.log(f"Fix attempt {attempt}: no code block in LLM response", header="REFLECT")
                continue

            new_code = fixed_code
            validation_error = self._validate_code(new_code)
            if validation_error is None:
                self.logger.log(f"Fix succeeded on attempt {attempt}", header="REFLECT")
                return MemoryProgram(
                    source_code=new_code,
                    generation=current.generation + 1,
                    parent_hash=current.hash,
                )

        self.logger.log(f"All {self.max_fix_attempts} fix attempts exhausted", header="REFLECT")
        return None
