# Seed Programs Design

## Context

The evolution loop starts from a pool of seed programs. The original baseline (append-all/return-all) is being replaced with 3 diverse seeds that test fundamentally different retrieval strategies.

## Seeds

### 1. `vector_search.py` — Semantic Vector Retrieval

Store raw text in ChromaDB, retrieve top-2 most similar documents.

- **KnowledgeItem**: `question: str`, `answer: str`
- **Query**: `raw: str`
- **write()**: Store `"{question} {answer}"` in ChromaDB, raw_text as metadata (truncated to 500 chars)
- **read()**: Query ChromaDB with `n_results=2`, return metadata raw_text (500 chars each)

### 2. `llm_summarizer.py` — LLM-Powered Query-Focused Summary

Store all raw text, produce query-focused LLM summary at read time.

- **KnowledgeItem**: `summary: str`
- **Query**: `query_text: str`
- **write()**: Append raw_text to internal list
- **read()**: Concat all raw texts (truncate 30k chars), call `toolkit.llm_completion()` for query-focused summary, cap at 1000 chars

### 3. `experience_learner.py` — Experience-Driven Learner

Extract structured lessons and facts, discard raw text, return everything.

- **KnowledgeItem**: `lesson_learned: str`, `fact_to_remember: str`
- **Query**: `raw: str` (ignored)
- **write()**: Store lesson and fact separately, ignore raw_text
- **read()**: Join all lessons + facts, truncate to 1000 chars

## Diversity Matrix

| Seed | Storage | Retrieval | ChromaDB | LLM in KB |
|------|---------|-----------|----------|-----------|
| vector_search | embeddings + raw | top-2 similarity | Yes | No |
| llm_summarizer | raw text list | LLM summary | No | Yes |
| experience_learner | lessons + facts | dump all | No | No |
