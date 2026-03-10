"""Vibe check: inspect batching results on real datasets without running evolution."""

from __future__ import annotations

from programmaticmemory.cache import configure_cache
from programmaticmemory.datasets import load_dataset
from programmaticmemory.evolution.batching import build_eval_batches


def truncate(s: str, max_len: int = 120) -> str:
    s = s.replace("\n", " ").strip()
    return s[:max_len] + "..." if len(s) > max_len else s


def vibe_check(dataset_name: str, num_batches: int = 5, **kwargs):
    print(f"\n{'=' * 80}")
    print(f"DATASET: {dataset_name}")
    print(f"{'=' * 80}")

    dataset = load_dataset(dataset_name, **kwargs)
    print(f"Total train: {len(dataset.train)}, Total val: {len(dataset.val)}")

    # Detect pipeline type
    is_offline = dataset.train and dataset.train[0].raw_text
    print(f"Pipeline: {'offline (raw_text)' if is_offline else 'online (question-only)'}")

    batches = build_eval_batches(
        dataset.train,
        dataset.val,
        num_batches=num_batches,
        train_budget_per_val=5,
    )

    for i, batch in enumerate(batches):
        print(f"\n{'─' * 80}")
        print(
            f"BATCH {i}: val={len(batch.val_indices)}, train={len(batch.train_indices)}, coverage={batch.coverage:.4f}"
        )
        print(f"{'─' * 80}")

        print(f"\n  VAL QUESTIONS ({len(batch.val_indices)}):")
        for j, idx in enumerate(batch.val_indices):
            item = dataset.val[idx]
            print(f"    [{idx:3d}] Q: {truncate(item.question, 100)}")
            print(f"          A: {truncate(item.expected_answer, 80)}")

        print(f"\n  TRAIN ITEMS ({len(batch.train_indices)}):")
        for j, idx in enumerate(batch.train_indices):
            item = dataset.train[idx]
            if is_offline:
                print(f"    [{idx:3d}] {truncate(item.raw_text, 120)}")
            else:
                print(f"    [{idx:3d}] Q: {truncate(item.question, 100)}")

    # Summary
    print(f"\n{'=' * 80}")
    print("SUMMARY")
    print(f"{'=' * 80}")
    sizes = [(len(b.val_indices), len(b.train_indices), b.coverage) for b in batches]
    for i, (v, t, c) in enumerate(sizes):
        print(f"  Batch {i}: val={v}, train={t}, coverage={c:.4f}")
    total_val = sum(v for v, _, _ in sizes)
    total_train_unique = len(set(idx for b in batches for idx in b.train_indices))
    print(f"  Total val items covered: {total_val}/{len(dataset.val)}")
    print(f"  Unique train items used: {total_train_unique}/{len(dataset.train)}")
    # Check for train overlap between batches
    overlaps = []
    for i in range(len(batches)):
        for j in range(i + 1, len(batches)):
            overlap = set(batches[i].train_indices) & set(batches[j].train_indices)
            if overlap:
                overlaps.append((i, j, len(overlap)))
    if overlaps:
        print("  Train overlap between batches:")
        for i, j, n in overlaps:
            print(f"    Batch {i} & {j}: {n} shared train items")


if __name__ == "__main__":
    configure_cache("disk")

    # LoCoMo: 1:1 train/val ratio, 10 batches
    try:
        vibe_check("locomo", num_batches=10, val_size=272)
    except Exception as e:
        print(f"\nLoCoMo skipped: {e}")

    # ALFWorld: 1:1 train/val ratio, 7 batches
    try:
        vibe_check("alfworld", num_batches=7, val_size=50)
    except Exception as e:
        print(f"\nALFWorld skipped: {e}")
