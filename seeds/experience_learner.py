from dataclasses import dataclass, field

COMMIT_MESSAGE = (
    "Title: Lesson-fact dual storage with full recall\n- Extracts lessons and facts separately, returns all on read()"
)

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract two things from the text: "
    "(1) a general lesson or pattern learned, and "
    "(2) a specific fact worth remembering."
)
INSTRUCTION_QUERY = "Given the following question, generate a query to retrieve relevant knowledge."
INSTRUCTION_RESPONSE = (
    "Based on the above knowledge and the original question, provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A lesson and a fact extracted from source text."""

    lesson_learned: str = field(metadata={"description": "A general lesson or pattern learned from the text"})
    fact_to_remember: str = field(metadata={"description": "A specific fact worth remembering from the text"})


@dataclass
class Query:
    """Query to the knowledge base (all knowledge is returned regardless)."""

    raw: str = field(metadata={"description": "The query text"})


class KnowledgeBase:
    """Experience-driven learner that stores lessons and facts, returns all on read."""

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.lessons: list[str] = []
        self.facts: list[str] = []

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        self.lessons.append(item.lesson_learned)
        self.facts.append(item.fact_to_remember)
        self.toolkit.logger.debug(f"Stored lesson: {item.lesson_learned[:60]}, fact: {item.fact_to_remember[:60]}")

    def read(self, query: Query) -> str:
        if not self.lessons and not self.facts:
            return "No information stored."
        lessons_text = "\n".join(self.lessons)[:500]
        facts_text = "\n".join(self.facts)[:500]
        result = f"Lessons:\n{lessons_text}\n\nFacts:\n{facts_text}"
        self.toolkit.logger.debug(f"Returning {len(self.lessons)} lessons, {len(self.facts)} facts")
        return result[:1000]
