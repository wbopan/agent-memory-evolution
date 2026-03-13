from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Formulate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = (
    "Based on the above knowledge and the original question, provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Minimal knowledge item — discarded by no-memory KB."""

    text: str = field(metadata={"description": "Any text"})


@dataclass
class Query:
    """Minimal query — returns nothing from no-memory KB."""

    text: str = field(metadata={"description": "Any text"})


class KnowledgeBase:
    """No-memory baseline: stores nothing, retrieves nothing."""

    def __init__(self, toolkit):
        pass

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        pass

    def read(self, query: Query) -> str:
        return ""
