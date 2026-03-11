# tests/evolution/test_baselines.py
from pathlib import Path

from programmaticmemory.evolution.sandbox import smoke_test

BASELINES_DIR = Path(__file__).resolve().parents[2] / "baselines"


class TestBaselineSmokeTests:
    def test_no_memory_smoke(self):
        source = (BASELINES_DIR / "no_memory.py").read_text()
        result = smoke_test(source)
        assert result.success, f"no_memory.py smoke test failed: {result.error}"
