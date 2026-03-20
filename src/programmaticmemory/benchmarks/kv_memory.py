"""KV Memory benchmark — simple factual recall for testing the evolution pipeline."""

from __future__ import annotations

import random

from programmaticmemory.datasets import register_dataset
from programmaticmemory.evolution.evaluator import ExactMatchScorer
from programmaticmemory.evolution.types import DataItem, Dataset

# Factual statements and corresponding QA pairs
_FACTS = [
    ("The capital of France is Paris.", "What is the capital of France?", "Paris"),
    ("The speed of light is approximately 299,792 km/s.", "What is the speed of light?", "299,792 km/s"),
    (
        "Water boils at 100 degrees Celsius at sea level.",
        "At what temperature does water boil at sea level?",
        "100 degrees Celsius",
    ),
    (
        "The largest planet in our solar system is Jupiter.",
        "What is the largest planet in our solar system?",
        "Jupiter",
    ),
    ("DNA stands for deoxyribonucleic acid.", "What does DNA stand for?", "deoxyribonucleic acid"),
    ("The Great Wall of China is over 13,000 miles long.", "How long is the Great Wall of China?", "over 13,000 miles"),
    ("Oxygen has the atomic number 8.", "What is the atomic number of oxygen?", "8"),
    ("Shakespeare was born in 1564.", "When was Shakespeare born?", "1564"),
    ("The Nile is the longest river in the world.", "What is the longest river in the world?", "the Nile"),
    ("Mount Everest is 8,849 meters tall.", "How tall is Mount Everest?", "8,849 meters"),
    ("The human body has 206 bones.", "How many bones does the human body have?", "206"),
    ("Pi is approximately 3.14159.", "What is the approximate value of Pi?", "3.14159"),
    ("Gold has the chemical symbol Au.", "What is the chemical symbol for gold?", "Au"),
    (
        "The Amazon rainforest produces about 20% of the world's oxygen.",
        "What percentage of the world's oxygen does the Amazon produce?",
        "20%",
    ),
    (
        "Einstein published the theory of general relativity in 1915.",
        "When did Einstein publish the theory of general relativity?",
        "1915",
    ),
    ("The Pacific Ocean is the largest ocean on Earth.", "What is the largest ocean on Earth?", "the Pacific Ocean"),
    ("A marathon is 42.195 kilometers.", "How long is a marathon in kilometers?", "42.195 kilometers"),
    ("The Mona Lisa was painted by Leonardo da Vinci.", "Who painted the Mona Lisa?", "Leonardo da Vinci"),
    (
        "Sound travels at about 343 meters per second in air.",
        "What is the speed of sound in air?",
        "343 meters per second",
    ),
    ("The Earth is approximately 4.5 billion years old.", "How old is the Earth?", "4.5 billion years"),
]

# Compound facts requiring reasoning across multiple pieces
_COMPOUND_FACTS = [
    (
        ["Alice's favorite color is blue.", "Bob's favorite color is the same as Alice's."],
        "What is Bob's favorite color?",
        "blue",
    ),
    (
        ["The project deadline is March 15.", "The review must happen 3 days before the deadline."],
        "When must the review happen?",
        "March 12",
    ),
    (
        ["The server runs on port 8080.", "The API endpoint is /api/v2/users.", "The protocol is HTTPS."],
        "What is the full URL for the users API?",
        "https://localhost:8080/api/v2/users",
    ),
    (
        ["Team A scored 3 goals.", "Team B scored 5 goals."],
        "Which team won and by how many goals?",
        "Team B won by 2 goals",
    ),
    (
        ["The recipe requires 2 cups of flour.", "Each cup of flour weighs 120 grams."],
        "How many grams of flour does the recipe require?",
        "240 grams",
    ),
]


@register_dataset("kv_memory")
def load_kv_memory(
    *,
    num_items: int = 15,
    difficulty: str = "simple",
    seed: int = 42,
    category: str | None = None,
) -> Dataset:
    """Load KV memory benchmark.

    Args:
        num_items: Number of items to include (max 20 for simple, 5 for compound).
        difficulty: "simple" for basic KV recall, "compound" for multi-fact reasoning.
        seed: Random seed for shuffling.
        category: Not supported; raises ValueError if not None.

    Returns:
        Dataset with ExactMatchScorer.
    """
    if category is not None:
        raise ValueError("kv_memory does not support category filtering")
    rng = random.Random(seed)

    if difficulty == "compound":
        items = _COMPOUND_FACTS[:num_items]
        rng.shuffle(items)
        data = []
        for facts, question, answer in items:
            raw_text = " ".join(facts)
            data.append(DataItem(raw_text=raw_text, question=question, expected_answer=answer))
    else:
        facts = _FACTS[:num_items]
        rng.shuffle(facts)
        data = [DataItem(raw_text=r, question=q, expected_answer=a) for r, q, a in facts]

    # All items are both train (ingest) and val (query) for offline eval
    return Dataset(train=data, val=data, test=[], compare_fn=ExactMatchScorer())
