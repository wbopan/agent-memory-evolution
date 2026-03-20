import json
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Extract a brief task description and the key insight or lesson from this experience."
INSTRUCTION_QUERY = "Formulate a short query describing the task you need help with."
INSTRUCTION_RESPONSE = (
    "Based on the retrieved insights from similar past tasks and the original question, "
    "provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Task description + extracted insight from a trajectory."""

    summary: str = field(metadata={"description": "Brief description of the task type"})


@dataclass
class Query:
    """Query to find insights from the most similar past task."""

    query_text: str = field(metadata={"description": "Description of the current task or question"})


class KnowledgeBase:
    """ReasoningBank baseline (ALMA, 2602.07755; cf. ReasoningBank 2509.25140).

    On write: uses LLM to extract key insights from the trajectory, stores
    task description + insights together in ChromaDB (keyed by task description).
    On read: finds the most similar task description, returns its insights.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("reasoning_bank")
        self._doc_id = 0

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        if not raw_text.strip():
            return
        # Extract insights via LLM
        try:
            insights = self.toolkit.llm_completion(
                [
                    {
                        "role": "user",
                        "content": (
                            "Analyze the following experience and extract 2-5 key insights, "
                            "rules, or strategies that would help with similar tasks in the future. "
                            "Be concise. One insight per line, prefixed with '- '.\n\n"
                            f"Experience:\n{raw_text[:4000]}"
                        ),
                    }
                ]
            )
        except Exception:
            insights = raw_text[:500]

        entry = json.dumps(
            {
                "task": item.summary[:200],
                "insights": insights[:2000],
            }
        )
        self.collection.add(
            documents=[entry],
            metadatas=[{"task_desc": item.summary[:200]}],
            ids=[f"rb_{self._doc_id}"],
        )
        self._doc_id += 1

    def read(self, query: Query) -> str:
        if self._doc_id == 0:
            return "No insights stored."
        results = self.collection.query(
            query_texts=[query.query_text],
            n_results=min(3, self._doc_id),
        )
        docs = results["documents"][0] if results["documents"] else []
        if not docs:
            return "No relevant insights found."
        # Parse and format insights
        parts = []
        for doc in docs:
            try:
                data = json.loads(doc)
                parts.append(f"Task: {data['task']}\nInsights:\n{data['insights']}")
            except (json.JSONDecodeError, KeyError):
                parts.append(doc[:500])
        return "\n\n---\n\n".join(parts)[:3000]
