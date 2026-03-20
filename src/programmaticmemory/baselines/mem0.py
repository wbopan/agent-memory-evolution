import datetime
import hashlib
import json
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Extract the most important factual statement from this text. "
    "Focus on personal details, preferences, plans, relationships, and key events. "
    "If there are multiple facts, pick the most specific one."
)
INSTRUCTION_QUERY = "Formulate a short query to search for relevant personal facts and memories."
INSTRUCTION_RESPONSE = (
    "Based on the retrieved personal facts and memories, and the original question, "
    "provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """A factual statement extracted from conversation."""

    summary: str = field(metadata={"description": "A concise factual statement from the text"})


@dataclass
class Query:
    """Search query for retrieving relevant facts."""

    query_text: str = field(metadata={"description": "Query to search stored facts"})


class KnowledgeBase:
    """Mem0-style baseline (Chhablani et al., 2025).

    Faithful reproduction of the Mem0 memory system from MemoryAgentBench.
    Two-phase write: (1) LLM extracts atomic facts from raw text,
    (2) for each fact, search ChromaDB for similar existing memories,
    then LLM decides ADD/UPDATE/DELETE/NONE.
    Read: pure vector similarity search, no LLM call.

    Requires --toolkit-budget >= 5 to allow multiple LLM calls per write.
    """

    FACT_EXTRACTION_PROMPT = (
        "You are a Personal Information Organizer, specialized in accurately "
        "storing facts, user memories, and preferences. Your primary role is to "
        "extract relevant pieces of information from conversations and organize "
        "them into distinct, manageable facts.\n\n"
        "Types of Information to Remember:\n"
        "1. Store Personal Preferences: likes, dislikes, specific preferences\n"
        "2. Maintain Important Personal Details: names, relationships, dates\n"
        "3. Track Plans and Intentions: upcoming events, trips, goals\n"
        "4. Remember Activity and Service Preferences: dining, travel, hobbies\n"
        "5. Monitor Health and Wellness Preferences: dietary, fitness\n"
        "6. Store Professional Details: job titles, work habits, career goals\n"
        "7. Miscellaneous Information Management: favorite books, movies, brands\n\n"
        "Extract facts from the following text. Return a JSON object with a single "
        'key "facts" containing a list of concise factual strings. If nothing '
        'relevant, return {{"facts": []}}.\n\n'
        "Input:\n{text}"
    )

    MERGE_PROMPT = (
        "You are a smart memory manager. You can perform four operations: "
        "(1) ADD a new memory, (2) UPDATE an existing memory, "
        "(3) DELETE an existing memory, (4) NONE (no change).\n\n"
        "Rules:\n"
        "- ADD: The new fact contains genuinely new information not in existing memories.\n"
        "- UPDATE: An existing memory covers the same topic but the new fact has more "
        "detail or corrected info. Keep the ID, rewrite the text. Pick the version with "
        "more information.\n"
        "- DELETE: The new fact directly contradicts an existing memory.\n"
        "- NONE: The new fact is already captured by an existing memory.\n\n"
        "Existing memories:\n{existing}\n\n"
        "New fact:\n{new_fact}\n\n"
        'Return a JSON object with exactly one key "action" whose value is one of '
        '"ADD", "UPDATE", "DELETE", "NONE", and optionally "target_id" '
        '(the integer ID to update/delete) and "text" (the new/updated text).\n'
        'Example: {{"action": "UPDATE", "target_id": 0, "text": "Updated fact"}}\n'
        'Example: {{"action": "ADD", "text": "New fact"}}\n'
        'Example: {{"action": "NONE"}}'
    )

    SIMILARITY_THRESHOLD = 0.35  # ChromaDB L2 distance; lower = more similar

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("mem0_facts")
        self.db = toolkit.db
        self._next_id = 0  # monotonic ID counter for doc IDs (never decremented)
        self._stored = 0  # live document count (decremented on delete)
        self._init_db()

    def _init_db(self):
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS mem0_history ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  memory_id TEXT NOT NULL,"
            "  old_text TEXT,"
            "  new_text TEXT,"
            "  event TEXT NOT NULL,"
            "  created_at TEXT NOT NULL"
            ")"
        )
        self.db.commit()

    def _now(self):
        return datetime.datetime.now().isoformat()

    def _add_fact(self, text, metadata=None):
        doc_id = f"mem0_{self._next_id}"
        meta = {
            "data": text,
            "hash": hashlib.md5(text.encode()).hexdigest(),
            "created_at": self._now(),
        }
        if metadata:
            meta.update(metadata)
        self.collection.add(documents=[text], ids=[doc_id], metadatas=[meta])
        self.db.execute(
            "INSERT INTO mem0_history (memory_id, old_text, new_text, event, created_at) VALUES (?, ?, ?, ?, ?)",
            (doc_id, None, text, "ADD", meta["created_at"]),
        )
        self.db.commit()
        self._next_id += 1
        self._stored += 1
        return doc_id

    def _update_fact(self, doc_id, old_text, new_text):
        now = self._now()
        self.collection.update(
            ids=[doc_id],
            documents=[new_text],
            metadatas=[
                {
                    "data": new_text,
                    "hash": hashlib.md5(new_text.encode()).hexdigest(),
                    "created_at": now,
                }
            ],
        )
        self.db.execute(
            "INSERT INTO mem0_history (memory_id, old_text, new_text, event, created_at) VALUES (?, ?, ?, ?, ?)",
            (doc_id, old_text, new_text, "UPDATE", now),
        )
        self.db.commit()

    def _delete_fact(self, doc_id, old_text):
        self.collection.delete(ids=[doc_id])
        self.db.execute(
            "INSERT INTO mem0_history (memory_id, old_text, new_text, event, created_at) VALUES (?, ?, ?, ?, ?)",
            (doc_id, old_text, None, "DELETE", self._now()),
        )
        self.db.commit()
        self._stored = max(0, self._stored - 1)

    def _parse_json_response(self, response):
        """Strip markdown fences and parse JSON."""
        response = response.strip()
        if response.startswith("```"):
            lines = response.split("\n")
            if lines[-1].strip().startswith("```"):
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            response = "\n".join(lines)
        return json.loads(response)

    def _extract_facts(self, raw_text):
        """LLM call 1: extract atomic facts from raw text (Mem0 FACT_RETRIEVAL_PROMPT)."""
        prompt = self.FACT_EXTRACTION_PROMPT.format(text=raw_text[:3000])
        try:
            response = self.toolkit.llm_completion([{"role": "user", "content": prompt}])
            parsed = self._parse_json_response(response)
            facts = parsed.get("facts", [])
            return [f for f in facts if isinstance(f, str) and f.strip()]
        except Exception:
            return []

    def _merge_fact(self, fact):
        """Process a single fact: search for similar, LLM merge decision if needed."""
        if self._stored == 0:
            self._add_fact(fact)
            return

        results = self.collection.query(
            query_texts=[fact],
            n_results=min(5, self._stored),
        )

        distances = results["distances"][0] if results["distances"] else []
        docs = results["documents"][0] if results["documents"] else []
        ids = results["ids"][0] if results["ids"] else []

        # No close match — just add
        if not distances or distances[0] > self.SIMILARITY_THRESHOLD:
            self._add_fact(fact)
            return

        # Build existing list for LLM merge decision
        existing_list = []
        id_mapping = {}
        for i, (doc_id, doc) in enumerate(zip(ids, docs, strict=False)):
            if distances[i] <= self.SIMILARITY_THRESHOLD:
                existing_list.append({"id": i, "text": doc})
                id_mapping[i] = doc_id

        if not existing_list:
            self._add_fact(fact)
            return

        prompt = self.MERGE_PROMPT.format(
            existing=json.dumps(existing_list, ensure_ascii=False),
            new_fact=fact,
        )

        try:
            decision = self._parse_json_response(self.toolkit.llm_completion([{"role": "user", "content": prompt}]))
        except Exception:
            self._add_fact(fact)
            return

        action = decision.get("action", "ADD").upper()
        target_idx = decision.get("target_id")
        new_text = decision.get("text", fact)

        if action == "UPDATE" and target_idx is not None and target_idx in id_mapping:
            real_id = id_mapping[target_idx]
            old_doc = next((e["text"] for e in existing_list if e["id"] == target_idx), "")
            self._update_fact(real_id, old_doc, new_text)
        elif action == "DELETE" and target_idx is not None and target_idx in id_mapping:
            real_id = id_mapping[target_idx]
            old_doc = next((e["text"] for e in existing_list if e["id"] == target_idx), "")
            self._delete_fact(real_id, old_doc)
        elif action == "NONE":
            pass
        else:
            self._add_fact(new_text if new_text else fact)

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        if not raw_text.strip():
            return

        # Phase 1: LLM fact extraction from raw text (faithful to Mem0)
        facts = self._extract_facts(raw_text)

        # Fallback: use the task-agent-extracted summary if LLM extraction fails
        if not facts and item.summary.strip():
            facts = [item.summary.strip()]

        # Phase 2: For each fact, merge into memory store
        for fact in facts:
            self._merge_fact(fact)

    def read(self, query: Query) -> str:
        if self._stored == 0:
            return ""

        results = self.collection.query(
            query_texts=[query.query_text],
            n_results=min(10, self._stored),
        )
        docs = results["documents"][0] if results["documents"] else []
        if not docs:
            return ""

        parts = [f"- {doc}" for doc in docs]
        return "\n".join(parts)[:3000]
