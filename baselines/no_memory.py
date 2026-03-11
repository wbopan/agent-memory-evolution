# baselines/no_memory.py
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = "Answer the question based on your own knowledge. No external memory is available."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Placeholder — no memory is stored."""

    summary: str = field(metadata={"description": "A brief summary (will be discarded)"})


@dataclass
class Query:
    """Placeholder — no retrieval is performed."""

    raw: str = field(metadata={"description": "The query text"})


class KnowledgeBase:
    """No-op knowledge base. Discards all writes, returns empty on reads."""

    def __init__(self, toolkit):
        self.toolkit = toolkit

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        pass

    def read(self, query: Query) -> str:
        return ""
