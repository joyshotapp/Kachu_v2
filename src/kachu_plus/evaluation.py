from __future__ import annotations

import re
from typing import Any


def _safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def compute_follow_up_routing_accuracy(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    correct = sum(1 for case in cases if bool(case.get("matched")))
    return {
        "total": total,
        "correct": correct,
        "accuracy": _safe_ratio(correct, total),
    }


def compute_retrieval_hit_rate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    hits = 0
    for case in cases:
        expected = set(str(value) for value in case.get("expected_ids", []) if str(value))
        retrieved = [str(value) for value in case.get("retrieved_ids", []) if str(value)]
        if expected and any(value in expected for value in retrieved):
            hits += 1
    return {
        "total": total,
        "hits": hits,
        "hit_rate": _safe_ratio(hits, total),
    }


def compute_memory_promotion_scores(cases: list[dict[str, Any]]) -> dict[str, Any]:
    true_positive = 0
    false_positive = 0
    false_negative = 0
    for case in cases:
        predicted = set(str(value) for value in case.get("predicted", []) if str(value))
        expected = set(str(value) for value in case.get("expected", []) if str(value))
        true_positive += len(predicted & expected)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
    precision = _safe_ratio(true_positive, true_positive + false_positive)
    recall = _safe_ratio(true_positive, true_positive + false_negative)
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
    }


def compute_consult_groundedness(reply: str, context_bundle: dict[str, Any]) -> dict[str, Any]:
    reply_text = str(reply or "")
    knowledge = [str(item or "") for item in context_bundle.get("relevant_knowledge", [])[:3]]
    conversation_turns = [
        str(item.get("content_text", "") or "")
        for item in context_bundle.get("recent_conversations", [])[:3]
        if isinstance(item, dict)
    ]
    sources = [value for value in knowledge + conversation_turns if value]
    matched = 0
    for source in sources:
        tokens = [token for token in re.split(r"[\s，。！？；,:]+", source) if len(token) >= 2]
        if any(token in reply_text for token in tokens[:4]):
            matched += 1
    return {
        "source_count": len(sources),
        "matched_sources": matched,
        "groundedness": _safe_ratio(matched, len(sources)) if sources else 0.0,
    }


def compute_preference_reuse_rate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    reused = 0
    for case in cases:
        generated = str(case.get("generated_text", "") or "")
        phrases = [str(value) for value in case.get("preference_phrases", []) if str(value)]
        if phrases and any(phrase in generated for phrase in phrases):
            reused += 1
    return {
        "total": total,
        "reused": reused,
        "reuse_rate": _safe_ratio(reused, total),
    }


def compute_task_follow_up_success_rate(cases: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(cases)
    success = sum(1 for case in cases if bool(case.get("resolved")))
    return {
        "total": total,
        "success": success,
        "success_rate": _safe_ratio(success, total),
    }