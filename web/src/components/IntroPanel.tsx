const INTERFACE = `INSTRUCTION_KNOWLEDGE_ITEM = "..."
INSTRUCTION_QUERY          = "..."
INSTRUCTION_RESPONSE       = "..."

@dataclass
class KnowledgeItem:
    summary: str

@dataclass
class Query:
    query_text: str

class KnowledgeBase:
    def __init__(self, toolkit):
        self.col = toolkit.chroma.collection("kb")

    def write(self, item, raw_text=""):
        self.col.add(documents=[item.summary])

    def read(self, query):
        r = self.col.query([query.query_text], n=5)
        return "\\n".join(r["documents"][0])`;

const POINTS = [
  {
    head: "Schema",
    body: (
      <>
        dataclasses (<code className="inl">KnowledgeItem</code>,{" "}
        <code className="inl">Query</code>) deciding what the memory stores and
        how it is queried.
      </>
    ),
  },
  {
    head: "Logic",
    body: (
      <>
        the <code className="inl">write()</code> and{" "}
        <code className="inl">read()</code> methods, over a toolkit of SQLite,
        ChromaDB, and a budget-limited LLM.
      </>
    ),
  },
  {
    head: "Instruction",
    body: <>prompt constants that steer how the agent summarizes, queries, and answers.</>,
  },
];

export default function IntroPanel() {
  return (
    <div className="grid grid-cols-1 gap-9 lg:grid-cols-[1fr_400px] lg:items-start">
      <div className="prose-paper">
        <p>
          Large language model agents rely on a memory harness to write,
          organize, retrieve, and use past experience. A harness that works well
          for one task often fails on another, because conversation, embodied
          planning, and specialized reasoning each demand different storage and
          retrieval behavior. M★ automatically discovers a task-optimized memory
          harness for each task through reflective code evolution.
        </p>
        <p>
          Rather than choosing among a fixed set of designs, M★ represents a
          memory harness as an executable Python program and searches over that
          program directly. Three parts of the program are optimized together:
        </p>
        <ul className="my-4 list-none p-0">
          {POINTS.map((pt) => (
            <li
              key={pt.head}
              className="relative border-b border-[var(--line-2)] py-2.5 pl-5 text-[15.5px] leading-relaxed"
            >
              <span className="absolute left-0 top-[15px] h-[7px] w-[7px] rounded-full bg-primary" />
              <b className="font-semibold text-foreground">{pt.head}</b>
              <span className="text-[var(--ink-2)]"> — {pt.body}</span>
            </li>
          ))}
        </ul>
        <p>
          The task agent that uses the memory is held fixed, so any change in
          score is attributable to the memory. Across four tasks —
          conversation, embodied planning, and expert reasoning — M★ improves
          over static memory harnesses on every task, and the programs it
          discovers are structurally distinct across domains. The{" "}
          <b className="font-semibold">Evolution Loop</b> tab shows how programs
          are discovered; the <b className="font-semibold">Inspector</b> tab
          opens the recorded runs program by program.
        </p>
      </div>

      <aside>
        <div className="mb-2 font-mono text-[11px] font-semibold uppercase tracking-wider text-[var(--ink-2)]">
          The interface every program implements
        </div>
        <pre className="cite" tabIndex={0} aria-label="Memory program interface">
          {INTERFACE}
        </pre>
      </aside>
    </div>
  );
}
