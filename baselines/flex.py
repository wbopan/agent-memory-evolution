# baselines/flex.py
import json
from dataclasses import dataclass, field

INSTRUCTION_KNOWLEDGE_ITEM = (
    "Analyze the text and extract two types of insights:\n"
    "1. Golden rules — best practices, strategies, or patterns that lead to success\n"
    "2. Warnings — common pitfalls, mistakes to avoid, or failure patterns\n"
    "Be concise and actionable."
)
INSTRUCTION_QUERY = "Given the following question, generate a query to find relevant rules and warnings."
INSTRUCTION_RESPONSE = (
    "Use the retrieved golden rules and warnings to guide your reasoning. "
    "Follow the golden rules and avoid the warned pitfalls. "
    "Provide a short answer without explanation."
)
ALWAYS_ON_KNOWLEDGE = ""


@dataclass
class KnowledgeItem:
    """Experience distilled into actionable rules and warnings."""
    golden_rule: str = field(metadata={"description": "A best practice or successful strategy learned from the text"})
    warning: str = field(metadata={"description": "A pitfall or common mistake to avoid, learned from the text"})


@dataclass
class Query:
    """Query to retrieve relevant rules and warnings."""
    raw: str = field(metadata={"description": "Description of the current problem or task"})


class KnowledgeBase:
    """FLEX-style experience library with golden rules and warnings.

    write(): Uses toolkit LLM to distill golden rules and warnings from
    training text, stores in ChromaDB with partition tags.
    read(): Retrieves relevant rules via embedding search, formats by type.
    """

    def __init__(self, toolkit):
        self.toolkit = toolkit
        self.collection = toolkit.chroma.get_or_create_collection("experience")
        self._count = 0

    def write(self, item: KnowledgeItem, raw_text: str) -> None:
        messages = [
            {
                "role": "user",
                "content": (
                    "Analyze this experience and extract:\n"
                    '1. "golden_rules": list of best practices / successful strategies\n'
                    '2. "warnings": list of pitfalls / mistakes to avoid\n'
                    "Each rule should be one concise sentence.\n\n"
                    f"Experience:\n{raw_text[:2000]}\n\n"
                    "Output ONLY valid JSON with keys 'golden_rules' and 'warnings'."
                ),
            }
        ]
        try:
            response = self.toolkit.llm_completion(messages)
            data = json.loads(response)
            golden = data.get("golden_rules", [item.golden_rule])
            warnings = data.get("warnings", [item.warning])
        except Exception:
            golden = [item.golden_rule]
            warnings = [item.warning]

        for rule in golden:
            rule_str = str(rule).strip()
            if rule_str:
                self.collection.add(
                    documents=[rule_str],
                    metadatas=[{"type": "golden"}],
                    ids=[str(self._count)],
                )
                self._count += 1

        for warn in warnings:
            warn_str = str(warn).strip()
            if warn_str:
                self.collection.add(
                    documents=[warn_str],
                    metadatas=[{"type": "warning"}],
                    ids=[str(self._count)],
                )
                self._count += 1

        self.toolkit.logger.debug(
            f"Stored {len(golden)} golden rules, {len(warnings)} warnings"
        )

    def read(self, query: Query) -> str:
        if self._count == 0:
            return ""
        results = self.collection.query(
            query_texts=[query.raw],
            n_results=min(5, self._count),
        )
        golden_parts = []
        warning_parts = []
        for doc, meta in zip(results["documents"][0], results["metadatas"][0]):
            if meta.get("type") == "golden":
                golden_parts.append(f"- {doc}")
            else:
                warning_parts.append(f"- {doc}")
        parts = []
        if golden_parts:
            parts.append("Golden rules:\n" + "\n".join(golden_parts))
        if warning_parts:
            parts.append("Warnings:\n" + "\n".join(warning_parts))
        return "\n\n".join(parts)[:1000]
