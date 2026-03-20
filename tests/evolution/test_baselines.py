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


_ALMA_BASELINES_DIR = Path(__file__).resolve().parents[2] / "src" / "programmaticmemory" / "baselines"


class TestTrajectoryRetrieval:
    @pytest.mark.uses_chroma
    def test_write_and_read(self):
        source = (_ALMA_BASELINES_DIR / "trajectory_retrieval.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError), f"Compile failed: {compiled}"

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=0))
        kb = compiled.kb_cls(toolkit)

        # Write two trajectories
        item1 = compiled.ki_cls(summary="cooking task")
        kb.write(item1, raw_text="Task: heat potato. > go to fridge 1 > take potato > go to microwave > heat potato")

        item2 = compiled.ki_cls(summary="cleaning task")
        kb.write(item2, raw_text="Task: clean mug. > go to sink 1 > take mug > clean mug > put mug on counter")

        # Read with a cooking-related query
        query = compiled.query_cls(query_text="how to heat something")
        result = kb.read(query)
        assert "potato" in result or "heat" in result
        assert len(result) <= 3000

    @pytest.mark.uses_chroma
    def test_read_empty(self):
        source = (_ALMA_BASELINES_DIR / "trajectory_retrieval.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError)

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=0))
        kb = compiled.kb_cls(toolkit)

        query = compiled.query_cls(query_text="anything")
        result = kb.read(query)
        assert isinstance(result, str)


class TestReasoningBank:
    @pytest.mark.uses_chroma
    def test_compile_and_smoke(self):
        source = (_ALMA_BASELINES_DIR / "reasoning_bank.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError), f"Compile failed: {compiled}"

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=10))
        kb = compiled.kb_cls(toolkit)

        # Verify interface exists
        assert hasattr(kb, "write")
        assert hasattr(kb, "read")

    @pytest.mark.uses_chroma
    def test_read_empty(self):
        source = (_ALMA_BASELINES_DIR / "reasoning_bank.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError)

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=0))
        kb = compiled.kb_cls(toolkit)

        query = compiled.query_cls(query_text="anything")
        result = kb.read(query)
        assert isinstance(result, str)


class TestDynamicCheatsheet:
    def test_compile_and_smoke(self):
        source = (_ALMA_BASELINES_DIR / "dynamic_cheatsheet.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError), f"Compile failed: {compiled}"

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=10))
        kb = compiled.kb_cls(toolkit)
        assert hasattr(kb, "write")
        assert hasattr(kb, "read")

    def test_read_empty_returns_empty(self):
        source = (_ALMA_BASELINES_DIR / "dynamic_cheatsheet.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError)

        toolkit = Toolkit(ToolkitConfig(llm_model="test", llm_call_budget=0))
        kb = compiled.kb_cls(toolkit)

        query = compiled.query_cls(query_text="anything")
        result = kb.read(query)
        assert isinstance(result, str)


class TestGMemory:
    @pytest.mark.uses_chroma
    def test_compile_and_smoke(self):
        source = (_ALMA_BASELINES_DIR / "g_memory.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError), f"Compile failed: {compiled}"

        toolkit = Toolkit(ToolkitConfig(llm_model="smoke-test/noop", llm_call_budget=20))
        kb = compiled.kb_cls(toolkit)
        assert hasattr(kb, "write")
        assert hasattr(kb, "read")
        toolkit.close()

    @pytest.mark.uses_chroma
    def test_read_empty(self):
        source = (_ALMA_BASELINES_DIR / "g_memory.py").read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError)

        toolkit = Toolkit(ToolkitConfig(llm_model="smoke-test/noop", llm_call_budget=0))
        kb = compiled.kb_cls(toolkit)

        query = compiled.query_cls(query_text="anything")
        result = kb.read(query)
        assert isinstance(result, str)
        toolkit.close()


ALMA_BASELINES = [
    "trajectory_retrieval.py",
    "reasoning_bank.py",
    "dynamic_cheatsheet.py",
    "g_memory.py",
]


class TestAllAlmaBaselinesCompile:
    @pytest.mark.parametrize("filename", ALMA_BASELINES)
    @pytest.mark.uses_chroma
    def test_compile_and_smoke(self, filename):
        source = (_ALMA_BASELINES_DIR / filename).read_text()
        compiled = compile_kb_program(source)
        assert not isinstance(compiled, CompileError), f"{filename} compile failed: {compiled}"

        result = smoke_test(source)
        assert result.success, f"{filename} smoke test failed: {result.error}"
