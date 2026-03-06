from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer without explanation."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A summary of what was learnt from the source text."""
    summary: str = field(metadata={"description": "What you have learnt from the text"})


@dataclass
class Query:
    """Raw text query to retrieve from the knowledge base."""
    raw: str = field(metadata={"description": "The query text to search for"})


class KnowledgeBase:
    """Simple append-all / return-all knowledge base."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.summaries: list[str] = []
        self.observations: list[str] = []

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.summaries.append(item.summary)
        self.observations.append(raw_text)
        self.toolkit.logger.debug(f"Stored summary: {item.summary}")

    def read(self, query: Query) -> str:
        self.toolkit.logger.debug(f"Query: {query.raw}, summaries: {len(self.summaries)}, observations: {len(self.observations)}")
        if not self.summaries and not self.observations:
            return "No information stored."
        summary_text = "\n".join(self.summaries)[:500]
        observation_text = "\n".join(self.observations)[:500]
        result = summary_text + "\n" + observation_text
        return result[:1000]
