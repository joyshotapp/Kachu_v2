from __future__ import annotations

import re
from typing import Any

from kachu_plus.learning import MemoryManager

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]{2,}")


def _query_terms(text: str) -> list[str]:
    normalized = str(text or "").strip()
    if not normalized:
        return []

    seen: set[str] = set()
    terms: list[str] = []
    for token in _TOKEN_PATTERN.findall(normalized):
        value = token.strip().lower()
        if len(value) < 2 or value in seen:
            continue
        seen.add(value)
        terms.append(value)
    if normalized.lower() not in seen:
        terms.append(normalized.lower())
    return terms[:12]


def _match_score(query: str, candidate: str, terms: list[str]) -> float:
    query_value = str(query or "").strip().lower()
    candidate_value = str(candidate or "").strip().lower()
    if not candidate_value:
        return 0.0

    score = 0.0
    if query_value and query_value in candidate_value:
        score += 4.0
    for term in terms:
        if term and term in candidate_value:
            score += 1.5 if len(term) >= 4 else 1.0
    if not score and any(char in candidate_value for char in query_value[:4]):
        score += 0.2
    return score


def _serialize_conversation(row: Any, *, score: float) -> dict[str, Any]:
    return {
        "id": str(getattr(row, "id", "") or ""),
        "actor_role": str(getattr(row, "actor_role", "") or ""),
        "conversation_kind": str(getattr(row, "conversation_kind", "") or ""),
        "content_text": str(getattr(row, "content_text", "") or ""),
        "related_task_id": str(getattr(row, "related_task_id", "") or ""),
        "related_run_id": str(getattr(row, "related_run_id", "") or ""),
        "score": round(score, 3),
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else "",
    }


def _serialize_knowledge(row: Any, *, score: float) -> dict[str, Any]:
    return {
        "id": str(getattr(row, "id", "") or ""),
        "category": str(getattr(row, "category", "") or ""),
        "content": str(getattr(row, "content", "") or ""),
        "source_conversation_id": str(getattr(row, "source_conversation_id", "") or ""),
        "confidence_score": float(getattr(row, "confidence_score", 1.0) or 1.0),
        "score": round(score, 3),
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else "",
    }


def _serialize_episode(row: dict[str, Any], *, score: float) -> dict[str, Any]:
    payload = dict(row)
    payload["score"] = round(score, 3)
    return payload


class RetrievalPlanComposer:
    def __init__(self, repo: Any, memory: MemoryManager) -> None:
        self._repo = repo
        self._memory = memory

    def compose(
        self,
        *,
        tenant_id: str,
        query: str,
        line_user_id: str = "",
        workflow_type: str = "",
        platform: str = "google",
    ) -> dict[str, Any]:
        terms = _query_terms(query)
        recent_conversations = self._repo.list_recent_conversations(
            tenant_id,
            limit=10,
            line_user_id=line_user_id or None,
        )
        active_task = (
            self._repo.get_latest_execute_task_record(tenant_id=tenant_id, line_user_id=line_user_id)
            if line_user_id
            else self._repo.get_latest_execute_task_for_tenant(tenant_id)
        )
        knowledge_entries = self._repo.list_knowledge_entries(tenant_id, limit=50)
        preference_examples = self._memory.get_preference_examples(tenant_id, platform, limit=5)
        episodes = self._memory.get_recent_episodes(tenant_id, workflow_type=workflow_type or None, limit=5)

        ranked_conversations = sorted(
            (
                _serialize_conversation(
                    row,
                    score=_match_score(query, getattr(row, "content_text", ""), terms) + 0.2,
                )
                for row in recent_conversations
            ),
            key=lambda item: (item["score"], item["created_at"]),
            reverse=True,
        )[:6]

        ranked_knowledge = sorted(
            (
                _serialize_knowledge(
                    row,
                    score=_match_score(query, getattr(row, "content", ""), terms)
                    + (0.5 if str(getattr(row, "status", "active") or "active") == "active" else 0.0)
                    + float(getattr(row, "confidence_score", 1.0) or 1.0) * 0.2,
                )
                for row in knowledge_entries
            ),
            key=lambda item: (item["score"], item["created_at"]),
            reverse=True,
        )[:8]

        ranked_preferences = []
        for row in preference_examples:
            note_text = " ".join(str(row.get(key, "") or "") for key in ("original", "edited", "notes"))
            ranked_preferences.append({
                **row,
                "score": round(_match_score(query, note_text, terms) + 0.1, 3),
            })
        ranked_preferences.sort(key=lambda item: item["score"], reverse=True)

        ranked_episodes = sorted(
            (
                _serialize_episode(
                    row,
                    score=_match_score(query, str(row.get("outcome", "") or ""), terms)
                    + (0.5 if workflow_type and row.get("workflow_type") == workflow_type else 0.0),
                )
                for row in episodes
            ),
            key=lambda item: (item["score"], item.get("created_at", "")),
            reverse=True,
        )

        active_task_state = {
            "task_id": str(getattr(active_task, "task_id", "") or "") if active_task is not None else "",
            "run_id": str(getattr(active_task, "run_id", "") or "") if active_task is not None else "",
            "intent_label": str(getattr(active_task, "intent_label", "") or "") if active_task is not None else "",
            "status": str(getattr(active_task, "status", "") or "") if active_task is not None else "",
            "objective": str(getattr(active_task, "objective", "") or "") if active_task is not None else "",
            "source_text": str(getattr(active_task, "source_text", "") or "") if active_task is not None else "",
            "related_conversation_id": str(getattr(active_task, "related_conversation_id", "") or "") if active_task is not None else "",
        }

        return {
            "query": query,
            "query_terms": terms,
            "recent_conversations": ranked_conversations,
            "active_task_state": active_task_state,
            "persistent_knowledge": ranked_knowledge,
            "preference_examples": ranked_preferences,
            "episodes": ranked_episodes,
        }