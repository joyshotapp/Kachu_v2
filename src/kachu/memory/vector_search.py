from __future__ import annotations

import math


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two equal-length vectors.

    Returns 0.0 for empty, mismatched-length, or zero-magnitude vectors.
    Pure Python — no external dependencies.
    """
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(y * y for y in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


def rank_entries(
    query_embedding: list[float],
    entries: list[dict],
    top_k: int = 8,
) -> list[dict]:
    """Rank *entries* by cosine similarity to *query_embedding*.

    Each entry dict should have an ``"embedding"`` key (list[float]).
    Returns at most *top_k* entries sorted by descending similarity,
    with an added ``"_score"`` key.

    When *query_embedding* is empty (no API key / failure), every entry receives
    score 0.0 and the original ordering is preserved — this ensures the caller
    always gets a usable list regardless of embedding availability.
    """
    scored: list[dict] = []
    for entry in entries:
        emb: list[float] = entry.get("embedding") or []
        score = cosine_similarity(query_embedding, emb) if query_embedding and emb else 0.0
        scored.append({**entry, "_score": score})
    scored.sort(key=lambda x: x["_score"], reverse=True)
    return scored[:top_k]
