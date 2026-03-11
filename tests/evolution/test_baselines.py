from pathlib import Path

import pytest

from programmaticmemory.evolution.sandbox import CompileError, compile_kb_program, smoke_test
from programmaticmemory.evolution.toolkit import Toolkit, ToolkitConfig

BASELINES_DIR = Path(__file__).resolve().parents[2] / "baselines"


class TestBaselineSmokeTests:
    def test_no_memory_smoke(self):
        source = (BASELINES_DIR / "no_memory.py").read_text()
        result = smoke_test(source)
        assert result.success, f"no_memory.py smoke test failed: {result.error}"

    def test_vanilla_rag_smoke(self):
        source = (BASELINES_DIR / "vanilla_rag.py").read_text()
        result = smoke_test(source)
        assert result.success, f"vanilla_rag.py smoke test failed: {result.error}"


class TestBaselineBehavior:
    """Verify write/read behavior of each baseline using compile_kb_program."""

    def _make_kb(self, filename: str):
        """Compile a baseline and return (kb_instance, toolkit)."""
        source = (BASELINES_DIR / filename).read_text()
        result = compile_kb_program(source)
        assert not isinstance(result, CompileError), f"Compile failed: {result.message}"
        config = ToolkitConfig(llm_model="smoke-test/noop")
        toolkit = Toolkit(config)
        kb = result.kb_cls(toolkit)
        return kb, result.ki_cls, result.query_cls, toolkit

    def test_no_memory_returns_empty(self):
        kb, ki_cls, query_cls, toolkit = self._make_kb("no_memory.py")
        kb.write(ki_cls(summary="test"), raw_text="some text")
        result = kb.read(query_cls(raw="anything"))
        assert result == ""
        toolkit.close()

    @pytest.mark.uses_chroma
    def test_vanilla_rag_retrieves(self):
        kb, ki_cls, query_cls, toolkit = self._make_kb("vanilla_rag.py")
        kb.write(ki_cls(summary="Paris is the capital of France"), raw_text="Paris is the capital of France.")
        result = kb.read(query_cls(raw="What is the capital of France?"))
        assert "Paris" in result or "France" in result
        toolkit.close()
