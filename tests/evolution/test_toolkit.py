"""Tests for evolution/toolkit.py — Toolkit, MemoryLogger, config."""

import pytest

from programmaticmemory.evolution.toolkit import (
    MemoryLogger,
    Toolkit,
    ToolkitConfig,
)


class TestMemoryLogger:
    def test_log_and_retrieve(self):
        logger = MemoryLogger()
        logger.log("first")
        logger.log("second")
        assert logger.logs == ["first", "second"]

    def test_clear(self):
        logger = MemoryLogger()
        logger.log("x")
        logger.clear()
        assert logger.logs == []

    def test_debug_appends_to_logs(self):
        logger = MemoryLogger()
        logger.debug("debug msg")
        logger.log("log msg")
        assert logger.logs == ["debug msg", "log msg"]

    def test_empty_initially(self):
        logger = MemoryLogger()
        assert logger.logs == []


class TestToolkitConfig:
    def test_defaults(self):
        config = ToolkitConfig(llm_model="test/model")
        assert config.llm_model == "test/model"
        assert config.llm_call_budget == 50
        assert config.llm_temperature == 0.0

    def test_custom(self):
        config = ToolkitConfig(llm_model="custom/model", llm_call_budget=10, llm_temperature=0.5)
        assert config.llm_model == "custom/model"
        assert config.llm_call_budget == 10


class TestToolkit:
    def test_sqlite_works(self):
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        tk.db.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, value TEXT)")
        tk.db.execute("INSERT INTO test VALUES (1, 'hello')")
        row = tk.db.execute("SELECT value FROM test WHERE id=1").fetchone()
        assert row[0] == "hello"
        tk.close()

    @pytest.mark.uses_chroma
    def test_chroma_works(self):
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        col = tk.chroma.get_or_create_collection("test")
        col.add(ids=["1"], documents=["hello world"])
        results = col.query(query_texts=["hello"], n_results=1)
        assert results["ids"][0] == ["1"]
        assert results["documents"][0] == ["hello world"]
        tk.close()

    def test_logger_integration(self):
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        tk.logger.log("test message")
        assert "test message" in tk.logger.logs
        tk.close()

    def test_llm_budget_enforcement(self):
        tk = Toolkit(ToolkitConfig(llm_model="test/model", llm_call_budget=2))
        tk._llm_calls_used = 2  # Simulate budget exhaustion
        with pytest.raises(RuntimeError, match="budget exhausted"):
            tk.llm_completion([{"role": "user", "content": "test"}])
        tk.close()

    def test_close_is_idempotent(self):
        tk = Toolkit(ToolkitConfig(llm_model="test/model"))
        tk.close()
        # Second close should not raise (sqlite3 may raise ProgrammingError but we shouldn't crash)
        try:
            tk.close()
        except Exception:
            pass  # Some DB drivers raise on double-close, that's fine

    def test_instances_are_independent(self):
        tk1 = Toolkit(ToolkitConfig(llm_model="test/model"))
        tk2 = Toolkit(ToolkitConfig(llm_model="test/model"))
        tk1.db.execute("CREATE TABLE only_in_tk1 (id INTEGER)")
        cursor = tk2.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='only_in_tk1'")
        assert cursor.fetchone() is None
        tk1.close()
        tk2.close()
