from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.approval import ApprovalBridge, PendingApprovalSyncService
from kachu_plus.config import Settings
from kachu_plus.crypto import encrypt_field
from kachu_plus.google_business import GoogleBusinessConnectorError
from kachu_plus.line.webhook import router as line_router
from kachu_plus.learning import ContextBriefManager, MemoryManager, PostTaskReviewService
from kachu_plus.meta import router as meta_router
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import LineChannelConfigTable, TenantTable
from kachu_plus.services import AgentOSTaskDispatcher
from kachu_plus.approval import ScheduledApprovalService
from kachu_plus.content_plans import ContentPlanService
from kachu_plus.tools_router import router as tools_router


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class _FakeGBPClient:
    def __init__(self, *, access_token: str = "") -> None:
        self.access_token = access_token

    def get_review(self, account_id: str, location_id: str, review_id: str):
        return {
            "reviewId": review_id,
            "starRating": "ONE",
            "comment": "這次等太久，有點失望",
            "reviewer": {"displayName": "林小姐"},
            "createTime": "2026-05-09T03:00:00Z",
        }

    def post_reply(self, account_id: str, location_id: str, review_id: str, reply_text: str):
        return {"reviewId": review_id, "comment": reply_text}

    def create_local_post(self, account_id: str, location_id: str, summary: str, call_to_action_url: str = ""):
        return {"name": "localPosts/1", "summary": summary}


class _FakeAgentOSClient:
    def __init__(self) -> None:
        self.decisions: list[dict] = []

    async def create_task(self, payload):
        raise NotImplementedError

    async def get_task(self, task_id: str):
        raise NotImplementedError

    async def run_task(self, task_id: str):
        raise NotImplementedError

    async def get_run(self, run_id: str):
        return type("RunView", (), {"run": {"id": run_id, "status": "waiting_approval"}, "approvals": [{"id": "approval-1", "decision": "pending"}], "checkpoints": [], "run_state": {}})()

    async def list_pending_approvals(self):
        return []

    async def decide_approval(self, approval_id, decision):
        self.decisions.append({"approval_id": approval_id, "decision": decision.model_dump(exclude_none=True)})
        return type("RunView", (), {"run": {"id": "run-1", "status": "running"}, "approvals": [], "checkpoints": [], "run_state": {}})()

    async def get_pending_approval_id_for_run(self, run_id: str):
        return "approval-1"

    async def aclose(self):
        return None


class _FakeMetaClient:
    def __init__(self, *, access_token: str = "", fb_page_id: str = "", ig_user_id: str = "", fb_access_token: str = "") -> None:
        self.access_token = access_token
        self.fb_page_id = fb_page_id
        self.ig_user_id = ig_user_id
        self.fb_access_token = fb_access_token

    def reply_to_comment(self, *, comment_id: str, message: str):
        return {"id": f"reply:{comment_id}", "message": message}

    def send_page_message(self, *, recipient_id: str, message: str):
        return {"message_id": f"mid:{recipient_id}", "message": message}


def _seed(repo: KachuPlusRepository) -> None:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="測試店", industry_type="cafe", address="台北市信義區"))
        session.add(
            LineChannelConfigTable(
                id="cfg-1",
                tenant_id="tenant-1",
                channel_secret="secret",
                channel_access_token="token",
                channel_id="line-channel-1",
            )
        )
        session.commit()


def _make_app(repo: KachuPlusRepository) -> FastAPI:
    app = FastAPI()
    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"
    settings.GOOGLE_AI_API_KEY = ""
    settings.OPENAI_API_KEY = ""
    settings.LITELLM_MODEL = "gemini/gemini-2.0-flash"
    app.state.repository = repo
    app.state.settings = settings
    app.state.consultant = AsyncMock()
    app.state.consultant.build_reply = AsyncMock(return_value="謝謝你的回饋，我們會持續改善，歡迎再來。")
    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)
    post_task_review = PostTaskReviewService(repo, memory, briefs)
    dispatcher = AgentOSTaskDispatcher(settings, client=_FakeAgentOSClient())
    app.state.execute_dispatcher = dispatcher
    app.state.approval_bridge = ApprovalBridge(dispatcher, repo, post_task_review)
    from kachu_plus.google_business import GoogleReviewService

    app.state.google_review_service = GoogleReviewService(
        repo,
        settings,
        client_factory=lambda **kwargs: _FakeGBPClient(**kwargs),
    )
    app.include_router(tools_router)
    app.include_router(line_router)
    app.include_router(meta_router)
    return app


def test_review_reply_tools_and_approval_bridge_flow() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        account_label="GBP",
        credentials_json=json.dumps({"account_id": "123", "location_id": "locations/456", "access_token": "token", "expires_at": 9999999999}),
    )
    client = TestClient(_make_app(repo))

    fetched = client.post("/tools/fetch-review", json={"tenant_id": "tenant-1", "review_id": "review-1"})
    assert fetched.status_code == 200
    sentiment = client.post("/tools/analyze-sentiment", json={"tenant_id": "tenant-1", "review": fetched.json()})
    assert sentiment.status_code == 200
    assert sentiment.json()["sentiment"] == "negative"

    context = client.post("/tools/retrieve-context", json={"tenant_id": "tenant-1", "query": "負評處理", "workflow_type": "kachu_review_reply"})
    assert context.status_code == 200
    draft = client.post(
        "/tools/generate-review-reply",
        json={
            "tenant_id": "tenant-1",
            "review": fetched.json(),
            "context": context.json(),
            "sentiment": sentiment.json(),
        },
    )
    assert draft.status_code == 200

    notified = client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-1",
            "task_id": "task-1",
            "workflow": "kachu_review_reply",
            "review_id": "review-1",
            "drafts": {"reply_draft": draft.json()["reply_draft"], "sentiment": sentiment.json()},
        },
    )
    assert notified.status_code == 200

    body = json.dumps(
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"userId": "U1"},
                    "postback": {"data": "run_id=run-1&action=approve"},
                }
            ]
        }
    ).encode()
    signature = _make_signature(body, "secret")
    postback = client.post(
        "/webhooks/line/tenant-1/postback",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )
    assert postback.status_code == 200
    pending = repo.get_pending_approval_by_run_id("run-1")
    assert pending is not None
    assert pending.status == "approved"

    posted = client.post(
        "/tools/post-review-reply",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-1",
            "review_id": "review-1",
            "reply": {"reply_draft": draft.json()["reply_draft"]},
            "confirmation": {},
        },
    )
    assert posted.status_code == 200
    assert posted.json()["status"] == "posted"


def test_knowledge_update_tools_flow() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_knowledge_entry(
        tenant_id="tenant-1",
        category="basic_info",
        content="營業時間：早上10點到晚上8點",
        source_type="conversation",
    )
    client = TestClient(_make_app(repo))

    parsed = client.post(
        "/tools/parse-knowledge-update",
        json={
            "tenant_id": "tenant-1",
            "boss_message": "營業時間改成早上9點到晚上7點",
            "run_id": "run-knowledge-1",
        },
    )
    assert parsed.status_code == 200
    payload = parsed.json()
    assert payload["parsed_update"]["category"] == "basic_info"
    assert payload["parsed_update"]["new_value"] == "早上9點到晚上7點"

    diff = client.post(
        "/tools/diff-knowledge",
        json={
            "tenant_id": "tenant-1",
            "parsed_update": payload["parsed_update"],
            "run_id": "run-knowledge-1",
        },
    )
    assert diff.status_code == 200
    diff_payload = diff.json()
    assert len(diff_payload["conflicting_entries"]) == 1

    applied = client.post(
        "/tools/apply-knowledge-update",
        json={
            "tenant_id": "tenant-1",
            "diff": diff_payload,
            "run_id": "run-knowledge-1",
        },
    )
    assert applied.status_code == 200
    assert applied.json()["status"] == "applied"

    latest_entries = repo.list_knowledge_entries("tenant-1", limit=5)
    assert latest_entries[0].content == "早上9點到晚上7點"
    assert latest_entries[0].source_type == "boss_update"


def test_review_reply_edit_session_rewrites_and_requeues() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        account_label="GBP",
        credentials_json=json.dumps({"account_id": "123", "location_id": "locations/456", "access_token": "token", "expires_at": 9999999999}),
    )
    repo.update_onboarding_step("tenant-1", "completed")
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="這是改寫後的回覆")
    client = TestClient(app)
    repo.update_onboarding_step("tenant-1", "completed")

    repo.save_pending_approval(
        tenant_id="tenant-1",
        agentos_task_id="task-1",
        agentos_run_id="run-edit-1",
        workflow_type="kachu_review_reply",
        draft_content=json.dumps({"reply_draft": "原始回覆", "review_content": "這次等太久"}, ensure_ascii=False),
        review_id="review-1",
    )

    with Session(repo._engine) as session:  # noqa: SLF001
        config = session.get(LineChannelConfigTable, "cfg-1")
        assert config is not None
        config.channel_secret = encrypt_field("secret", app.state.settings.FIELD_ENCRYPTION_KEY)
        config.channel_access_token = encrypt_field("token", app.state.settings.FIELD_ENCRYPTION_KEY)
        session.add(config)
        session.commit()

    import kachu_plus.line.webhook as webhook_module

    original_push = webhook_module.push_line_messages
    webhook_module.push_line_messages = AsyncMock(return_value=None)
    try:
        edit_body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "source": {"userId": "U1"},
                        "postback": {"data": "run_id=run-edit-1&action=edit"},
                    }
                ]
            }
        ).encode()
        edit_signature = _make_signature(edit_body, "secret")
        postback = client.post(
            "/webhooks/line/tenant-1/postback",
            content=edit_body,
            headers={"X-Line-Signature": edit_signature, "Content-Type": "application/json"},
        )
        assert postback.status_code == 200
        pending = repo.get_pending_approval_by_run_id("run-edit-1")
        assert pending is not None
        assert pending.status == "editing"

        message_body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "replyToken": "reply-1",
                        "timestamp": 1,
                        "source": {"userId": "U1"},
                        "message": {"id": "msg-1", "type": "text", "text": "請更誠懇一點，補一句願意改進"},
                    }
                ]
            }
        ).encode()
        message_signature = _make_signature(message_body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1",
            content=message_body,
            headers={"X-Line-Signature": message_signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        updated = repo.get_pending_approval_by_run_id("run-edit-1")
        assert updated is not None
        assert updated.status == "pending"
        drafts = json.loads(updated.draft_content or "{}")
        assert drafts["reply_draft"] == "這是改寫後的回覆"
        assert app.state.consultant.build_reply.await_count == 1
    finally:
        webhook_module.push_line_messages = original_push


def test_generate_content_plan_then_generate_drafts() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(side_effect=["先用母親節關懷切入，再帶出預約誘因。", "IG 草稿", "Google 草稿"])
    client = TestClient(app)

    planned = client.post(
        "/tools/generate-content-plan",
        json={
            "tenant_id": "tenant-1",
            "objective": "母親節檔期宣傳",
            "selected_platforms": ["ig_fb", "google"],
            "context": {
                "brand_name": "測試店",
                "brand_tone": "親切真誠",
                "owner_brief": {"current_priorities": ["提升母親節預約"]},
                "knowledge": ["本月主打肩頸放鬆課程"],
                "industry_context": {"content_angles": ["節慶關懷", "方案亮點"]},
            },
        },
    )
    assert planned.status_code == 200
    content_plan = planned.json()["content_plan"]
    assert content_plan["campaign_angle"] == "節慶關懷"
    assert content_plan["selected_platforms"] == ["ig_fb", "google"]

    drafts = client.post(
        "/tools/generate-drafts",
        json={
            "tenant_id": "tenant-1",
            "context": {"brand_name": "測試店", "brand_tone": "親切真誠"},
            "analysis": {},
            "content_plan": content_plan,
        },
    )
    assert drafts.status_code == 200
    payload = drafts.json()
    assert payload["ig_fb"] == "IG 草稿"
    assert payload["google"] == "Google 草稿"
    assert payload["content_plan_summary"]["campaign_angle"] == "節慶關懷"


def test_line_webhook_binds_owner_membership_and_notify_pushes_to_owner() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    client = TestClient(_make_app(repo))

    body = json.dumps(
        {
            "events": [
                {
                    "type": "message",
                    "source": {"type": "user", "userId": "U-owner-1"},
                    "message": {"type": "text", "text": "你好"},
                }
            ]
        }
    ).encode()
    signature = _make_signature(body, "secret")
    response = client.post(
        "/webhooks/line/tenant-1",
        content=body,
        headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    members = repo.list_active_memberships("tenant-1")
    assert len(members) == 1
    assert members[0].line_user_id == "U-owner-1"

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.tools_router as tools_router_module

    original_push = tools_router_module.push_line_messages
    tools_router_module.push_line_messages = _fake_push
    try:
        notify = client.post(
            "/tools/notify-approval",
            json={
                "tenant_id": "tenant-1",
                "run_id": "run-push-1",
                "task_id": "task-push-1",
                "workflow": "kachu_google_post",
                "drafts": {"post_text": "這是一則待確認貼文"},
            },
        )
        assert notify.status_code == 200
    finally:
        tools_router_module.push_line_messages = original_push

    assert len(pushed) == 1
    assert pushed[0]["to"] == "U-owner-1"
    assert pushed[0]["messages"][0]["type"] == "flex"
    assert pushed[0]["messages"][0]["altText"] == "Google 商家動態草稿"
    footer_buttons = pushed[0]["messages"][0]["contents"]["footer"]["contents"]
    assert len(footer_buttons) == 4
    assert footer_buttons[0]["action"]["data"] == "action=approve&run_id=run-push-1&tenant_id=tenant-1"
    assert footer_buttons[1]["action"]["data"] == "action=schedule_publish&run_id=run-push-1&tenant_id=tenant-1"


def test_google_post_tools_edit_flow_and_learning_records() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        account_label="GBP",
        credentials_json=json.dumps({"account_id": "123", "location_id": "locations/456", "access_token": "token", "expires_at": 9999999999}),
    )
    client = TestClient(_make_app(repo))

    post_type = client.post("/tools/determine-post-type", json={"tenant_id": "tenant-1", "topic": "週末優惠活動"})
    assert post_type.status_code == 200
    assert post_type.json()["post_type"] == "OFFER"

    context = client.post("/tools/retrieve-context", json={"tenant_id": "tenant-1", "query": "週末優惠", "workflow_type": "kachu_google_post"})
    generated = client.post(
        "/tools/generate-google-post",
        json={
            "tenant_id": "tenant-1",
            "topic": "週末優惠活動",
            "post_type": post_type.json()["post_type"],
            "context": context.json(),
            "run_id": "run-post-1",
        },
    )
    assert generated.status_code == 200

    notify = client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-post-1",
            "task_id": "task-post-1",
            "workflow": "kachu_google_post",
            "drafts": {"post_text": generated.json()["post_text"]},
        },
    )
    assert notify.status_code == 200

    edit = client.post(
        "/webhooks/line/tenant-1/approval-edit/run-post-1",
        json={"actor_line_id": "U1", "edited_payload": {"post_text": "修正版貼文，語氣更直接。"}},
    )
    assert edit.status_code == 200

    publish = client.post(
        "/tools/publish-google-post",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-post-1",
            "post_text": "修正版貼文，語氣更直接。",
            "post_type": "OFFER",
        },
    )
    assert publish.status_code == 200
    assert publish.json()["status"] == "published"

    prefs = repo.get_preference_memories("tenant-1", platform="google", limit=3)
    assert len(prefs) == 1

    brief = repo.get_context_brief("tenant-1", "owner_brief")
    assert brief is not None


def test_notify_approval_marks_delivery_failed_when_all_pushes_fail() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    app = _make_app(repo)
    client = TestClient(app)

    import kachu_plus.tools_router as tools_router_module

    request = httpx.Request("POST", "https://api.line.me/v2/bot/message/push")
    response = httpx.Response(429, request=request, text='{"message":"Too Many Requests"}')
    original_push = tools_router_module.push_line_messages
    tools_router_module.push_line_messages = AsyncMock(
        side_effect=httpx.HTTPStatusError("rate limited", request=request, response=response)
    )
    try:
        notify = client.post(
            "/tools/notify-approval",
            json={
                "tenant_id": "tenant-1",
                "run_id": "run-delivery-failed-1",
                "task_id": "task-delivery-failed-1",
                "workflow": "kachu_photo_content",
                "drafts": {"ig_fb": "IG 草稿", "google": "Google 草稿"},
            },
        )
        assert notify.status_code == 200
    finally:
        tools_router_module.push_line_messages = original_push

    pending = repo.get_pending_approval_by_run_id("run-delivery-failed-1")
    assert pending is not None
    assert pending.status == "delivery_failed"
    assert "push_warnings" in notify.json()


def test_publish_google_post_marks_delivery_failed_when_connector_expired() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    app = _make_app(repo)
    app.state.google_review_service = MagicMock()
    app.state.google_review_service.resolve_client_context.side_effect = GoogleBusinessConnectorError(
        "google_business credential expired and no refresh token is available"
    )
    client = TestClient(app)

    repo.save_pending_approval(
        tenant_id="tenant-1",
        agentos_task_id="task-post-expired-1",
        agentos_run_id="run-post-expired-1",
        workflow_type="kachu_google_post",
        draft_content=json.dumps({"post_text": "過期憑證測試貼文"}, ensure_ascii=False),
    )

    publish = client.post(
        "/tools/publish-google-post",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-post-expired-1",
            "post_text": "過期憑證測試貼文",
            "post_type": "STANDARD",
        },
    )

    assert publish.status_code == 200
    assert publish.json()["status"] == "failed"
    assert "expired" in publish.json()["error"]
    assert repo.get_pending_approval_by_run_id("run-post-expired-1").status == "delivery_failed"


def test_faq_tools_and_customer_webhook_flow() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-customer-1", role="customer")
    repo.save_knowledge_entry(
        tenant_id="tenant-1",
        category="basic_info",
        content="我們每天早上9點到晚上7點營業",
        source_type="conversation",
    )
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="我們每天早上9點到晚上7點營業，歡迎您來。")
    app.state.execute_dispatcher = MagicMock()
    app.state.execute_dispatcher.dispatch = AsyncMock()
    client = TestClient(app)

    classified = client.post("/tools/classify-message", json={"tenant_id": "tenant-1", "message": "請問你們幾點營業？"})
    assert classified.status_code == 200
    assert classified.json()["category"] == "faq"

    answer = client.post("/tools/retrieve-answer", json={"tenant_id": "tenant-1", "message": "請問你們幾點營業？"})
    assert answer.status_code == 200
    assert "9點到晚上7點" in answer.json()["answer"]

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.tools_router as tools_router_module

    original_push = tools_router_module.push_line_messages
    tools_router_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "source": {"type": "user", "userId": "U-customer-1"},
                        "message": {"id": "mid-customer-1", "type": "text", "text": "請問你們幾點營業？"},
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
    finally:
        tools_router_module.push_line_messages = original_push

    assert len(pushed) == 1
    assert pushed[0]["to"] == "U-customer-1"
    assert "9點到晚上7點" in pushed[0]["messages"][0]["text"]
    app.state.execute_dispatcher.dispatch.assert_not_called()


def test_image_webhook_dispatches_photo_content_workflow() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    app = _make_app(repo)
    app.state.execute_dispatcher = MagicMock()
    app.state.execute_dispatcher.dispatch = AsyncMock(
        return_value=ExecutionTaskResult(
            task_id="task-photo-1",
            domain="kachu_photo_content",
            status="created",
            objective="Generate social post drafts from the boss photo upload",
        )
    )
    client = TestClient(app)

    import kachu_plus.line.webhook as webhook_module

    original_download = webhook_module._download_line_message_content
    original_push = webhook_module.push_line_messages
    pushed: list[dict] = []

    async def _fake_download(line_message_id: str, access_token: str) -> bytes:
        return b"fake-image-bytes"

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    webhook_module._download_line_message_content = _fake_download
    webhook_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "source": {"type": "user", "userId": "U-owner-2"},
                        "message": {"id": "img-1", "type": "image"},
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
    finally:
        webhook_module._download_line_message_content = original_download
        webhook_module.push_line_messages = original_push

    app.state.execute_dispatcher.dispatch.assert_not_awaited()
    pending_asset = repo.get_latest_pending_asset_intent(tenant_id="tenant-1", line_user_id="U-owner-2")
    assert pending_asset is not None
    assert len(pushed) == 1
    assert pushed[0]["to"] == "U-owner-2"
    message = pushed[0]["messages"][0]
    assert "你要我怎麼處理這張圖" in message["text"]
    assert [item["action"]["label"] for item in message["quickReply"]["items"]] == ["寫貼文", "進知識庫", "先討論"]


def test_asset_intent_postback_via_main_webhook_dispatches_photo_content() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    pending_asset = repo.save_pending_asset_intent(
        tenant_id="tenant-1",
        line_user_id="U-owner-2",
        line_message_id="img-asset-1",
        payload={
            "line_message_id": "img-asset-1",
            "photo_url": "data:image/jpeg;base64,ZmFrZQ==",
            "analysis": {"scene_description": "一張茶飲商品照", "upload_intent": "新品分享"},
            "source_conversation_id": "conv-asset-1",
        },
    )
    app = _make_app(repo)
    app.state.execute_dispatcher = MagicMock()
    app.state.execute_dispatcher.dispatch = AsyncMock(
        return_value=ExecutionTaskResult(
            task_id="task-photo-asset-1",
            domain="kachu_photo_content",
            status="created",
            objective="Generate social post drafts from the boss photo upload",
        )
    )
    client = TestClient(app)

    import kachu_plus.line.webhook as webhook_module

    original_push = webhook_module.push_line_messages
    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    webhook_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "source": {"type": "user", "userId": "U-owner-2"},
                        "postback": {
                            "data": f"action=asset_intent&decision=photo_content&asset_intent_id={pending_asset.id}&tenant_id=tenant-1"
                        },
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
    finally:
        webhook_module.push_line_messages = original_push

    app.state.execute_dispatcher.dispatch.assert_awaited_once()
    kwargs = app.state.execute_dispatcher.dispatch.await_args.kwargs
    assert kwargs["intent_label"] == "photo_content"
    assert kwargs["workflow_input_patch"]["line_message_id"] == "img-asset-1"
    assert kwargs["workflow_input_patch"]["analysis"]["scene_description"] == "一張茶飲商品照"
    assert repo.get_pending_asset_intent(pending_asset.id).status == "resolved"
    assert "收到照片了" in pushed[0]["messages"][0]["text"]


def test_asset_intent_text_follow_up_saves_knowledge_entry() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    repo.save_pending_asset_intent(
        tenant_id="tenant-1",
        line_user_id="U-owner-2",
        line_message_id="img-asset-2",
        payload={
            "line_message_id": "img-asset-2",
            "photo_url": "data:image/jpeg;base64,ZmFrZQ==",
            "analysis": {
                "scene_description": "一張店內漢方茶包展示照",
                "upload_intent": "品牌素材",
                "detected_objects": ["茶包", "木盤"],
            },
            "source_conversation_id": "conv-asset-2",
        },
    )
    app = _make_app(repo)
    app.state.execute_dispatcher = MagicMock()
    app.state.execute_dispatcher.dispatch = AsyncMock()
    client = TestClient(app)

    import kachu_plus.line.webhook as webhook_module

    original_push = webhook_module.push_line_messages
    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    webhook_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "source": {"type": "user", "userId": "U-owner-2"},
                        "message": {"id": "msg-asset-text-1", "type": "text", "text": "進知識庫"},
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
    finally:
        webhook_module.push_line_messages = original_push

    app.state.execute_dispatcher.dispatch.assert_not_called()
    knowledge_entries = repo.list_knowledge_entries("tenant-1", limit=5)
    assert any("店內漢方茶包展示照" in entry.content for entry in knowledge_entries)
    assert "收進品牌知識庫" in pushed[0]["messages"][0]["text"]


def test_asset_intent_postback_consult_returns_guided_reply() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.update_onboarding_step("tenant-1", "completed")
    pending_asset = repo.save_pending_asset_intent(
        tenant_id="tenant-1",
        line_user_id="U-owner-2",
        line_message_id="img-asset-3",
        payload={
            "line_message_id": "img-asset-3",
            "photo_url": "data:image/jpeg;base64,ZmFrZQ==",
            "analysis": {"scene_description": "一張門市櫃台與招牌商品照", "upload_intent": "店內日常"},
            "source_conversation_id": "conv-asset-3",
        },
    )
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="這張圖可以走新品介紹、門市氛圍、熟客互動三個方向。你這次最想主打哪一個？")
    client = TestClient(app)

    import kachu_plus.line.webhook as webhook_module

    original_push = webhook_module.push_line_messages
    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    webhook_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "source": {"type": "user", "userId": "U-owner-2"},
                        "postback": {
                            "data": f"action=asset_intent&decision=consult&asset_intent_id={pending_asset.id}&tenant_id=tenant-1"
                        },
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1/postback",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200
    finally:
        webhook_module.push_line_messages = original_push

    assert repo.get_pending_asset_intent(pending_asset.id).status == "resolved"
    assert "新品介紹" in pushed[0]["messages"][0]["text"]


def test_analyze_photo_uses_llm_when_key_is_available() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    app = _make_app(repo)
    app.state.settings.GOOGLE_AI_API_KEY = "fake-gemini-key"
    client = TestClient(app)

    async def _fake_acompletion(*, model, messages, api_key):
        assert model == "gemini/gemini-2.0-flash"
        assert api_key == "fake-gemini-key"
        assert messages[1]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content=json.dumps(
                            {
                                "scene_description": "木盤上擺著剛出爐的甜點與店內招牌飲品。",
                                "upload_intent": "新品分享",
                                "detected_objects": ["甜點", "飲品", "木盤"],
                                "suggested_tags": ["#新品上市", "#今日甜點"],
                                "quality_score": 0.91,
                                "needs_manual_review": False,
                            },
                            ensure_ascii=False,
                        )
                    )
                )
            ]
        )

    original_litellm = sys.modules.get("litellm")
    sys.modules["litellm"] = SimpleNamespace(acompletion=_fake_acompletion)
    try:
        response = client.post(
            "/tools/analyze-photo",
            json={
                "tenant_id": "tenant-1",
                "line_message_id": "img-llm-1",
                "photo_url": "data:image/jpeg;base64,ZmFrZQ==",
            },
        )
    finally:
        if original_litellm is None:
            sys.modules.pop("litellm", None)
        else:
            sys.modules["litellm"] = original_litellm

    assert response.status_code == 200
    payload = response.json()
    assert payload["scene_description"] == "木盤上擺著剛出爐的甜點與店內招牌飲品。"
    assert payload["upload_intent"] == "新品分享"
    assert payload["quality_score"] == 0.91


def test_schedule_publish_postback_and_due_scheduler_flow() -> None:
    from cryptography.fernet import Fernet

    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        account_label="GBP",
        credentials_json=json.dumps({"account_id": "123", "location_id": "locations/456", "access_token": "token", "expires_at": 9999999999}),
    )
    repo.update_onboarding_step("tenant-1", "completed")
    app = _make_app(repo)
    enc_key = Fernet.generate_key().decode()
    app.state.settings.FIELD_ENCRYPTION_KEY = enc_key
    with Session(repo._engine) as session:  # noqa: SLF001
        config = session.get(LineChannelConfigTable, "cfg-1")
        assert config is not None
        config.channel_secret = encrypt_field("secret", enc_key)
        config.channel_access_token = encrypt_field("token", enc_key)
        session.add(config)
        session.commit()

    client = TestClient(app)
    notify = client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-schedule-1",
            "task_id": "task-schedule-1",
            "workflow": "kachu_google_post",
            "drafts": {"post_text": "這是一則排程貼文"},
        },
    )
    assert notify.status_code == 200

    import kachu_plus.line.webhook as webhook_module

    pushed: list[dict] = []
    original_push = webhook_module.push_line_messages

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    webhook_module.push_line_messages = _fake_push
    try:
        body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "source": {"userId": "U-owner-1"},
                        "postback": {"data": "run_id=run-schedule-1&action=schedule_publish"},
                    }
                ]
            }
        ).encode()
        signature = _make_signature(body, "secret")
        response = client.post(
            "/webhooks/line/tenant-1/postback",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
        assert response.status_code == 200

        schedule_text_body = json.dumps(
            {
                "events": [
                    {
                        "type": "message",
                        "source": {"userId": "U-owner-1"},
                        "message": {"id": "msg-schedule-1", "type": "text", "text": "5月3日晚上8點"},
                    }
                ]
            }
        ).encode()
        schedule_text_signature = _make_signature(schedule_text_body, "secret")
        schedule_text_response = client.post(
            "/webhooks/line/tenant-1",
            content=schedule_text_body,
            headers={"X-Line-Signature": schedule_text_signature, "Content-Type": "application/json"},
        )
        assert schedule_text_response.status_code == 200

        confirm_body = json.dumps(
            {
                "events": [
                    {
                        "type": "postback",
                        "source": {"userId": "U-owner-1"},
                        "postback": {"data": "run_id=run-schedule-1&action=confirm_schedule_publish"},
                    }
                ]
            }
        ).encode()
        confirm_signature = _make_signature(confirm_body, "secret")
        confirm_response = client.post(
            "/webhooks/line/tenant-1/postback",
            content=confirm_body,
            headers={"X-Line-Signature": confirm_signature, "Content-Type": "application/json"},
        )
        assert confirm_response.status_code == 200
    finally:
        webhook_module.push_line_messages = original_push

    pending = repo.get_pending_approval_by_run_id("run-schedule-1")
    assert pending is not None
    assert pending.status == "scheduled"
    assert len(pushed) == 3
    assert pushed[0]["to"] == "U-owner-1"
    assert "請直接告訴我預計發布時間" in pushed[0]["messages"][0]["text"]
    assert "確認排程" in pushed[1]["messages"][0]["text"]
    assert "已排程於" in pushed[2]["messages"][0]["text"]

    payload = json.loads(pending.decision_payload_json or "{}")
    assert payload["scheduled_for"]
    assert payload["scheduled_timezone"]

    with Session(repo._engine) as session:  # noqa: SLF001
        pending_row = session.get(type(pending), pending.id)
        assert pending_row is not None
        pending_row.decision_payload_json = json.dumps({"scheduled_for": "2000-01-01T00:00:00+00:00"}, ensure_ascii=False)
        session.add(pending_row)
        session.commit()

    service = ScheduledApprovalService(app.state.approval_bridge, repo)
    summary = asyncio.run(service.process_due_approvals())
    assert summary["approved"] == 1
    pending = repo.get_pending_approval_by_run_id("run-schedule-1")
    assert pending is not None
    assert pending.status == "approved"
    assert app.state.execute_dispatcher._client.decisions[-1]["decision"]["decision"] == "approved"  # noqa: SLF001


def test_retrieve_context_and_generation_prompts_include_three_briefs() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    client = TestClient(_make_app(repo))

    repo.save_context_brief(
        tenant_id="tenant-1",
        brief_type="brand_brief",
        content={"brand_name": "測試店", "tone": "溫暖直接", "knowledge_highlights": ["主打熟客回流"]},
        ttl_hours=24,
    )
    repo.save_context_brief(
        tenant_id="tenant-1",
        brief_type="owner_brief",
        content={
            "current_priorities": ["先提升回購率"],
            "preference_examples": [{"original": "原版", "edited": "改成更直接", "notes": "避免太客套"}],
        },
        ttl_hours=24,
    )
    repo.save_context_brief(
        tenant_id="tenant-1",
        brief_type="customer_brief",
        content={"sleeping_customer_count": 8, "top_segment": "60 天未互動"},
        ttl_hours=24,
    )

    context = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-1", "query": "回流活動", "workflow_type": "kachu_google_post"},
    )
    assert context.status_code == 200
    context_body = context.json()
    assert context_body["owner_brief"]["current_priorities"][0] == "先提升回購率"
    assert context_body["customer_brief"]["sleeping_customer_count"] == 8
    assert context_body["industry_context"]["industry_name"] == "咖啡廳"
    assert "content_angles" in context_body["industry_context"]

    generated_post = client.post(
        "/tools/generate-google-post",
        json={
            "tenant_id": "tenant-1",
            "topic": "回流活動",
            "post_type": "STANDARD",
            "context": context_body,
        },
    )
    assert generated_post.status_code == 200

    consultant_calls = client.app.state.consultant.build_reply.await_args_list
    assert consultant_calls
    google_prompt = consultant_calls[-1].kwargs["message"]
    assert "先提升回購率" in google_prompt
    assert "sleeping_customer_count" in google_prompt
    assert "改成更直接" in google_prompt
    assert "空間氛圍" in google_prompt

    generated_reply = client.post(
        "/tools/generate-review-reply",
        json={
            "tenant_id": "tenant-1",
            "review": {"reviewer_name": "王小姐", "content": "還不錯"},
            "context": context_body,
            "sentiment": {"recommended_strategy": "感謝並邀請再訪"},
        },
    )
    assert generated_reply.status_code == 200
    review_prompt = client.app.state.consultant.build_reply.await_args_list[-1].kwargs["message"]
    assert "先提升回購率" in review_prompt
    assert "top_segment" in review_prompt
    assert "保持視覺一致性" in review_prompt


def test_brief_refresh_includes_industry_playbook_context() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    settings = Settings()
    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)

    refreshed = __import__("asyncio").run(briefs.refresh_briefs("tenant-1", reason="test"))

    assert refreshed["brand_brief"]["tone"].startswith("質感")
    assert refreshed["brand_brief"]["industry_context"]["industry_name"] == "咖啡廳"
    assert "customer_motivations" in refreshed["customer_brief"]


def test_content_plan_due_item_dispatches_and_local_approve_publishes() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        account_label="GBP",
        credentials_json=json.dumps({"account_id": "123", "location_id": "locations/456", "access_token": "token", "expires_at": 9999999999}),
    )
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")
    app = _make_app(repo)
    client = TestClient(app)

    service = ContentPlanService(repo, app.state.settings, app.state.consultant)
    created = asyncio.run(
        service.create_plan(
            tenant_id="tenant-1",
            objective="下週新品曝光",
            context={"brand_name": "測試店", "brand_tone": "自然親切"},
            selected_platforms=["google"],
            scheduled_for=None,
        )
    )
    item = created["item"]
    repo.update_content_plan_item(item_id=item.id, status="planned")

    with patch("kachu_plus.content_plans.push_line_messages", new=AsyncMock()) as mock_push:
        dispatch_result = asyncio.run(service.dispatch_item(item_id=item.id))

    run_id = dispatch_result["run_id"]
    pending = repo.get_pending_approval_by_run_id(run_id)
    assert pending is not None
    assert pending.workflow_type == "kachu_planned_content"
    assert pending.status == "pending"
    mock_push.assert_awaited()

    body = json.dumps(
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"userId": "U-owner-1"},
                    "postback": {"data": f"run_id={run_id}&action=approve"},
                }
            ]
        }
    ).encode()
    signature = _make_signature(body, "secret")
    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()):
        response = client.post(
            "/webhooks/line/tenant-1/postback",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
    assert response.status_code == 200
    assert repo.get_pending_approval_by_run_id(run_id).status == "published"
    assert repo.get_content_plan_item(item.id).status == "published"


def test_meta_webhook_creates_reply_draft_and_local_approve_posts_reply() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="meta",
        account_label="Meta Page",
        credentials_json=json.dumps({"access_token": "meta-token", "fb_page_id": "fb-page-1", "fb_access_token": "page-token"}),
    )
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="謝謝你的留言，歡迎私訊我們，我來幫你安排。")
    client = TestClient(app)

    with patch("kachu_plus.meta.push_line_messages", new=AsyncMock()) as mock_push:
        response = client.post(
            "/meta/webhook",
            json={
                "entry": [
                    {
                        "id": "fb-page-1",
                        "changes": [
                            {
                                "field": "feed",
                                "value": {
                                    "item": "comment",
                                    "comment_id": "comment-1",
                                    "post_id": "post-1",
                                    "message": "請問這週末有開嗎？",
                                    "from": {"id": "user-1", "name": "王小姐"},
                                },
                            }
                        ],
                    }
                ]
            },
        )
    assert response.status_code == 200
    assert response.json()["processed"] == 1
    mock_push.assert_awaited()

    engagement = repo.get_external_engagement_by_message_id("comment-1")
    assert engagement is not None
    assert engagement.status == "awaiting_approval"
    run_id = engagement.related_run_id
    pending = repo.get_pending_approval_by_run_id(run_id)
    assert pending is not None
    assert pending.workflow_type == "kachu_meta_reply"

    body = json.dumps(
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"userId": "U-owner-1"},
                    "postback": {"data": f"run_id={run_id}&action=approve"},
                }
            ]
        }
    ).encode()
    signature = _make_signature(body, "secret")
    with patch(
        "kachu_plus.publishing.resolve_meta_graph_client",
        return_value=(_FakeMetaClient(fb_page_id="fb-page-1"), {"fb_page_id": "fb-page-1"}),
    ), patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()):
        approved = client.post(
            "/webhooks/line/tenant-1/postback",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
    assert approved.status_code == 200
    assert repo.get_pending_approval_by_run_id(run_id).status == "published"
    assert repo.get_external_engagement(engagement.id).status == "replied"


def test_meta_local_approve_marks_delivery_failed_when_connector_expired() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="meta",
        account_label="Meta Page",
        credentials_json=json.dumps({"access_token": "expired-token", "fb_page_id": "fb-page-1", "fb_access_token": "page-token", "expires_at": 1}),
    )
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="謝謝你的留言，歡迎私訊我們，我來幫你安排。")
    client = TestClient(app)

    with patch("kachu_plus.meta.push_line_messages", new=AsyncMock()):
        response = client.post(
            "/meta/webhook",
            json={
                "entry": [
                    {
                        "id": "fb-page-1",
                        "changes": [
                            {
                                "field": "feed",
                                "value": {
                                    "item": "comment",
                                    "comment_id": "comment-expired-1",
                                    "post_id": "post-1",
                                    "message": "請問這週末有開嗎？",
                                    "from": {"id": "user-1", "name": "王小姐"},
                                },
                            }
                        ],
                    }
                ]
            },
        )
    assert response.status_code == 200

    engagement = repo.get_external_engagement_by_message_id("comment-expired-1")
    assert engagement is not None
    run_id = engagement.related_run_id

    body = json.dumps(
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"userId": "U-owner-1"},
                    "postback": {"data": f"run_id={run_id}&action=approve"},
                }
            ]
        }
    ).encode()
    signature = _make_signature(body, "secret")
    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()):
        approved = client.post(
            "/webhooks/line/tenant-1/postback",
            content=body,
            headers={"X-Line-Signature": signature, "Content-Type": "application/json"},
        )
    assert approved.status_code == 200
    assert repo.get_pending_approval_by_run_id(run_id).status == "delivery_failed"
    assert repo.get_external_engagement(engagement.id).status == "delivery_failed"


def test_meta_webhook_records_shared_event_envelope() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="meta",
        account_label="Meta Page",
        credentials_json=json.dumps({"access_token": "meta-token", "fb_page_id": "fb-page-1", "fb_access_token": "page-token"}),
    )
    app = _make_app(repo)
    app.state.consultant.build_reply = AsyncMock(return_value="謝謝你的留言，歡迎私訊我們，我來幫你安排。")
    client = TestClient(app)

    with patch("kachu_plus.meta.push_line_messages", new=AsyncMock()):
        response = client.post(
            "/meta/webhook",
            json={
                "entry": [
                    {
                        "id": "fb-page-1",
                        "changes": [
                            {
                                "field": "feed",
                                "value": {
                                    "item": "comment",
                                    "comment_id": "comment-event-1",
                                    "post_id": "post-1",
                                    "message": "請問這週末有開嗎？",
                                    "created_time": 1714895904000,
                                    "from": {"id": "user-1", "name": "王小姐"},
                                },
                            }
                        ],
                    }
                ]
            },
        )
    assert response.status_code == 200

    events = repo.list_webhook_events("tenant-1", provider="meta")
    assert len(events) == 1
    assert events[0].external_event_id == "comment-event-1"
    assert events[0].external_user_id == "user-1"
    assert events[0].external_thread_id == "post-1"
    assert events[0].occurred_at.replace(tzinfo=timezone.utc) == datetime.fromtimestamp(1714895904, tz=timezone.utc)
    assert events[0].received_at is not None


def test_pending_approval_sync_service_updates_local_status_from_agentos() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    repo.save_pending_approval(
        tenant_id="tenant-1",
        agentos_task_id="task-sync-1",
        agentos_run_id="run-sync-1",
        workflow_type="kachu_review_reply",
        draft_content=json.dumps({"reply_draft": "原草稿"}, ensure_ascii=False),
    )

    class _SyncClient(_FakeAgentOSClient):
        async def get_run(self, run_id: str):
            return type(
                "RunView",
                (),
                {
                    "run": {"id": run_id, "status": "completed"},
                    "approvals": [{"id": "approval-sync-1", "decision": "approved", "actor_id": "agentos-operator"}],
                    "checkpoints": [],
                    "run_state": {"summary": "completed"},
                },
            )()

    dispatcher = AgentOSTaskDispatcher(Settings(), client=_SyncClient())
    service = PendingApprovalSyncService(dispatcher, repo)

    summary = asyncio.run(service.sync_open_approvals())
    pending = repo.get_pending_approval_by_run_id("run-sync-1")

    assert summary == {"checked": 1, "synced": 1, "skipped": 0, "failed": 0}
    assert pending is not None
    assert pending.status == "approved"
    assert pending.actor_line_id == "agentos-operator"
    assert json.loads(pending.decision_payload_json)["synced_from"] == "agentos"