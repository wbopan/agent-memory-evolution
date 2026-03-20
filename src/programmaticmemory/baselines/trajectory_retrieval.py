from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Briefly describe the type of task in this trajectory."
INSTRUCTION_QUERY = "Formulate a short query describing the task you need help with."
INSTRUCTION_RESPONSE = (
    "Based on the retrieved trajectory and the original question, provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Brief task description extracted from trajectory."""

    summary: str = field(metadata={"description": "Brief description of the task type"})


@dataclass
class Query:
    """Query to find the most similar past trajectory."""

    query_text: str = field(metadata={"description": "Description of the current task or question"})


class KnowledgeBase:
    """Trajectory Retrieval baseline (ALMA, 2602.07755).

    Stores full raw trajectories in ChromaDB. On read, retrieves the most
    similar trajectory by embedding cosine similarity.
    No LLM calls — purely embedding-based retrieval.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("trajectories")
        self._doc_id = 0

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        if not raw_text.strip():
            return
        self.collection.add(
            documents=[raw_text[:8000]],
            ids=[f"traj_{self._doc_id}"],
        )
        self._doc_id += 1

    def read(self, query: Query) -> str:
        if self._doc_id == 0:
            return "No trajectories stored."
        results = self.collection.query(
            query_texts=[query.query_text],
            n_results=1,
        )
        docs = results["documents"][0] if results["documents"] else []
        if not docs:
            return "No relevant trajectory found."
        return docs[0][:3000]
