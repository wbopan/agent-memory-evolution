"""Programmatic memory evolution system — GEPA cycle for Knowledge Base Program optimization."""

from programmaticmemory.evolution.evaluator import (
    ExactMatchScorer,
    LLMJudgeScorer,
    MemoryEvaluator,
)
from programmaticmemory.evolution.loop import EvolutionLoop
from programmaticmemory.evolution.prompts import INITIAL_KB_PROGRAM
from programmaticmemory.evolution.reflector import Reflector
from programmaticmemory.evolution.sandbox import (
    CompileError,
    compile_kb_program,
    extract_dataclass_schema,
    smoke_test,
)
from programmaticmemory.evolution.toolkit import (
    MemoryLogger,
    Toolkit,
    ToolkitConfig,
)
from programmaticmemory.evolution.types import (
    DataItem,
    EvalResult,
    EvolutionRecord,
    EvolutionState,
    FailedCase,
    KBProgram,
    MaxSelection,
    PoolEntry,
    ProgramPool,
    RecencyDecaySelection,
    SelectionStrategy,
    SoftmaxSelection,
)
