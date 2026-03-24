from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract a concise summary capturing the key facts from the text. Focus on who, what, when, where, and why."
)
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = (
    "Based on the above knowledge and the original question, provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A factual summary extracted from source text."""

    summary: str = field(metadata={"description": "Concise summary of key facts from the text"})


@dataclass
class Query:
    """Semantic search query."""

    text: str = field(metadata={"description": "The query text to search for"})


class KnowledgeBase:
    """Embedding-based retrieval using ChromaDB. No LLM calls."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("knowledge")
        self._count = 0

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        doc = f"{item.summary}\n{raw_text[:300]}"
        self.collection.add(
            documents=[doc],
            metadatas=[{"raw": raw_text[:500]}],
            ids=[str(self._count)],
        )
        self._count += 1
        self.toolkit.logger.debug(f"Stored doc {self._count}: {doc[:80]}")

    def read(self, query: Query) -> str:
        if self._count == 0:
            return ""
        results = self.collection.query(
            query_texts=[query.text],
            n_results=min(3, self._count),
        )
        parts = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0], strict=True):
            parts.append(meta.get("raw", doc)[:300])
        self.toolkit.logger.debug(f"Query: {query.text}, retrieved {len(parts)} results")
        return "\n\n".join(parts)[:1000]
