from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract a question-answer pair from the text. "
    "The question should capture what this text is about, "
    "and the answer should contain the key factual content."
)
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = "Based on the above knowledge and the original question, provide a short answer without explanation."
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A question-answer pair extracted from source text."""
    question: str = field(metadata={"description": "A question that this text answers"})
    answer: str = field(metadata={"description": "The factual answer contained in the text"})


@dataclass
class Query:
    """Raw text query for semantic search."""
    raw: str = field(metadata={"description": "The query text to search for"})


class KnowledgeBase:
    """Semantic vector retrieval using ChromaDB embeddings."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("knowledge")
        self._doc_count = 0

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        doc_text = f"{item.question} {item.answer}"
        meta = {"raw_text": raw_text[:500]}
        self.collection.add(
            documents=[doc_text],
            metadatas=[meta],
            ids=[str(self._doc_count)],
        )
        self._doc_count += 1
        self.toolkit.logger.debug(f"Stored doc {self._doc_count}: {doc_text[:80]}")

    def read(self, query: Query) -> str:
        if self._doc_count == 0:
            return "No information stored."
        results = self.collection.query(query_texts=[query.raw], n_results=min(2, self._doc_count))
        parts = []
        for meta in results["metadatas"][0]:
            parts.append(meta.get("raw_text", "")[:500])
        result = "\n\n".join(parts)
        self.toolkit.logger.debug(f"Query: {query.raw}, retrieved {len(parts)} results")
        return result[:1000]
