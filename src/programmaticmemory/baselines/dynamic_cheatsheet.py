from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Briefly describe the type of task or topic in this text."
INSTRUCTION_QUERY = "Formulate a short query describing what you need help with."
INSTRUCTION_RESPONSE = (
    "Based on the cheatsheet of accumulated knowledge and the original question, "
    "provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Brief topic summary (used only for LLM extraction; cheatsheet update uses raw_text)."""

    summary: str = field(metadata={"description": "Brief description of the topic"})


@dataclass
class Query:
    """Query — the entire cheatsheet is returned regardless of query content."""

    query_text: str = field(metadata={"description": "The question or task description"})


class KnowledgeBase:
    """Dynamic Cheatsheet baseline (ALMA, 2602.07755; cf. Suzgun et al. 2025).

    Maintains a single global cheatsheet. On each write, the LLM updates the
    cheatsheet by integrating new experience (cumulative mode). On read, returns
    the full cheatsheet — no retrieval logic needed.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self._cheatsheet = ""

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        if not raw_text.strip():
            return
        try:
            updated = self.toolkit.llm_completion(
                [
                    {
                        "role": "user",
                        "content": (
                            "You are a Cheatsheet Curator. Your job is to maintain a concise, evolving "
                            "reference of key strategies, rules, and insights learned from experience.\n\n"
                            "CURRENT CHEATSHEET:\n"
                            f"{self._cheatsheet if self._cheatsheet else '(empty — first entry)'}\n\n"
                            "NEW EXPERIENCE:\n"
                            f"{raw_text[:3000]}\n\n"
                            "Update the cheatsheet by integrating any new insights from this experience. "
                            "Preserve existing useful entries. Remove redundancies. Keep it under 2000 words. "
                            "Output ONLY the updated cheatsheet content, nothing else."
                        ),
                    }
                ]
            )
            self._cheatsheet = updated[:6000]
        except Exception:
            # LLM unavailable or budget exhausted — append raw summary as fallback
            self._cheatsheet += f"\n- {item.summary[:200]}"
            self._cheatsheet = self._cheatsheet[:6000]

    def read(self, query: Query) -> str:
        if not self._cheatsheet:
            return "No knowledge accumulated yet."
        return self._cheatsheet[:3000]
