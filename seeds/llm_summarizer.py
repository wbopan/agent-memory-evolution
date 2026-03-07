from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = (
    "Formulate a natural language query to search the knowledge base "
    "for information relevant to the question."
)
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer without explanation."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A summary of what was learnt from the source text."""
    summary: str = field(metadata={"description": "What you have learnt from the text"})


@dataclass
class Query:
    """Natural language query to the knowledge base."""
    query_text: str = field(metadata={"description": "A natural language query describing what information you need"})


class KnowledgeBase:
    """LLM-powered query-focused summarization over stored raw texts."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.raw_texts: list[str] = []

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.raw_texts.append(raw_text)
        self.toolkit.logger.debug(f"Stored raw text ({len(raw_text)} chars), total: {len(self.raw_texts)}")

    def read(self, query: Query) -> str:
        if not self.raw_texts:
            return "No information stored."
        combined = "\n\n".join(self.raw_texts)[:30000]
        messages = [
            {
                "role": "user",
                "content": (
                    f"Given the following query, summarize ONLY the relevant information "
                    f"from the provided texts. Be concise and factual.\n\n"
                    f"Query: {query.query_text}\n\n"
                    f"Texts:\n{combined}"
                ),
            }
        ]
        self.toolkit.logger.debug(f"Query: {query.query_text}, sending {len(combined)} chars to LLM")
        try:
            result = self.toolkit.llm_completion(messages)
        except Exception:
            # Fallback: return truncated raw text if LLM call fails
            result = combined
        return result[:1000]
