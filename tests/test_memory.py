"""
Phase 1: 四層記憶架構 + 偏好學習 測試

涵蓋：
  - VectorSearch: cosine_similarity + rank_entries
  - MemoryManager: Layer 2 retrieve (without real API), Layer 3 preference, Layer 4 episodic
  - Repository: PreferenceMemoryTable, EpisodicMemoryTable, EditSessionTable CRUD
  - _compute_diff_notes: diff analysis

Run:
    pytest tests/test_memory.py -v
"""

from __future__ import annotations

import json
import math
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kachu.memory.vector_search import cosine_similarity, rank_entries
from kachu.memory.manager import MemoryManager, _compute_diff_notes


# ── vector_search unit tests ──────────────────────────────────────────────────


def test_cosine_similarity_identical() -> None:
    v = [1.0, 0.0, 0.0]
    assert cosine_similarity(v, v) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal() -> None:
    a = [1.0, 0.0]
    b = [0.0, 1.0]
    assert cosine_similarity(a, b) == pytest.approx(0.0)


def test_cosine_similarity_opposite() -> None:
    a = [1.0, 0.0]
    b = [-1.0, 0.0]
    assert cosine_similarity(a, b) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector() -> None:
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_empty() -> None:
    assert cosine_similarity([], [1.0]) == 0.0


def test_cosine_similarity_length_mismatch() -> None:
    assert cosine_similarity([1.0, 0.0], [1.0]) == 0.0


def test_rank_entries_returns_top_k() -> None:
    query = [1.0, 0.0]
    entries = [
        {"id": "a", "content": "a", "embedding": [0.0, 1.0]},   # orthogonal
        {"id": "b", "content": "b", "embedding": [1.0, 0.0]},   # identical
        {"id": "c", "content": "c", "embedding": [0.7, 0.7]},   # partial match
    ]
    ranked = rank_entries(query, entries, top_k=2)
    assert len(ranked) == 2
    assert ranked[0]["id"] == "b"   # highest similarity
    assert ranked[0]["_score"] == pytest.approx(1.0)
    assert ranked[1]["id"] == "c"   # second highest


def test_rank_entries_empty_query_preserves_order() -> None:
    """When query_embedding is empty, order is preserved with score 0."""
    entries = [
        {"id": "x", "content": "x", "embedding": [1.0, 0.0]},
        {"id": "y", "content": "y", "embedding": [0.0, 1.0]},
    ]
    ranked = rank_entries([], entries, top_k=5)
    assert len(ranked) == 2
    assert all(e["_score"] == 0.0 for e in ranked)


def test_rank_entries_entry_without_embedding() -> None:
    """Entries without embeddings get score 0.0 and are ranked last."""
    query = [1.0, 0.0]
    entries = [
        {"id": "no-emb", "content": "no embedding", "embedding": []},
        {"id": "has-emb", "content": "has embedding", "embedding": [1.0, 0.0]},
    ]
    ranked = rank_entries(query, entries, top_k=5)
    assert ranked[0]["id"] == "has-emb"
    assert ranked[1]["_score"] == 0.0


# ── _compute_diff_notes ───────────────────────────────────────────────────────


def test_diff_notes_no_change() -> None:
    assert _compute_diff_notes("abc", "abc") == "無修改"


def test_diff_notes_large_addition() -> None:
    original = "短文字"
    edited = "短文字" + "增加很多內容讓文字變長" * 5
    notes = _compute_diff_notes(original, edited)
    assert "補充" in notes


def test_diff_notes_large_deletion() -> None:
    original = "很長的文字" * 20
    edited = "很短的文字"
    notes = _compute_diff_notes(original, edited)
    assert "縮短" in notes


def test_diff_notes_emoji_added() -> None:
    notes = _compute_diff_notes("普通文字", "普通文字😊")
    assert "emoji" in notes


def test_diff_notes_opening_changed() -> None:
    notes = _compute_diff_notes("A開頭", "B開頭")
    assert "開頭" in notes


# ── MemoryManager: Layer 3 preference ────────────────────────────────────────

@pytest.fixture
def memory_manager_with_mock_repo() -> tuple[MemoryManager, MagicMock]:
    mock_repo = MagicMock()
    mock_settings = MagicMock()
    mock_settings.OPENAI_API_KEY = ""  # No real API key → no embeddings
    manager = MemoryManager(repo=mock_repo, settings=mock_settings)
    return manager, mock_repo


def test_store_preference_saves_to_repo(memory_manager_with_mock_repo) -> None:
    manager, mock_repo = memory_manager_with_mock_repo
    manager.store_preference(
        tenant_id="t1",
        platform="ig_fb",
        original_draft="原版內容",
        edited_draft="修改後的內容，加了很多字讓它變更長",
        run_id="run-001",
    )
    mock_repo.save_preference_memory.assert_called_once()
    kwargs = mock_repo.save_preference_memory.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["platform"] == "ig_fb"
    assert kwargs["original_draft"] == "原版內容"
    assert len(kwargs["diff_notes"]) > 0


def test_get_preference_examples_returns_formatted_list(memory_manager_with_mock_repo) -> None:
    manager, mock_repo = memory_manager_with_mock_repo
    mock_repo.get_preference_memories.return_value = [
        MagicMock(
            content=json.dumps({
                "original": "原版",
                "edited": "修改版",
                "diff_notes": "老闆調整了用詞",
            }),
        )
    ]
    examples = manager.get_preference_examples("t1", "ig_fb")
    assert len(examples) == 1
    assert examples[0]["original"] == "原版"
    assert examples[0]["edited"] == "修改版"
    assert examples[0]["notes"] == "老闆調整了用詞"


# ── MemoryManager: Layer 4 episodic ──────────────────────────────────────────


def test_record_episode_saves_to_repo(memory_manager_with_mock_repo) -> None:
    manager, mock_repo = memory_manager_with_mock_repo
    manager.record_episode(
        tenant_id="t1",
        workflow_type="photo_content",
        outcome="approved_published",
        context_summary={"run_id": "run-001"},
    )
    mock_repo.save_episodic_memory.assert_called_once()
    kwargs = mock_repo.save_episodic_memory.call_args.kwargs
    assert kwargs["tenant_id"] == "t1"
    assert kwargs["outcome"] == "approved_published"
    parsed = json.loads(kwargs["context_summary"])
    assert parsed["run_id"] == "run-001"


def test_get_recent_episodes_formatted(memory_manager_with_mock_repo) -> None:
    from datetime import datetime, timezone
    manager, mock_repo = memory_manager_with_mock_repo
    mock_repo.get_episodic_memories.return_value = [
        MagicMock(
            content=json.dumps({"workflow_type": "photo_content", "outcome": "edited"}),
            created_at=datetime(2026, 4, 26, tzinfo=timezone.utc),
        )
    ]
    episodes = manager.get_recent_episodes("t1")
    assert len(episodes) == 1
    assert episodes[0]["outcome"] == "edited"
    assert "2026" in episodes[0]["created_at"]


# ── MemoryManager: Layer 2 retrieve (no embedding) ───────────────────────────


@pytest.mark.asyncio
async def test_retrieve_relevant_knowledge_no_api_key(memory_manager_with_mock_repo) -> None:
    """Without voyage_api_key, returns all entries with score 0 (keyword fallback)."""
    manager, mock_repo = memory_manager_with_mock_repo

    from kachu.persistence.tables import KnowledgeEntryTable
    mock_entries = [
        MagicMock(spec=KnowledgeEntryTable, id="k1", category="core_value", content="誠信", embedding=None),
        MagicMock(spec=KnowledgeEntryTable, id="k2", category="goal", content="增加客流", embedding=None),
    ]
    mock_repo.get_knowledge_entries.return_value = mock_entries

    results = await manager.retrieve_relevant_knowledge(tenant_id="t1", query="誠信服務")
    assert len(results) == 2
    assert all(e["_score"] == 0.0 for e in results)


@pytest.mark.asyncio
async def test_retrieve_relevant_knowledge_with_embeddings(memory_manager_with_mock_repo) -> None:
    """With stored embeddings, semantic ranking is applied."""
    manager, mock_repo = memory_manager_with_mock_repo
    manager._settings.OPENAI_API_KEY = "fake-key"

    from kachu.persistence.tables import KnowledgeEntryTable
    mock_entries = [
        MagicMock(
            spec=KnowledgeEntryTable,
            id="k1", category="core_value", content="誠信",
            embedding=json.dumps([1.0, 0.0]),
        ),
        MagicMock(
            spec=KnowledgeEntryTable,
            id="k2", category="goal", content="增加客流",
            embedding=json.dumps([0.0, 1.0]),
        ),
    ]
    mock_repo.get_knowledge_entries.return_value = mock_entries

    # Mock get_embedding to return a vector similar to k1
    with patch("kachu.memory.manager.get_embedding", new=AsyncMock(return_value=[1.0, 0.0])):
        results = await manager.retrieve_relevant_knowledge(tenant_id="t1", query="誠信")

    assert results[0]["id"] == "k1"
    assert results[0]["_score"] == pytest.approx(1.0)


# ── Repository: EditSession CRUD ──────────────────────────────────────────────


def test_edit_session_lifecycle() -> None:
    """Create → advance → complete an EditSession in an in-memory DB."""
    from sqlmodel import SQLModel, create_engine
    from sqlalchemy.pool import StaticPool
    from kachu.persistence.repository import KachuRepository

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repo = KachuRepository(engine)

    # No active session yet
    assert repo.get_active_edit_session("tenant-edit") is None

    # Create
    s = repo.create_edit_session(
        tenant_id="tenant-edit",
        run_id="run-edit-001",
        ig_draft="IG 原版",
        google_draft="Google 原版",
    )
    assert s.step == "waiting_ig"

    # get_active returns it
    active = repo.get_active_edit_session("tenant-edit")
    assert active is not None
    assert active.id == s.id

    # Advance to waiting_google
    repo.advance_edit_session(s.id, "waiting_google")
    active2 = repo.get_active_edit_session("tenant-edit")
    assert active2.step == "waiting_google"

    # Complete
    repo.complete_edit_session(s.id)
    assert repo.get_active_edit_session("tenant-edit") is None


# ── Repository: PreferenceMemory + EpisodicMemory CRUD ───────────────────────


def test_preference_and_episodic_memory_crud() -> None:
    from sqlmodel import SQLModel, create_engine
    from sqlalchemy.pool import StaticPool
    from kachu.persistence.repository import KachuRepository

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repo = KachuRepository(engine)

    # Preference
    p = repo.save_preference_memory(
        tenant_id="t-pref",
        platform="ig_fb",
        original_draft="原版",
        edited_draft="修改版",
        diff_notes="老闆改了開頭",
        run_id="r-001",
    )
    prefs = repo.get_preference_memories("t-pref", platform="ig_fb")
    assert len(prefs) == 1
    assert prefs[0].id == p.id

    # Episodic
    e = repo.save_episodic_memory(
        tenant_id="t-ep",
        workflow_type="photo_content",
        outcome="rejected",
        context_summary='{"run_id": "r-002"}',
    )
    episodes = repo.get_episodic_memories("t-ep", workflow_type="photo_content")
    assert len(episodes) == 1
    assert json.loads(episodes[0].content)["outcome"] == "rejected"
