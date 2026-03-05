"""Tests for evolution/patcher.py — apply_patch wrapper (deterministic, no LLM)."""

import pytest

from programmaticmemory.evolution.patcher import apply_patch

SOURCE = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = "Capture key facts."
INSTRUCTION_QUERY = "Ask about facts."
INSTRUCTION_RESPONSE = "Answer from memory."
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []

    def write(self, obs):
        self.store.append(obs.raw)

    def read(self, query):
        return " | ".join(self.store)
"""


class TestApplyPatch:
    def test_basic_line_replacement(self):
        patch_body = """\
*** Update File: program.py
@@
-    raw: str
+    text: str
+    category: str
"""
        result = apply_patch(SOURCE, patch_body)
        # First Observation.raw replaced
        assert "    text: str" in result
        assert "    category: str" in result

    def test_add_new_lines(self):
        patch_body = """\
*** Update File: program.py
@@
     def __init__(self, toolkit):
         self.store = []
+        self.index = {}
"""
        result = apply_patch(SOURCE, patch_body)
        assert "        self.index = {}" in result
        # Original lines preserved
        assert "        self.store = []" in result

    def test_delete_lines(self):
        patch_body = """\
*** Update File: program.py
@@
     def read(self, query):
-        return " | ".join(self.store)
+        return ""
"""
        result = apply_patch(SOURCE, patch_body)
        assert '        return ""' in result
        assert '" | ".join' not in result

    def test_multi_hunk_patch(self):
        patch_body = """\
*** Update File: program.py
@@
-INSTRUCTION_OBSERVATION = "Capture key facts."
+INSTRUCTION_OBSERVATION = "Extract entities and relationships."
@@
-INSTRUCTION_QUERY = "Ask about facts."
+INSTRUCTION_QUERY = "Query by entity name."
@@
-    def read(self, query):
-        return " | ".join(self.store)
+    def read(self, query):
+        return "\\n".join(self.store)
"""
        result = apply_patch(SOURCE, patch_body)
        assert 'INSTRUCTION_OBSERVATION = "Extract entities and relationships."' in result
        assert 'INSTRUCTION_QUERY = "Query by entity name."' in result
        assert '"\\n".join(self.store)' in result
        # Unchanged parts preserved
        assert "class KnowledgeBase:" in result
        assert "class Observation:" in result

    def test_invalid_patch_raises(self):
        patch_body = """\
*** Update File: program.py
@@
-this line does not exist in the source
+replacement
"""
        with pytest.raises(RuntimeError):
            apply_patch(SOURCE, patch_body)

    def test_empty_source_with_add(self):
        """Applying a patch that adds content to an empty file."""
        patch_body = """\
*** Update File: program.py
@@
+# new content
"""
        result = apply_patch("", patch_body)
        assert "# new content" in result

    def test_preserves_trailing_newline(self):
        """Patching should preserve the overall structure of the source."""
        patch_body = """\
*** Update File: program.py
@@
-INSTRUCTION_RESPONSE = "Answer from memory."
+INSTRUCTION_RESPONSE = "Answer concisely."
"""
        result = apply_patch(SOURCE, patch_body)
        assert 'INSTRUCTION_RESPONSE = "Answer concisely."' in result
        # Other instructions unchanged
        assert 'INSTRUCTION_OBSERVATION = "Capture key facts."' in result
