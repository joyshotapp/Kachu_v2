from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from .embedder import get_embedding
from .vector_search import rank_entries

if TYPE_CHECKING:
    from ..config import Settings
    from ..persistence import KachuRepository

logger = logging.getLogger(__name__)


class MemoryManager:
    """Unified interface to Kachu's four-layer memory.

    Layer 1 – Raw Memory:
        Every boss/customer conversation is logged in ``ConversationTable``
        (managed directly by ``OnboardingFlow`` and the webhook handler).

    Layer 2 – Structured Memory:
        Categorised knowledge entries (``KnowledgeEntryTable``) enriched with
        OpenAI text-embedding-3-small vectors for semantic retrieval.

    Layer 3 – Preference Memory:
        Boss edit diffs stored in ``KnowledgeEntryTable`` (category='preference').
        When the boss taps "✏️ 我要修改" and types a corrected version, the
        original↔edited pair is stored as JSON content and injected as few-shot
        examples on the next generation.

    Layer 4 – Episodic Memory:
        Workflow outcomes stored in ``KnowledgeEntryTable`` (category='episode').
        Every approval, rejection, or edit action is recorded so the system can
        later surface success patterns and avoid repeating rejected approaches.
    """

    def __init__(self, repo: "KachuRepository", settings: "Settings") -> None:
        self._repo = repo
        self._settings = settings

    # ── Layer 2: Structured Memory ────────────────────────────────────────────

    async def store_knowledge(
        self,
        *,
        tenant_id: str,
        category: str,
        content: str,
        source_type: str = "conversation",
        source_id: str | None = None,
    ) -> None:
        """Persist a knowledge entry and (if configured) enrich it with an embedding."""
        entry = self._repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category=category,
            content=content,
            source_type=source_type,
            source_id=source_id,
        )
        if self._settings.OPENAI_API_KEY:
            embedding = await get_embedding(content, self._settings.OPENAI_API_KEY)
            if embedding:
                self._repo.update_knowledge_entry_embedding(entry.id, json.dumps(embedding))

    async def retrieve_relevant_knowledge(
        self,
        *,
        tenant_id: str,
        query: str,
        top_k: int = 8,
    ) -> list[dict]:
        """Semantic search over a tenant's knowledge base.

        Falls back to returning all entries (up to *top_k*) when VoyageAI is
        not configured or returns an empty embedding.
        """
        all_entries = self._repo.get_knowledge_entries(tenant_id)
        if not all_entries:
            return []

        query_embedding = await get_embedding(query, self._settings.OPENAI_API_KEY)

        entry_dicts: list[dict] = []
        for e in all_entries:
            emb: list[float] = []
            if e.embedding:
                try:
                    emb = json.loads(e.embedding)
                except (json.JSONDecodeError, TypeError):
                    pass
            entry_dicts.append(
                {
                    "id": e.id,
                    "category": e.category,
                    "content": e.content,
                    "embedding": emb,
                }
            )

        return rank_entries(query_embedding, entry_dicts, top_k=top_k)

    # ── Layer 3: Preference Memory ────────────────────────────────────────────

    def store_preference(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        run_id: str = "",
    ) -> None:
        """Record a boss edit as a style/preference signal."""
        diff_notes = _compute_diff_notes(original_draft, edited_draft)
        self._repo.save_preference_memory(
            tenant_id=tenant_id,
            platform=platform,
            original_draft=original_draft,
            edited_draft=edited_draft,
            diff_notes=diff_notes,
            run_id=run_id,
        )
        logger.info("Stored preference: tenant=%s platform=%s", tenant_id, platform)

    def get_preference_examples(
        self,
        tenant_id: str,
        platform: str,
        limit: int = 3,
    ) -> list[dict]:
        """Return recent boss edits as few-shot style examples.

        Reads from KnowledgeEntryTable (category='preference'); content is a
        JSON blob: {platform, original, edited, diff_notes, run_id}.
        """
        prefs = self._repo.get_preference_memories(tenant_id, platform=platform, limit=limit)
        results: list[dict] = []
        for p in prefs:
            try:
                data = json.loads(p.content)
                results.append(
                    {
                        "original": data.get("original", ""),
                        "edited": data.get("edited", ""),
                        "notes": data.get("diff_notes", ""),
                    }
                )
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        return results

    # ── Layer 4: Episodic Memory ──────────────────────────────────────────────

    def record_episode(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: dict,
    ) -> None:
        """Record a workflow outcome (approved / rejected / edited)."""
        self._repo.save_episodic_memory(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome=outcome,
            context_summary=json.dumps(context_summary, ensure_ascii=False),
        )
        logger.info(
            "Episode recorded: tenant=%s workflow=%s outcome=%s",
            tenant_id,
            workflow_type,
            outcome,
        )

    def get_recent_episodes(
        self,
        tenant_id: str,
        workflow_type: str | None = None,
        limit: int = 5,
    ) -> list[dict]:
        """Return recent workflow episodes.

        Reads from KnowledgeEntryTable (category='episode'); content is a
        JSON blob: {workflow_type, outcome, context_summary}.
        """
        episodes = self._repo.get_episodic_memories(
            tenant_id, workflow_type=workflow_type, limit=limit
        )
        results: list[dict] = []
        for e in episodes:
            try:
                data = json.loads(e.content)
                results.append(
                    {
                        "workflow_type": data.get("workflow_type", ""),
                        "outcome": data.get("outcome", ""),
                        "created_at": e.created_at.isoformat(),
                    }
                )
            except (json.JSONDecodeError, TypeError, AttributeError):
                pass
        return results


# ── Diff analysis (no LLM dependency) ────────────────────────────────────────


def _compute_diff_notes(original: str, edited: str) -> str:
    """Lightweight diff analysis to annotate what the boss changed."""
    if original == edited:
        return "無修改"

    notes: list[str] = []
    delta = len(edited) - len(original)

    if delta > 50:
        notes.append("老闆補充了更多內容")
    elif delta < -50:
        notes.append("老闆大幅縮短文字")
    else:
        notes.append("老闆調整了用詞")

    emoji_chars = {"😊", "🎉", "✨", "🍜", "🏪", "💪", "❤️", "⭐"}
    added_emojis = emoji_chars & (set(edited) - set(original))
    if added_emojis:
        notes.append("老闆加了 emoji")

    exclaim_added = edited.count("！") > original.count("！")
    if exclaim_added:
        notes.append("老闆加了感嘆號")

    if original and edited and original[0] != edited[0]:
        notes.append("老闆修改了開頭")

    if "#" in edited and "#" not in original:
        notes.append("老闆加了 hashtag")

    return "；".join(notes) if notes else "老闆微調了文字"
