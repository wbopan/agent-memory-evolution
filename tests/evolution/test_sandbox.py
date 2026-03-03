"""Tests for evolution/sandbox.py — compile, schema, smoke test."""

from programmaticmemory.evolution.sandbox import (
    CompiledProgram,
    CompileError,
    compile_kb_program,
    extract_dataclass_schema,
    smoke_test,
)

VALID_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.store = []

    def write(self, obs):
        self.store.append(obs.raw)

    def read(self, query):
        return " | ".join(self.store)
"""

SYNTAX_ERROR_PROGRAM = """\
def foo(
    # missing closing paren
class Observation:
    pass
"""

MISSING_CLASS_PROGRAM = """\
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""

DISALLOWED_IMPORT_PROGRAM = """\
import os
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""

RUNTIME_ERROR_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        raise ValueError("init error")

    def write(self, obs): pass
    def read(self, query): return ""
"""

READ_ERROR_PROGRAM = """\
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

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
        return 1 / 0  # ZeroDivisionError
"""


class TestCompileKBProgram:
    def test_valid_program(self):
        result = compile_kb_program(VALID_PROGRAM)
        assert isinstance(result, CompiledProgram)
        assert result.obs_cls.__name__ == "Observation"
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

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.data = defaultdict(list)
    def write(self, obs):
        key = hashlib.md5(obs.raw.encode()).hexdigest()[:8]
        self.data[key].append(obs.raw)
    def read(self, query):
        return json.dumps(dict(self.data))
"""
        result = compile_kb_program(code)
        assert not isinstance(result, CompileError)

    def test_chromadb_import_allowed(self):
        code = """\
import chromadb
from dataclasses import dataclass

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.col = toolkit.chroma.get_or_create_collection("mem")
    def write(self, obs):
        self.col.add(ids=[str(id(obs))], documents=[obs.raw])
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

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompileError)
        assert "Execution error" in result.message

    def test_missing_constants(self):
        code = """\
from dataclasses import dataclass

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompileError)
        assert "Missing required constant" in result.message


class TestExtractDataclassSchema:
    def test_simple_dataclass(self):
        result = compile_kb_program(VALID_PROGRAM)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.obs_cls)
        assert "Observation" in schema
        assert '"raw"' in schema
        assert "str" in schema
        assert "{" in schema  # JSON object

    def test_multi_field_dataclass(self):
        code = """\
from dataclasses import dataclass, field
from typing import Any

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    text: str
    category: str = "general"
    priority: int = 0

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.obs_cls)
        assert '"text"' in schema
        assert '"category"' in schema
        assert '"priority"' in schema
        assert "general" in schema  # default value shown

    def test_field_description_in_metadata(self):
        code = """\
from dataclasses import dataclass, field

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    text: str = field(metadata={"description": "The main content to store"})
    tag: str = field(default="misc", metadata={"description": "Category tag"})

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit): pass
    def write(self, obs): pass
    def read(self, query): return ""
"""
        result = compile_kb_program(code)
        assert isinstance(result, CompiledProgram)
        schema = extract_dataclass_schema(result.obs_cls)
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

INSTRUCTION_OBSERVATION = ""
INSTRUCTION_QUERY = ""
INSTRUCTION_RESPONSE = ""

@dataclass
class Observation:
    raw: str

@dataclass
class Query:
    raw: str

class KnowledgeBase:
    def __init__(self, toolkit):
        start = datetime.now()
        while (datetime.now() - start).total_seconds() < 0.5:
            pass  # Busy-wait
    def write(self, obs): pass
    def read(self, query): return ""
"""
        result = smoke_test(code, timeout=0.1)
        assert result.success is False
        assert "timed out" in result.error
