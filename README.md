<div align="center">

<!-- Replace with actual icon once generated -->
<!-- <img src="assets/icon.png" width="120" alt="agent-memory-evolution icon"> -->

# agent-memory-evolution

**Evolve task-specific memory systems for LLM agents as executable Python code.**

[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-632%20passing-brightgreen.svg)](#quick-start)
[![arXiv](https://img.shields.io/badge/arXiv-coming%20soon-b31b1b.svg)](#citation)

</div>

LLM agents need memory to store what they have seen and retrieve it when needed.
Everyone hand-designs these systems: pick a vector store, write some retrieval logic, tune the prompts, ship it.
This project automates the design process.
You provide a benchmark; it evolves a task-specific memory system as executable Python code.
The memory it discovers for conversational QA looks nothing like what it discovers for a household robot.
Both emerge from the same three simple seeds.

<p align="center">
  <img src="assets/system-overview.png" width="800" alt="System overview">
</p>

## The idea

A memory system is a Python file: dataclasses that define what to store and how to query, `write()` and `read()` methods, and a few instruction strings that tell the task agent how to use the memory.
We call this a Knowledge Base Program.
The evolution loop evaluates a program on a benchmark, asks an LLM to reflect on failures, generates a mutated version, and repeats.
After 20 iterations over a population of diverse candidates, the best program wins.

The task agent that *uses* the memory is completely fixed.
Only the memory program changes.
If the score improves, the memory got better.

## What evolution finds

A seed program starts at roughly 30 lines. After 20 iterations, the system discovers structurally different solutions for different tasks:

<table>
<tr>
<td align="center"><b>Seed</b><br>(30 lines)</td>
<td align="center"><b>LoCoMo (Conversational QA)</b><br>(200+ lines)</td>
<td align="center"><b>ALFWorld (Embodied Tasks)</b><br>(200+ lines)</td>
</tr>
<tr>
<td><img src="assets/minimap-seed.png" width="200" alt="Seed program"></td>
<td><img src="assets/minimap-locomo.png" width="200" alt="Evolved LoCoMo program"></td>
<td><img src="assets/minimap-alfworld.png" width="200" alt="Evolved ALFWorld program"></td>
</tr>
<tr>
<td>Dump everything,<br>retrieve everything</td>
<td>Multi-table SQL, temporal decay,<br>entity indexing, semantic fusion</td>
<td>Deterministic action cache,<br>zero LLM calls at retrieval</td>
</tr>
</table>

## How it works

```
seeds/                  3 starting programs (vector search, LLM summarizer, experience learner)
src/.../evolution/      evaluate → reflect → mutate → repeat
src/.../benchmarks/     LoCoMo, ALFWorld, WebArena, NYT Connections, AgentBoard
```

Each iteration: sample a parent from the pool (softmax on scores), reflect on its failures, produce a mutated child, evaluate, add to pool.
The toolkit available to evolved programs includes SQLite, ChromaDB, and a budget-limited LLM (50 calls per evaluation).

## Quick start

Python 3.12+, [uv](https://docs.astral.sh/uv/), an API key (e.g. `OPENROUTER_API_KEY`).

```bash
git clone https://github.com/wbopan/agent-memory-evolution.git
cd agent-memory-evolution
uv pip install -e ".[dev]"

# run tests (no API key needed)
uv run pytest tests/evolution/ -m "not llm" -v

# evolve a memory system (needs API key)
uv run python -m programmaticmemory.evolution \
  --dataset mini_locomo --iterations 3 --no-weave

# evaluate a single program without evolution
uv run python -m programmaticmemory.evolution \
  --dataset mini_locomo --seed-program seeds/vector_search.py \
  --iterations 0 --eval-strategy none --no-weave
```

For ALFWorld: `uv pip install -e ".[alfworld]"` then `--dataset alfworld`.
For WebArena: `uv pip install -e ".[webarena]"` then `playwright install chromium`.

## What a memory program looks like

```python
INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Formulate a query to search for relevant information."
INSTRUCTION_RESPONSE = "Based on the knowledge, provide a short answer."
ALWAYS_ON_KNOWLEDGE = ""

@dataclass
class KnowledgeItem:
    summary: str

@dataclass
class Query:
    query_text: str

class KnowledgeBase:
    def __init__(self, toolkit):    # toolkit = SQLite + ChromaDB + LLM
        self.collection = toolkit.chroma.get_or_create_collection("kb")

    def write(self, item, raw_text=""):
        self.collection.add(documents=[item.summary], ids=[...])

    def read(self, query):
        results = self.collection.query(query_texts=[query.query_text], n_results=5)
        return "\n".join(results["documents"][0])
```

## Project structure

```
src/programmaticmemory/
  evolution/        core loop, evaluator, reflector, sandbox, toolkit, types
  benchmarks/       dataset integrations
  baselines/        no-memory and vanilla-RAG
  logging/          experiment tracking, output management
seeds/              3 starting programs
scripts/            experiment scripts
tests/              test suite (disk-cached, runs without API keys)
```

## Citation

Paper coming soon.

## License

Apache 2.0
