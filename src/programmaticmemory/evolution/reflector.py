"""Reflector — LLM-driven reflection and code mutation for Knowledge Base Programs."""

from __future__ import annotations

import re

import litellm
import weave

from programmaticmemory.evolution.patcher import apply_patch
from programmaticmemory.evolution.prompts import (
    ReflectionPromptConfig,
    build_compile_fix_prompt,
    build_reflection_user_prompt,
)
from programmaticmemory.evolution.sandbox import CompileError, compile_kb_program, smoke_test
from programmaticmemory.evolution.toolkit import ToolkitConfig
from programmaticmemory.evolution.types import EvalResult, KBProgram
from programmaticmemory.logging.logger import get_logger


def _extract_patch(text: str) -> str | None:
    """Extract the last patch block from LLM output.

    Returns the patch body (everything between ``*** Begin Patch`` and
    ``*** End Patch`` markers, excluding the markers themselves), or None
    if no patch block is found.
    """
    matches = re.findall(r"\*\*\* Begin Patch\n(.*?)\*\*\* End Patch", text, re.DOTALL)
    if matches:
        return matches[-1]
    return None


class Reflector:
    """Reflects on evaluation results and mutates Knowledge Base Programs."""

    def __init__(
        self,
        model: str,
        max_fix_attempts: int = 3,
        toolkit_config: ToolkitConfig | None = None,
        prompt_config: ReflectionPromptConfig | None = None,
    ) -> None:
        self.model = model
        self.max_fix_attempts = max_fix_attempts
        self.toolkit_config = toolkit_config
        self.prompt_config = prompt_config
        self.logger = get_logger()

    def _validate_code(self, code: str) -> tuple[str, str] | None:
        """Compile and smoke-test code. Return (error_type, error_details) or None if valid."""
        result = compile_kb_program(code)
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
            max_tokens=16384,
            caching=True,
        )
        output = response.choices[0].message.content

        patch = _extract_patch(output)
        if patch is None:
            return None

        try:
            return apply_patch(code, patch)
        except RuntimeError:
            self.logger.log("Failed to apply patch from fix LLM", header="REFLECT")
            return None

    @weave.op()
    def reflect_and_mutate(
        self,
        current: KBProgram,
        eval_result: EvalResult,
        iteration: int,
    ) -> KBProgram | None:
        """Reflect on failures and produce a mutated Knowledge Base Program.

        Returns None if code extraction fails or compile-fix loop is exhausted.
        Returned KBProgram is guaranteed to pass compile + smoke_test.
        """
        # Build failed case dicts for the prompt
        failed_dicts = []
        for fc in eval_result.failed_cases:
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

        # Build success case dicts for the prompt
        success_dicts = []
        for sc in eval_result.success_cases:
            success_dicts.append(
                {
                    "question": sc.question,
                    "output": sc.output,
                    "expected": sc.expected,
                    "score": sc.score,
                    "conversation_history": sc.conversation_history,
                    "memory_logs": sc.memory_logs,
                }
            )

        user_prompt = build_reflection_user_prompt(
            code=current.source_code,
            score=eval_result.score,
            failed_cases=failed_dicts,
            iteration=iteration,
            train_examples=eval_result.train_examples or None,
            config=self.prompt_config,
            success_cases=success_dicts,
        )

        self.logger.log(f"Reflecting on iteration {iteration}, score={eval_result.score:.3f}", header="REFLECT")

        response = litellm.completion(
            model=self.model,
            messages=[
                {"role": "user", "content": user_prompt},
            ],
            max_tokens=16384,
            caching=True,
        )
        output = response.choices[0].message.content

        # Extract and apply patch
        patch = _extract_patch(output)
        if patch is None:
            self.logger.log("Failed to extract patch from reflection output", header="REFLECT")
            return None

        try:
            new_code = apply_patch(current.source_code, patch)
        except RuntimeError as exc:
            self.logger.log(f"Failed to apply patch: {exc}", header="REFLECT")
            return None

        # Validate and fix loop
        validation_error = self._validate_code(new_code)
        if validation_error is None:
            return KBProgram(
                source_code=new_code,
                generation=current.generation + 1,
                parent_hash=current.hash,
            )

        for attempt in range(1, self.max_fix_attempts + 1):
            error_type, error_details = validation_error
            self.logger.log(f"Fix attempt {attempt}/{self.max_fix_attempts}: {error_details}", header="REFLECT")
            fixed_code = self._try_fix(new_code, error_type, error_details)
            if fixed_code is None:
                self.logger.log(f"Fix attempt {attempt}: no patch in LLM response", header="REFLECT")
                continue

            new_code = fixed_code
            validation_error = self._validate_code(new_code)
            if validation_error is None:
                self.logger.log(f"Fix succeeded on attempt {attempt}", header="REFLECT")
                return KBProgram(
                    source_code=new_code,
                    generation=current.generation + 1,
                    parent_hash=current.hash,
                )

        self.logger.log(f"All {self.max_fix_attempts} fix attempts exhausted", header="REFLECT")
        return None

    def fix_runtime_violation(self, code: str, violation: str) -> str | None:
        """Fix a runtime violation. Returns validated (compile+smoke) code or None.

        Calls LLM to fix the violation, then validates. If the fix introduces
        compile/smoke errors, enters the compile-fix loop.
        """
        self.logger.log(f"Fixing runtime violation: {violation}", header="REFLECT")

        fixed = self._try_fix(code, "Runtime violation", violation)
        if fixed is None:
            self.logger.log("Runtime fix: no patch in LLM response", header="REFLECT")
            return None

        validation_error = self._validate_code(fixed)
        if validation_error is None:
            return fixed

        # Compile-fix loop for the fixed code
        for attempt in range(1, self.max_fix_attempts + 1):
            error_type, error_details = validation_error
            self.logger.log(
                f"Runtime fix compile-fix attempt {attempt}/{self.max_fix_attempts}: {error_details}",
                header="REFLECT",
            )
            fixed = self._try_fix(fixed, error_type, error_details)
            if fixed is None:
                continue
            validation_error = self._validate_code(fixed)
            if validation_error is None:
                return fixed

        self.logger.log("Runtime fix: all compile-fix attempts exhausted", header="REFLECT")
        return None
