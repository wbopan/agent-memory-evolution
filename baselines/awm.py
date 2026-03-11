# baselines/awm.py
import json
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Analyze the text and extract a reusable workflow: "
    "a high-level goal description and a sequence of concrete steps that accomplish it. "
    "Focus on generalizable procedures, not specific details."
)
INSTRUCTION_QUERY = "Given the following question, generate a query to find relevant procedural workflows."
INSTRUCTION_RESPONSE = (
    "Use the retrieved workflows as guidance to solve the task. "
    "Follow applicable steps and adapt them to the current situation. "
    "Provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A reusable workflow extracted from experience."""
    goal: str = field(metadata={"description": "High-level goal this workflow accomplishes"})
    steps: str = field(metadata={"description": "Sequence of concrete steps to achieve the goal, separated by newlines"})


@dataclass
class Query:
    """Query to find relevant workflows."""
    raw: str = field(metadata={"description": "Description of the current task or goal"})


class KnowledgeBase:
    """Agent Workflow Memory — stores and retrieves reusable workflows.

    write(): Uses toolkit LLM to distill a generalizable workflow from the
    training text, stores it in ChromaDB for semantic retrieval.
    read(): Retrieves the most similar workflow(s) via embedding search.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("workflows")
        self._count = 0

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Given the following experience, extract a REUSABLE workflow.\n"
                    "Output a JSON object with:\n"
                    '  "goal": a one-sentence description of what this workflow accomplishes\n'
                    '  "steps": a list of step strings describing the procedure\n\n'
                    f"Experience:\n{raw_text[:2000]}\n\n"
                    "Output ONLY valid JSON."
                ),
            }
        ]
        try:
            response = self.toolkit.llm_completion(messages)
            data = json.loads(response)
            goal = data.get("goal", item.goal)
            steps = data.get("steps", [item.steps])
            if isinstance(steps, list):
                steps_str = "\n".join(str(s) for s in steps)
            else:
                steps_str = str(steps)
        except Exception:
            goal = item.goal
            steps_str = item.steps

        self.collection.add(
            documents=[goal],
            metadatas=[{"steps": steps_str[:500]}],
            ids=[str(self._count)],
        )
        self._count += 1
        self.toolkit.logger.debug(f"Stored workflow: {goal[:80]}")

    def read(self, query: Query) -> str:
        if self._count == 0:
            return ""
        results = self.collection.query(
            query_texts=[query.raw],
            n_results=min(2, self._count),
        )
        parts = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            steps = meta.get("steps", "")
            parts.append(f"Workflow: {doc}\nSteps:\n{steps}")
        return "\n\n".join(parts)[:1000]
