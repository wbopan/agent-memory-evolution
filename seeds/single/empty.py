from dataclasses import dataclass, field

COMMIT_MESSAGE = (
    "Title: Empty baseline\n- Discards all input, returns empty output\n- Minimal skeleton for ablation starting point"
)

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Formulate a query to search the knowledge base."
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    summary: str = field(metadata={"description": "A summary of the text"})


@dataclass
class Query:
    query: str = field(metadata={"description": "A search query"})


class KnowledgeBase:
    def __init__(self, toolkit):
        self.toolkit = toolkit

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        pass

    def read(self, query: Query) -> str:
        return ""
