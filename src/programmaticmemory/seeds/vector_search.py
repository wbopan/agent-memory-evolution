from dataclasses import dataclass, field

COMMIT_MESSAGE = (
    "Title: Vanilla RAG with ChromaDB\n"
    "- Chunks raw text into paragraphs, stores in ChromaDB\n"
    "- Retrieves top-k by semantic similarity, no LLM in read()"
)

INSTRUCTION_KNOWLEDGE_ITEM = "Summarize the key information from the text."
INSTRUCTION_QUERY = (
    "Formulate a natural language query to search the knowledge base for information relevant to the question."
)
INSTRUCTION_RESPONSE = (
    "Based on the above knowledge and the original question, provide a short answer without explanation."
)
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
    """Vanilla RAG: store text chunks in ChromaDB, retrieve by semantic similarity."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("knowledge")
        self._doc_id = 0

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        chunks = self._chunk(raw_text, max_chars=500)
        for chunk in chunks:
            self.collection.add(
                documents=[chunk],
                ids=[f"doc_{self._doc_id}"],
            )
            self._doc_id += 1
        self.toolkit.logger.debug(f"Stored {len(chunks)} chunks, total docs: {self._doc_id}")

    def read(self, query: Query) -> str:
        if self._doc_id == 0:
            return "No information stored."
        results = self.collection.query(
            query_texts=[query.query_text],
            n_results=min(5, self._doc_id),
        )
        docs = results["documents"][0] if results["documents"] else []
        if not docs:
            return "No relevant information found."
        return "\n\n".join(docs)[:3000]

    @staticmethod
    def _chunk(text: str, max_chars: int = 500) -> list[str]:
        """Split text into chunks by paragraphs, merging short ones."""
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        if not paragraphs:
            paragraphs = [text.strip()] if text.strip() else [""]
        chunks = []
        current = ""
        for para in paragraphs:
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append(current)
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para
        if current:
            chunks.append(current)
        return chunks if chunks else [text[:max_chars]]
