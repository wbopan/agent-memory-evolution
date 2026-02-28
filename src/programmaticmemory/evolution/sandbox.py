"""Sandbox — compile, validate, and execute generated Memory Programs safely."""

from __future__ import annotations

import ast
import concurrent.futures
import dataclasses
import traceback
from dataclasses import dataclass, field
from typing import Any

import weave

from programmaticmemory.evolution.toolkit import ToolkitConfig, create_toolkit

ALLOWED_IMPORTS: set[str] = {
    "json",
    "re",
    "math",
    "hashlib",
    "collections",
    "dataclasses",
    "typing",
    "datetime",
    "textwrap",
    "sqlite3",
    "chromadb",
}


@dataclass
class CompileError:
    """Compilation failure info."""

    message: str
    details: str = ""


@dataclass
class SmokeTestResult:
    """Result of a smoke test run."""

    success: bool
    error: str = ""


@dataclass
class ExecutionResult:
    """Result of executing memory operations."""

    outputs: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    success: bool = True


class _ImportValidator(ast.NodeVisitor):
    """AST visitor that checks all imports are in the whitelist."""

    def __init__(self) -> None:
        self.violations: list[str] = []

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            top_module = alias.name.split(".")[0]
            if top_module not in ALLOWED_IMPORTS:
                self.violations.append(f"Disallowed import: {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.module:
            top_module = node.module.split(".")[0]
            if top_module not in ALLOWED_IMPORTS:
                self.violations.append(f"Disallowed import: {node.module}")
        self.generic_visit(node)


class _ClassFinder(ast.NodeVisitor):
    """AST visitor that finds class definitions by name."""

    def __init__(self) -> None:
        self.class_names: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.class_names.add(node.name)
        self.generic_visit(node)


def compile_memory_program(
    source_code: str,
) -> tuple[type, type, type] | CompileError:
    """Compile memory program source code and extract Observation, Query, Memory classes.

    Returns (Observation, Query, Memory) class tuple on success, CompileError on failure.
    """
    # 1. Parse
    try:
        tree = ast.parse(source_code)
    except SyntaxError as e:
        return CompileError(message="Syntax error", details=str(e))

    # 2. Check required classes exist
    finder = _ClassFinder()
    finder.visit(tree)
    required = {"Observation", "Query", "Memory"}
    missing = required - finder.class_names
    if missing:
        return CompileError(
            message=f"Missing required class(es): {', '.join(sorted(missing))}",
            details=f"Found classes: {', '.join(sorted(finder.class_names))}",
        )

    # 3. Check imports
    validator = _ImportValidator()
    validator.visit(tree)
    if validator.violations:
        return CompileError(
            message="Import whitelist violation",
            details="; ".join(validator.violations),
        )

    # 4. Execute in restricted namespace
    import collections
    import datetime
    import hashlib as _hashlib
    import json
    import math
    import re
    import textwrap

    import chromadb

    namespace: dict[str, Any] = {
        "__builtins__": __builtins__,
        "dataclasses": dataclasses,
        "dataclass": dataclasses.dataclass,
        "field": dataclasses.field,
    }
    # Pre-populate allowed modules so imports work
    allowed_modules = {
        "json": json,
        "re": re,
        "math": math,
        "hashlib": _hashlib,
        "collections": collections,
        "datetime": datetime,
        "textwrap": textwrap,
        "chromadb": chromadb,
        "typing": __import__("typing"),
        "sqlite3": __import__("sqlite3"),
    }
    namespace.update(allowed_modules)

    try:
        exec(source_code, namespace)
    except Exception:
        return CompileError(message="Execution error", details=traceback.format_exc())

    # 5. Extract classes
    obs_cls = namespace.get("Observation")
    query_cls = namespace.get("Query")
    memory_cls = namespace.get("Memory")

    if not all([obs_cls, query_cls, memory_cls]):
        return CompileError(
            message="Classes not found after execution",
            details="One or more of Observation, Query, Memory not in namespace after exec",
        )

    return (obs_cls, query_cls, memory_cls)


def _type_to_json_example(type_str: str) -> str:
    """Map a Python type annotation string to a JSON example value."""
    t = type_str.strip().lower()
    if t in ("str", "string"):
        return '"..."'
    if t in ("int", "integer"):
        return "0"
    if t in ("float", "number"):
        return "0.0"
    if t in ("bool", "boolean"):
        return "true"
    if "list" in t:
        return "[]"
    if "dict" in t:
        return "{}"
    if "optional" in t:
        return "null"
    return '"..."'


def extract_dataclass_schema(cls: type) -> str:
    """Extract a JSON schema description from a dataclass for LLM prompting.

    Returns a commented JSON example showing field names, types, and defaults.
    """
    if not dataclasses.is_dataclass(cls):
        return f"{cls.__name__} is not a dataclass. Construct it with: {cls.__name__}(raw=<string>)"

    lines: list[str] = []
    doc = cls.__doc__
    if doc:
        lines.append(f"// {cls.__name__}: {doc.strip()}")
    else:
        lines.append(f"// {cls.__name__}")
    lines.append("{")

    fields = dataclasses.fields(cls)
    for i, f in enumerate(fields):
        type_str = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
        example = _type_to_json_example(type_str)

        comment_parts = [type_str]
        if f.default is not dataclasses.MISSING:
            comment_parts.append(f"default: {f.default!r}")
        elif f.default_factory is not dataclasses.MISSING:
            comment_parts.append("optional")

        comma = "," if i < len(fields) - 1 else ""
        lines.append(f'  "{f.name}": {example}{comma}  // {", ".join(comment_parts)}')

    lines.append("}")
    return "\n".join(lines)


@weave.op()
def smoke_test(
    source_code: str,
    toolkit_config: ToolkitConfig | None = None,
    timeout: float = 10.0,
) -> SmokeTestResult:
    """Compile and run a basic write/read cycle to verify the program works."""

    def _run() -> SmokeTestResult:
        result = compile_memory_program(source_code)
        if isinstance(result, CompileError):
            return SmokeTestResult(success=False, error=f"Compile: {result.message} — {result.details}")

        obs_cls, query_cls, memory_cls = result
        toolkit = create_toolkit(toolkit_config)
        try:
            memory = memory_cls(toolkit)

            # Try a basic write
            if dataclasses.is_dataclass(obs_cls):
                obs_fields = dataclasses.fields(obs_cls)
                kwargs = {}
                for f in obs_fields:
                    if f.default is not dataclasses.MISSING:
                        continue
                    if f.default_factory is not dataclasses.MISSING:
                        continue
                    kwargs[f.name] = "smoke test value"
                obs = obs_cls(**kwargs)
            else:
                obs = obs_cls("smoke test value")
            memory.write(obs)

            # Try a basic read
            if dataclasses.is_dataclass(query_cls):
                query_fields = dataclasses.fields(query_cls)
                kwargs = {}
                for f in query_fields:
                    if f.default is not dataclasses.MISSING:
                        continue
                    if f.default_factory is not dataclasses.MISSING:
                        continue
                    kwargs[f.name] = "smoke test query"
                query = query_cls(**kwargs)
            else:
                query = query_cls("smoke test query")
            memory.read(query)

            return SmokeTestResult(success=True)
        except Exception as e:
            return SmokeTestResult(success=False, error=f"Runtime: {e}")
        finally:
            toolkit.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return SmokeTestResult(success=False, error=f"Smoke test timed out after {timeout}s")


def execute_memory_operations(
    memory: object,
    operations: list[tuple[str, object]],
    timeout: float = 30.0,
) -> ExecutionResult:
    """Execute a sequence of write/read operations on a memory instance.

    Each operation is ("write", observation) or ("read", query).
    Individual operation failures don't stop the sequence.
    """

    def _run() -> ExecutionResult:
        result = ExecutionResult()
        for op_type, arg in operations:
            try:
                if op_type == "write":
                    memory.write(arg)
                    result.outputs.append("")
                elif op_type == "read":
                    output = memory.read(arg)
                    result.outputs.append(str(output) if output is not None else "")
                else:
                    result.errors.append(f"Unknown operation: {op_type}")
                    result.outputs.append("")
                    result.success = False
            except Exception as e:
                result.errors.append(f"{op_type} error: {e}")
                result.outputs.append("")
                result.success = False
        return result

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(_run)
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            return ExecutionResult(
                outputs=[],
                errors=[f"Operations timed out after {timeout}s"],
                success=False,
            )
