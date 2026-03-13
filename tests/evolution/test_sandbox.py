"""Tests for evolution/sandbox.py — compile, schema, smoke test."""

from programmaticmemory.evolution.sandbox import (
    CompiledProgram,
    CompileError,
    compile_kb_program,
    extract_dataclass_schema,
    freeze_instruction_constants,
    smoke_test,
)

VALID_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []

    def write(self, item, raw_text=""):
        self.store.append(item.raw)

    def read(self, query):
        return " | ".join(self.store)
"""

SYNTAX_ERROR_PROGRAM = """\
def foo(
    # missing closing paren
class KnowledgeItem:
    pass
"""

MISSING_CLASS_PROGRAM = """\
from dataclasses import dataclass

@dataclass
class KnowledgeItem:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""

DISALLOWED_IMPORT_PROGRAM = """\
import os
from dataclasses import dataclass

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""

RUNTIME_ERROR_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        raise ValueError("init error")

    def write(self, item): pass
    def read(self, query): return ""
"""

READ_ERROR_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []

    def write(self, item):
        self.store.append(item.raw)

    def read(self, query):
        return 1 / 0  # ZeroDivisionError
"""


class TestCompileKBProgram:
    def test_valid_program(self):
        result = compile_kb_program(VALID_PROGRAM)
        assert isinstance(result, CompiledProgram)
        assert result.ki_cls.__name__ == "KnowledgeItem"
        assert result.query_cls.__name__ == "Query"
        assert result.kb_cls.__name__ == "KnowledgeBase"

    def test_syntax_error(self):
        result = compile_kb_program(SYNTAX_ERROR_PROGRAM)
        assert isinstance(result, CompileError)
        assert "Syntax error" in result.message

    def test_missing_class(self):
        result = compile_kb_program(MISSING_CLASS_PROGRAM)
        assert isinstance(result, CompileError)
        assert "Missing required class" in result.message
        assert "Query" in result.message

    def test_disallowed_import(self):
        result = compile_kb_program(DISALLOWED_IMPORT_PROGRAM)
        assert isinstance(result, CompileError)
        assert "Import whitelist" in result.message
        assert "os" in result.details

    def test_allowed_imports(self):
        code = """\
import json
import re
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.data = defaultdict(list)
    def write(self, item):
        key = hashlib.md5(item.raw.encode()).hexdigest()[:8]
        self.data[key].append(item.raw)
    def read(self, query):
        return json.dumps(dict(self.data))
"""
        result = compile_kb_program(code)
        assert not isinstance(result, CompileError)

    def test_chromadb_import_allowed(self):
        code = """\
import chromadb
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.col = toolkit.chroma.get_or_create_collection("mem")
    def write(self, item):
        self.col.add(ids=[str(id(item))], documents=[item.raw])
    def read(self, query):
        results = self.col.query(query_texts=[query.raw], n_results=3)
        return str(results["documents"])
"""
        result = compile_kb_program(code)
        assert not isinstance(result, CompileError)

    def test_runtime_execution_error(self):
        code = """\
from dataclasses import dataclass
x = 1 / 0  # RuntimeError during exec

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompileError)
        assert "Execution error" in result.message

    def test_missing_constants(self):
        code = """\
from dataclasses import dataclass

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompileError)
        assert "Missing required constant" in result.message


class TestExtractDataclassSchema:
    def test_simple_dataclass(self):
        result = compile_kb_program(VALID_PROGRAM)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.ki_cls)
        assert "KnowledgeItem" in schema
        assert '"raw"' in schema
        assert "str" in schema
        assert "{" in schema  # JSON object

    def test_multi_field_dataclass(self):
        code = """\
from dataclasses import dataclass, field
from typing import Any

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str
    category: str = "general"
    priority: int = 0

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.ki_cls)
        assert '"text"' in schema
        assert '"category"' in schema
        assert '"priority"' in schema
        assert "general" in schema  # default value shown

    def test_field_description_in_metadata(self):
        code = """\
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "The main content to store"})
    tag: str = field(default="misc", metadata={"description": "Category tag"})

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.ki_cls)
        assert "The main content to store" in schema
        assert "Category tag" in schema

    def test_non_dataclass(self):
        result = compile_kb_program(VALID_PROGRAM)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.kb_cls)
        assert "not a dataclass" in schema


class TestSmokeTest:
    def test_valid_program_passes(self):
        result = smoke_test(VALID_PROGRAM)
        assert result.success is True
        assert result.error == ""

    def test_syntax_error_fails(self):
        result = smoke_test(SYNTAX_ERROR_PROGRAM)
        assert result.success is False
        assert "Compile" in result.error

    def test_runtime_error_in_init_fails(self):
        result = smoke_test(RUNTIME_ERROR_PROGRAM)
        assert result.success is False
        assert "Runtime" in result.error

    def test_timeout(self):
        code = """\
from dataclasses import dataclass
from datetime import datetime

INSTRUCTION_KNOWLEDGE_ITEM = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 0.5:
            pass  # Busy-wait
    def write(self, item): pass
    def read(self, query): return ""
"""
        result = smoke_test(code, timeout=0.1)
        assert result.success is False
        assert "timed out" in result.error


class TestAlwaysOnKnowledge:
    """Tests for the ALWAYS_ON_KNOWLEDGE constant in compile_kb_program."""

    PROGRAM_WITH_ALWAYS_ON = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "observe everything"
INSTRUCTION_QUERY = "query everything"
INSTRUCTION_RESPONSE = "respond with everything"
ALWAYS_ON_KNOWLEDGE = "The user prefers concise answers."

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []
    def write(self, item):
        self.store.append(item.raw)
    def read(self, query):
        return " | ".join(self.store)
"""

    PROGRAM_MISSING_ALWAYS_ON = """\
from dataclasses import dataclass

INSTRUCTION_KNOWLEDGE_ITEM = "observe everything"
INSTRUCTION_QUERY = "query everything"
INSTRUCTION_RESPONSE = "respond with everything"

@dataclass
class KnowledgeItem:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.store = []
    def write(self, item):
        self.store.append(item.raw)
    def read(self, query):
        return " | ".join(self.store)
"""

    def test_compile_extracts_always_on_knowledge(self):
        """compile_kb_program returns CompiledProgram with always_on_knowledge field."""
        result = compile_kb_program(self.PROGRAM_WITH_ALWAYS_ON)
        assert isinstance(result, CompiledProgram)
        assert result.always_on_knowledge == "The user prefers concise answers."

    def test_compile_fails_when_always_on_knowledge_missing(self):
        """compile_kb_program returns CompileError when ALWAYS_ON_KNOWLEDGE is missing."""
        result = compile_kb_program(self.PROGRAM_MISSING_ALWAYS_ON)
        assert isinstance(result, CompileError)
        assert "ALWAYS_ON_KNOWLEDGE" in result.message


class TestFreezeInstructionConstants:
    def test_replaces_all_four_constants(self):
        parent_source = """
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = "Parent KI instruction"
INSTRUCTION_QUERY = "Parent query instruction"
INSTRUCTION_RESPONSE = "Parent response instruction"
ALWAYS_ON_KNOWLEDGE = "Parent always-on"

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
"""
        child_source = parent_source.replace("Parent KI instruction", "Child KI instruction")
        child_source = child_source.replace("Parent query instruction", "Child query instruction")
        child_source = child_source.replace("Parent response instruction", "Child response instruction")
        child_source = child_source.replace("Parent always-on", "Child always-on")

        frozen = freeze_instruction_constants(parent_source, child_source)

        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
        assert frozen_compiled.instruction_query == parent_compiled.instruction_query
        assert frozen_compiled.instruction_response == parent_compiled.instruction_response
        assert frozen_compiled.always_on_knowledge == parent_compiled.always_on_knowledge

    def test_preserves_non_constant_code(self):
        parent_source = """
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = "Parent KI"
INSTRUCTION_QUERY = "Parent Q"
INSTRUCTION_RESPONSE = "Parent R"
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
"""
        child_source = (
            parent_source.replace("Parent KI", "Child KI")
            .replace("Parent Q", "Child Q")
            .replace("Parent R", "Child R")
            .replace(
                'def read(self, query): return ""',
                'def read(self, query): return "child logic"',
            )
        )

        frozen = freeze_instruction_constants(parent_source, child_source)
        assert 'return "child logic"' in frozen
        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item

    def test_handles_triple_quoted_constants(self):
        parent_source = '''
from dataclasses import dataclass, field
INSTRUCTION_KNOWLEDGE_ITEM = """Parent
multiline KI"""
INSTRUCTION_QUERY = "Parent Q"
INSTRUCTION_RESPONSE = "Parent R"
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    text: str = field(metadata={"description": "text"})

@dataclass
class Query:
    text: str = field(metadata={"description": "text"})

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, item, raw_text=""): pass
    def read(self, query): return ""
'''
        child_source = parent_source.replace("Parent\nmultiline KI", "Child KI changed")

        frozen = freeze_instruction_constants(parent_source, child_source)

        parent_compiled = compile_kb_program(parent_source)
        frozen_compiled = compile_kb_program(frozen)
        assert frozen_compiled.instruction_knowledge_item == parent_compiled.instruction_knowledge_item
