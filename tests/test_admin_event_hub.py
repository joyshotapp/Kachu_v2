from __future__ import annotations

import json
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.admin import router as admin_router
from kachu_plus.approval import ApprovalBridge
from kachu_plus.config import Settings
from kachu_plus.learning import ContextBriefManager, MemoryManager, PostTaskReviewService
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import TenantTable
from kachu_plus.services import AgentOSTaskDispatcher


class _FakeAgentOSClient:
    async def aclose(self):
        return None


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed(repo: KachuPlusRepository) -> None:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="測試店", industry_type="cafe", address="台北市信義區"))
        session.commit()


def _make_app(repo: KachuPlusRepository) -> FastAPI:
    app = FastAPI()
    settings = Settings()
    settings.ADMIN_API_TOKEN = "admin-token"
    app.state.repository = repo
    app.state.settings = settings
    app.state.consultant = AsyncMock()
    app.state.consultant.build_reply = AsyncMock(return_value="謝謝你的留言，我們會盡快協助你。")
    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)
    post_task_review = PostTaskReviewService(repo, memory, briefs)
    dispatcher = AgentOSTaskDispatcher(settings, client=_FakeAgentOSClient())
    app.state.execute_dispatcher = dispatcher
    app.state.approval_bridge = ApprovalBridge(dispatcher, repo, post_task_review)
    app.include_router(admin_router)
    return app


def _auth_headers() -> dict[str, str]:
    return {"Authorization": "Bearer admin-token"}


def test_admin_can_list_and_get_webhook_events() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:comment-admin-1",
        event_type="comment",
        raw_payload={
            "comment_id": "comment-admin-1",
            "post_id": "post-1",
            "message": "請問今天營業到幾點？",
            "from": {"id": "user-1", "name": "王小姐"},
        },
        external_event_id="comment-admin-1",
        external_user_id="user-1",
        external_thread_id="post-1",
    )
    event = repo.list_webhook_events("tenant-1", provider="meta")[0]
    client = TestClient(_make_app(repo))

    listed = client.get("/admin/tenants/tenant-1/events?provider=meta", headers=_auth_headers())
    assert listed.status_code == 200
    assert listed.json()["total"] == 1
    assert listed.json()["events"][0]["id"] == event.id
    assert "raw_payload" not in listed.json()["events"][0]

    detail = client.get(f"/admin/tenants/tenant-1/events/{event.id}", headers=_auth_headers())
    assert detail.status_code == 200
    assert detail.json()["event"]["external_event_id"] == "comment-admin-1"
    assert detail.json()["event"]["raw_payload"]["message"] == "請問今天營業到幾點？"


def test_admin_can_replay_meta_event_and_create_audit_trace() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:comment-replay-1",
        event_type="comment",
        raw_payload={
            "comment_id": "comment-replay-1",
            "post_id": "post-1",
            "message": "請問這週末有位子嗎？",
            "from": {"id": "user-2", "name": "林小姐"},
        },
        external_event_id="comment-replay-1",
        external_user_id="user-2",
        external_thread_id="post-1",
    )
    event = repo.list_webhook_events("tenant-1", provider="meta")[0]
    client = TestClient(_make_app(repo))

    replay = client.post(
        f"/admin/tenants/tenant-1/events/{event.id}/replay",
        headers=_auth_headers(),
        json={"note": "補發 pending approval"},
    )

    assert replay.status_code == 200
    assert replay.json()["replay"]["status"] == "queued"

    engagement = repo.get_external_engagement_by_message_id("comment-replay-1")
    assert engagement is not None
    assert engagement.status == "awaiting_approval"

    pending = repo.get_pending_approval_by_run_id("meta-engagement:comment-replay-1")
    assert pending is not None

    conversations = repo.list_recent_conversations(
        "tenant-1",
        actor_roles=["system"],
        conversation_kinds=["event_replay"],
    )
    assert len(conversations) == 1
    metadata = json.loads(conversations[0].metadata_json)
    assert metadata["webhook_event_id"] == event.id
    assert metadata["note"] == "補發 pending approval"
    assert metadata["replay"]["status"] == "queued"


def test_admin_can_replay_line_postback_event_and_update_suggestion() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="retention",
        title="喚回沉睡顧客",
        reason="30 天未互動",
        body="建議主動關懷顧客",
        suggested_action="send_message",
        draft_message="最近有新活動，歡迎回來看看。",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="line",
        dedupe_key=f"line:tenant-1:postback:event:{suggestion.id}",
        event_type="postback",
        raw_payload={
            "type": "postback",
            "timestamp": 1714895904000,
            "webhookEventId": suggestion.id,
            "source": {"type": "user", "userId": "U-owner-1"},
            "postback": {"data": f"action=suggestion_dismiss&suggestion_id={suggestion.id}"},
        },
        external_event_id=suggestion.id,
        external_user_id="U-owner-1",
        external_thread_id=f"action=suggestion_dismiss&suggestion_id={suggestion.id}",
    )
    event = repo.list_webhook_events("tenant-1", provider="line")[0]
    client = TestClient(_make_app(repo))

    replay = client.post(
        f"/admin/tenants/tenant-1/events/{event.id}/replay",
        headers=_auth_headers(),
        json={"note": "重放 suggestion dismiss postback"},
    )

    assert replay.status_code == 200
    assert replay.json()["replay"]["status"] == "replayed"
    assert replay.json()["replay"]["result"]["status"] == "processed"
    assert repo.get_suggestion(suggestion.id).status == "dismissed"

    conversations = repo.list_recent_conversations(
        "tenant-1",
        actor_roles=["system"],
        conversation_kinds=["event_replay"],
    )
    assert len(conversations) == 1
    metadata = json.loads(conversations[0].metadata_json)
    assert metadata["provider"] == "line"
    assert metadata["note"] == "重放 suggestion dismiss postback"
    assert metadata["replay"]["event_type"] == "postback"


def test_admin_can_batch_replay_events_for_backfill() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="retention",
        title="喚回沉睡顧客",
        reason="30 天未互動",
        body="建議主動關懷顧客",
        suggested_action="send_message",
        draft_message="最近有新活動，歡迎回來看看。",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:comment-batch-1",
        event_type="comment",
        raw_payload={
            "comment_id": "comment-batch-1",
            "post_id": "post-2",
            "message": "想問一下今天還有營業嗎？",
            "from": {"id": "user-9", "name": "蔡小姐"},
        },
        external_event_id="comment-batch-1",
        external_user_id="user-9",
        external_thread_id="post-2",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="line",
        dedupe_key=f"line:tenant-1:postback:event:batch:{suggestion.id}",
        event_type="postback",
        raw_payload={
            "type": "postback",
            "timestamp": 1714895904000,
            "webhookEventId": f"batch:{suggestion.id}",
            "source": {"type": "user", "userId": "U-owner-1"},
            "postback": {"data": f"action=suggestion_dismiss&suggestion_id={suggestion.id}"},
        },
        external_event_id=f"batch:{suggestion.id}",
        external_user_id="U-owner-1",
        external_thread_id=f"action=suggestion_dismiss&suggestion_id={suggestion.id}",
    )
    events = repo.list_webhook_events("tenant-1", limit=10)
    event_ids = [event.id for event in events]
    client = TestClient(_make_app(repo))

    replay = client.post(
        "/admin/tenants/tenant-1/events/replay-batch",
        headers=_auth_headers(),
        json={"event_ids": event_ids, "note": "批次補發 backlog"},
    )

    assert replay.status_code == 200
    assert replay.json()["requested_count"] == 2
    assert replay.json()["success_count"] == 2
    assert replay.json()["error_count"] == 0
    assert repo.get_external_engagement_by_message_id("comment-batch-1") is not None
    assert repo.get_suggestion(suggestion.id).status == "dismissed"

    conversations = repo.list_recent_conversations(
        "tenant-1",
        actor_roles=["system"],
        conversation_kinds=["event_replay"],
    )
    assert len(conversations) == 2
    notes = [json.loads(item.metadata_json)["note"] for item in conversations]
    assert notes == ["批次補發 backlog", "批次補發 backlog"]


def test_admin_can_preview_and_execute_query_based_replay() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    first = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="retention",
        title="喚回沉睡顧客 1",
        reason="30 天未互動",
        body="建議主動關懷顧客",
        suggested_action="send_message",
        draft_message="歡迎回來。",
    )
    second = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="retention",
        title="喚回沉睡顧客 2",
        reason="45 天未互動",
        body="建議主動關懷顧客",
        suggested_action="send_message",
        draft_message="最近有新活動。",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="line",
        dedupe_key=f"line:tenant-1:postback:event:query:{first.id}",
        event_type="postback",
        raw_payload={
            "type": "postback",
            "timestamp": 1714895904000,
            "webhookEventId": f"query:{first.id}",
            "source": {"type": "user", "userId": "U-owner-1"},
            "postback": {"data": f"action=suggestion_dismiss&suggestion_id={first.id}"},
        },
        external_event_id=f"query:{first.id}",
        external_user_id="U-owner-1",
        external_thread_id=f"action=suggestion_dismiss&suggestion_id={first.id}",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="line",
        dedupe_key=f"line:tenant-1:postback:event:query:{second.id}",
        event_type="postback",
        raw_payload={
            "type": "postback",
            "timestamp": 1714895905000,
            "webhookEventId": f"query:{second.id}",
            "source": {"type": "user", "userId": "U-owner-1"},
            "postback": {"data": f"action=suggestion_dismiss&suggestion_id={second.id}"},
        },
        external_event_id=f"query:{second.id}",
        external_user_id="U-owner-1",
        external_thread_id=f"action=suggestion_dismiss&suggestion_id={second.id}",
    )
    client = TestClient(_make_app(repo))

    preview = client.post(
        "/admin/tenants/tenant-1/events/replay-query",
        headers=_auth_headers(),
        json={"provider": "line", "event_type": "postback", "external_user_id": "U-owner-1", "limit": 1, "dry_run": True},
    )

    assert preview.status_code == 200
    assert preview.json()["mode"] == "dry_run"
    assert preview.json()["requested_count"] == 1
    assert repo.get_suggestion(first.id).status == "pending"
    assert repo.get_suggestion(second.id).status == "pending"

    execute = client.post(
        "/admin/tenants/tenant-1/events/replay-query",
        headers=_auth_headers(),
        json={"provider": "line", "event_type": "postback", "external_user_id": "U-owner-1", "limit": 1, "note": "query replay"},
    )

    assert execute.status_code == 200
    assert execute.json()["mode"] == "executed"
    assert execute.json()["requested_count"] == 1
    dismissed = sorted(
        [repo.get_suggestion(first.id).status, repo.get_suggestion(second.id).status]
    )
    assert dismissed == ["dismissed", "pending"]


def test_admin_query_replay_policy_missing_engagement_only_selects_unmaterialized_meta_events() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:policy-engagement-1",
        event_type="comment",
        raw_payload={
            "comment_id": "policy-engagement-1",
            "post_id": "post-1",
            "message": "第一則留言",
            "from": {"id": "user-1", "name": "王小姐"},
        },
        external_event_id="policy-engagement-1",
        external_user_id="user-1",
        external_thread_id="post-1",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:policy-engagement-2",
        event_type="comment",
        raw_payload={
            "comment_id": "policy-engagement-2",
            "post_id": "post-2",
            "message": "第二則留言",
            "from": {"id": "user-2", "name": "林小姐"},
        },
        external_event_id="policy-engagement-2",
        external_user_id="user-2",
        external_thread_id="post-2",
    )
    repo.create_external_engagement(
        tenant_id="tenant-1",
        platform="meta",
        engagement_type="comment",
        external_thread_id="post-1",
        external_message_id="policy-engagement-1",
        author_name="王小姐",
        author_id="user-1",
        content_text="第一則留言",
        source_payload={"comment_id": "policy-engagement-1"},
        status="awaiting_approval",
        reply_draft="已存在草稿",
        related_run_id="meta-engagement:policy-engagement-1",
    )
    client = TestClient(_make_app(repo))

    preview = client.post(
        "/admin/tenants/tenant-1/events/replay-query",
        headers=_auth_headers(),
        json={"provider": "meta", "event_type": "comment", "replay_policy": "missing_engagement", "dry_run": True},
    )

    assert preview.status_code == 200
    assert preview.json()["replay_policy"] == "missing_engagement"
    assert preview.json()["requested_count"] == 1
    assert preview.json()["selected_events"][0]["external_event_id"] == "policy-engagement-2"


def test_admin_query_replay_policy_missing_pending_approval_requeues_only_missing_runs() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:policy-pending-1",
        event_type="comment",
        raw_payload={
            "comment_id": "policy-pending-1",
            "post_id": "post-1",
            "message": "需要補 approval 的留言",
            "from": {"id": "user-1", "name": "王小姐"},
        },
        external_event_id="policy-pending-1",
        external_user_id="user-1",
        external_thread_id="post-1",
    )
    repo.record_webhook_event_if_new(
        tenant_id="tenant-1",
        provider="meta",
        dedupe_key="meta:tenant-1:comment:policy-pending-2",
        event_type="comment",
        raw_payload={
            "comment_id": "policy-pending-2",
            "post_id": "post-2",
            "message": "已有 approval 的留言",
            "from": {"id": "user-2", "name": "林小姐"},
        },
        external_event_id="policy-pending-2",
        external_user_id="user-2",
        external_thread_id="post-2",
    )
    missing_pending = repo.create_external_engagement(
        tenant_id="tenant-1",
        platform="meta",
        engagement_type="comment",
        external_thread_id="post-1",
        external_message_id="policy-pending-1",
        author_name="王小姐",
        author_id="user-1",
        content_text="需要補 approval 的留言",
        source_payload={"comment_id": "policy-pending-1"},
        status="awaiting_approval",
        reply_draft="補 approval",
        related_run_id="meta-engagement:policy-pending-1",
    )
    existing_pending = repo.create_external_engagement(
        tenant_id="tenant-1",
        platform="meta",
        engagement_type="comment",
        external_thread_id="post-2",
        external_message_id="policy-pending-2",
        author_name="林小姐",
        author_id="user-2",
        content_text="已有 approval 的留言",
        source_payload={"comment_id": "policy-pending-2"},
        status="awaiting_approval",
        reply_draft="已有 approval",
        related_run_id="meta-engagement:policy-pending-2",
    )
    repo.save_pending_approval(
        tenant_id="tenant-1",
        agentos_task_id=existing_pending.id,
        agentos_run_id="meta-engagement:policy-pending-2",
        workflow_type="kachu_meta_reply",
        draft_content=json.dumps({"engagement_id": existing_pending.id, "reply_draft": "已有 approval"}, ensure_ascii=False),
    )
    client = TestClient(_make_app(repo))

    execute = client.post(
        "/admin/tenants/tenant-1/events/replay-query",
        headers=_auth_headers(),
        json={"provider": "meta", "event_type": "comment", "replay_policy": "missing_pending_approval", "note": "補 pending approval"},
    )

    assert execute.status_code == 200
    assert execute.json()["replay_policy"] == "missing_pending_approval"
    assert execute.json()["requested_count"] == 1
    assert execute.json()["success_count"] == 1
    pending = repo.get_pending_approval_by_run_id("meta-engagement:policy-pending-1")
    assert pending is not None
    assert pending.agentos_task_id == missing_pending.id
    untouched = repo.get_pending_approval_by_run_id("meta-engagement:policy-pending-2")
    assert untouched is not None