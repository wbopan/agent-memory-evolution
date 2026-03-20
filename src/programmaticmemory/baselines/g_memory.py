import json
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = "Extract a brief task description from the text."
INSTRUCTION_QUERY = "Formulate a short query describing the task you need help with."
INSTRUCTION_RESPONSE = (
    "Based on the retrieved insights and key steps from similar past tasks, "
    "and the original question, provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Task description extracted from trajectory."""

    summary: str = field(metadata={"description": "Brief description of the task"})


@dataclass
class Query:
    """Query to retrieve insights from the hierarchical memory graph."""

    query_text: str = field(metadata={"description": "Description of the current task or question"})


class KnowledgeBase:
    """G-Memory baseline (ALMA, 2602.07755; cf. G-Memory 2025).

    Three-layer hierarchical memory using SQLite (task graph) + ChromaDB (storage):
    - Task layer: tracks task descriptions and links similar tasks via SQLite
    - Trajectory layer: stores full trajectories in ChromaDB
    - Insight layer: LLM-extracted key steps and insights stored alongside trajectories

    On read: finds similar tasks in ChromaDB, traverses SQLite graph for related tasks,
    retrieves their insights and key steps.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("g_memory")
        self.db = toolkit.db
        self._doc_id = 0
        self._init_db()

    def _init_db(self):
        self.db.execute("CREATE TABLE IF NOT EXISTS tasks (  id INTEGER PRIMARY KEY, task_desc TEXT, doc_id TEXT)")
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS task_links ("
            "  task_a INTEGER, task_b INTEGER, "
            "  FOREIGN KEY(task_a) REFERENCES tasks(id), "
            "  FOREIGN KEY(task_b) REFERENCES tasks(id)"
            ")"
        )
        self.db.commit()

    def write(self, item: KnowledgeItem, raw_text: str = "") -> None:
        if not raw_text.strip():
            return
        task_desc = item.summary[:200]

        # Extract key steps via LLM
        try:
            key_steps = self.toolkit.llm_completion(
                [
                    {
                        "role": "user",
                        "content": (
                            "Extract the 3-5 most important steps or key actions from this experience. "
                            "One step per line, prefixed with a number.\n\n"
                            f"Experience:\n{raw_text[:3000]}"
                        ),
                    }
                ]
            )
        except Exception:
            key_steps = ""

        doc_id = f"gm_{self._doc_id}"
        entry = json.dumps(
            {
                "task": task_desc,
                "key_steps": key_steps[:1000],
                "trajectory": raw_text[:3000],
            }
        )
        self.collection.add(documents=[entry], ids=[doc_id])

        # Add to task graph
        self.db.execute(
            "INSERT INTO tasks (task_desc, doc_id) VALUES (?, ?)",
            (task_desc, doc_id),
        )
        new_task_id = self.db.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Link to similar existing tasks (simple: link to all existing tasks —
        # similarity filtering happens at read time via ChromaDB)
        existing = self.db.execute("SELECT id FROM tasks WHERE id != ?", (new_task_id,)).fetchall()
        for (other_id,) in existing[-5:]:  # link to 5 most recent
            self.db.execute(
                "INSERT INTO task_links (task_a, task_b) VALUES (?, ?)",
                (new_task_id, other_id),
            )
        self.db.commit()
        self._doc_id += 1

    def read(self, query: Query) -> str:
        if self._doc_id == 0:
            return "No memory stored."

        # Find most similar tasks via ChromaDB
        results = self.collection.query(
            query_texts=[query.query_text],
            n_results=min(2, self._doc_id),
        )
        docs = results["documents"][0] if results["documents"] else []
        doc_ids = results["ids"][0] if results["ids"] else []

        if not docs:
            return "No relevant memory found."

        # Expand via graph: find linked tasks
        expanded_doc_ids = set(doc_ids)
        for doc_id in doc_ids:
            row = self.db.execute("SELECT id FROM tasks WHERE doc_id = ?", (doc_id,)).fetchone()
            if not row:
                continue
            task_id = row[0]
            linked = self.db.execute(
                "SELECT t.doc_id FROM task_links l "
                "JOIN tasks t ON (t.id = l.task_b AND l.task_a = ?) "
                "   OR (t.id = l.task_a AND l.task_b = ?)",
                (task_id, task_id),
            ).fetchall()
            for (linked_doc_id,) in linked[:3]:
                expanded_doc_ids.add(linked_doc_id)

        # Fetch all expanded docs
        if expanded_doc_ids - set(doc_ids):
            extra = self.collection.get(ids=list(expanded_doc_ids - set(doc_ids)))
            docs.extend(extra["documents"] if extra["documents"] else [])

        # Format output
        parts = []
        for doc in docs[:4]:
            try:
                data = json.loads(doc)
                parts.append(f"Task: {data.get('task', '?')}\nKey steps:\n{data.get('key_steps', 'N/A')}")
            except (json.JSONDecodeError, KeyError):
                parts.append(doc[:500])
        return "\n\n---\n\n".join(parts)[:3000]
