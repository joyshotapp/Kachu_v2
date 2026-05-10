from __future__ import annotations

import base64
import hashlib
import hmac
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.config import Settings
from kachu_plus.line.webhook import router as line_router
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import ChannelEntityTable, CustomerProfileTable, LineChannelConfigTable, ProfileLinkTable, TenantTable
from kachu_plus.suggestions import router as suggestions_router


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


class _FakeSuggestionDispatcher:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def dispatch(self, *, tenant_id: str, text: str, intent_label: str, workflow_input_patch=None) -> ExecutionTaskResult:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "text": text,
                "intent_label": intent_label,
                "workflow_input_patch": workflow_input_patch or {},
            }
        )
        return ExecutionTaskResult(
            task_id=f"task-{len(self.calls)}",
            domain="kachu_google_post" if intent_label == "google_post" else "kachu_review_reply",
            status="waiting_approval",
            objective=text,
            current_run_id=f"run-{len(self.calls)}",
            waiting_approval=True,
            approval_count=1,
        )


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


def _make_app(repo: KachuPlusRepository, dispatcher: _FakeSuggestionDispatcher) -> FastAPI:
    app = FastAPI()
    app.state.repository = repo
    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"
    app.state.settings = settings
    app.state.execute_dispatcher = dispatcher
    app.include_router(suggestions_router)
    app.include_router(line_router)
    return app


def test_suggestion_accept_send_and_report_flow() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="content_gap",
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="曝光下降，需要補內容",
        body="建議發一篇本週動態",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="這週新品已經上架，歡迎來看看。",
        payload={"detected_at": "2026-05-09T08:00:00Z"},
    )

    accepted = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/decision",
        json={
            "action": "accept",
            "actor_line_id": "U-owner-1",
            "draft_message": "這週新品已經準備好了，歡迎回來看看。",
            "execute_now": False,
        },
    )
    assert accepted.status_code == 200
    accepted_body = accepted.json()["suggestion"]
    assert accepted_body["status"] == "accepted"
    assert accepted_body["draft_message"] == "這週新品已經準備好了，歡迎回來看看。"

    sent = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
        json={"actor_line_id": "U-owner-1"},
    )
    assert sent.status_code == 200
    sent_body = sent.json()["suggestion"]
    assert sent_body["status"] == "sent"
    assert sent_body["result_snapshot"]["sending_at"]
    assert sent_body["related_run_id"] == "run-1"
    assert sent_body["result_snapshot"]["execution"]["workflow"] == "kachu_google_post"
    assert dispatcher.calls[0]["intent_label"] == "google_post"

    reported = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/report",
        json={
            "actor_line_id": "U-owner-1",
            "metrics": {"response_rate": 0.35, "booked_count": 2},
            "note": "7 天內有 2 位回流",
        },
    )
    assert reported.status_code == 200
    reported_body = reported.json()["suggestion"]
    assert reported_body["status"] == "reported"
    assert reported_body["result_snapshot"]["metrics"]["response_rate"] == 0.35


def test_line_postback_can_accept_and_send_suggestion() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="content_gap",
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="曝光下降，需要補內容",
        body="建議發一篇本週動態",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="這週新品已經上架，歡迎來看看。",
    )

    body = json.dumps(
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"userId": "U-owner-1"},
                    "postback": {"data": f"action=suggestion_accept&suggestion_id={suggestion.id}"},
                }
            ]
        }
    ).encode()
    response = client.post(
        "/webhooks/line/tenant-1/postback",
        content=body,
        headers={
            "X-Line-Signature": _make_signature(body, "secret"),
            "Content-Type": "application/json",
        },
    )
    assert response.status_code == 200

    stored = repo.get_suggestion(suggestion.id)
    assert stored is not None
    assert stored.status == "sent"
    snapshot = json.loads(stored.result_snapshot_json or "{}")
    assert snapshot["execution"]["workflow"] == "kachu_google_post"
    assert dispatcher.calls[0]["workflow_input_patch"]["suggestion_id"] == suggestion.id


def test_recover_sleeping_send_materializes_audience_snapshot_without_dispatch() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))
    client.app.state.settings.LINE_CHANNEL_ACCESS_TOKEN = ""

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="customer_relationship",
        title="有 3 位顧客超過 60 天沒來",
        reason="這批顧客可能流失，建議先喚回",
        body="建議發一則好久不見訊息",
        affected_profile_ids=["p1", "p2", "p3"],
        profile_count=3,
        suggested_action="發送一則好久不見喚回訊息",
        draft_message="好久不見，最近店裡有新的內容，歡迎回來看看。",
    )

    sent = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
        json={"actor_line_id": "U-owner-1"},
    )
    assert sent.status_code == 200
    sent_body = sent.json()["suggestion"]
    assert sent_body["status"] == "sent"
    assert sent_body["result_snapshot"]["execution"]["workflow"] == "recover_sleeping_campaign"
    assert sent_body["result_snapshot"]["execution"]["profile_count"] == 3
    assert dispatcher.calls == []


def test_suggestion_send_from_pending_records_accepted_and_sending() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="content_gap",
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="曝光下降，需要補內容",
        body="建議發一篇本週動態",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="這週新品已經上架，歡迎來看看。",
    )

    sent = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
        json={"actor_line_id": "U-owner-1"},
    )

    assert sent.status_code == 200
    body = sent.json()["suggestion"]
    assert body["status"] == "sent"
    assert body["result_snapshot"]["accepted_by"] == "U-owner-1"
    assert body["result_snapshot"]["sending_by"] == "U-owner-1"
    assert dispatcher.calls[0]["intent_label"] == "google_post"


def test_sent_suggestion_cannot_be_reedited_or_resent() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="content_gap",
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="曝光下降，需要補內容",
        body="建議發一篇本週動態",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="原始草稿",
    )
    client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/decision",
        json={"action": "accept", "actor_line_id": "U-owner-1"},
    )
    sent = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
        json={"actor_line_id": "U-owner-1", "draft_message": "送出前最後草稿"},
    )
    assert sent.status_code == 200

    resend = client.post(
        f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
        json={"actor_line_id": "U-owner-1", "draft_message": "不應覆寫"},
    )
    assert resend.status_code == 409

    stored = repo.get_suggestion(suggestion.id)
    assert stored is not None
    assert stored.draft_message == "送出前最後草稿"


def test_recover_sleeping_send_pushes_to_reachable_line_profiles() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(CustomerProfileTable(id="p1", tenant_id="tenant-1", status="sleeping"))
        entity = ChannelEntityTable(tenant_id="tenant-1", channel_type="line", external_user_id="U-customer-1")
        session.add(entity)
        session.flush()
        session.add(ProfileLinkTable(tenant_id="tenant-1", profile_id="p1", channel_entity_id=entity.id))
        session.commit()

    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="customer_relationship",
        title="有 1 位顧客超過 60 天沒來",
        reason="這位顧客可能流失，建議先喚回",
        body="建議發一則好久不見訊息",
        affected_profile_ids=["p1"],
        profile_count=1,
        suggested_action="發送一則好久不見喚回訊息",
        draft_message="好久不見，歡迎再回來看看。",
    )

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.suggestions as suggestions_module

    original_push = suggestions_module.push_line_messages
    suggestions_module.push_line_messages = _fake_push
    try:
        sent = client.post(
            f"/tenants/tenant-1/suggestions/{suggestion.id}/send",
            json={"actor_line_id": "U-owner-1"},
        )
    finally:
        suggestions_module.push_line_messages = original_push

    assert sent.status_code == 200
    sent_body = sent.json()["suggestion"]
    assert sent_body["result_snapshot"]["execution"]["mode"] == "line_push_campaign"
    assert sent_body["result_snapshot"]["execution"]["delivered_count"] == 1
    assert pushed[0]["to"] == "U-customer-1"
    assert dispatcher.calls == []


def test_suggestion_metrics_returns_windowed_acceptance_and_response_rate() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed(repo)
    dispatcher = _FakeSuggestionDispatcher()
    client = TestClient(_make_app(repo, dispatcher))

    first = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="content_gap",
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="曝光下降，需要補內容",
        body="建議發一篇本週動態",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="這週新品已經上架，歡迎來看看。",
    )
    client.post(
        f"/tenants/tenant-1/suggestions/{first.id}/decision",
        json={"action": "accept", "actor_line_id": "U-owner-1", "execute_now": False},
    )
    client.post(
        f"/tenants/tenant-1/suggestions/{first.id}/send",
        json={"actor_line_id": "U-owner-1"},
    )
    client.post(
        f"/tenants/tenant-1/suggestions/{first.id}/report",
        json={
            "actor_line_id": "U-owner-1",
            "metrics": {"response_rate": 0.4, "booked_count": 3},
        },
    )

    second = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type="recover_sleeping",
        category="customer_relationship",
        title="有 2 位顧客超過 60 天沒來",
        reason="這批顧客可能流失",
        body="建議先喚回",
        affected_profile_ids=["p1", "p2"],
        profile_count=2,
        suggested_action="發送一則好久不見喚回訊息",
        draft_message="好久不見，歡迎再回來看看。",
    )
    client.post(
        f"/tenants/tenant-1/suggestions/{second.id}/decision",
        json={"action": "dismiss", "actor_line_id": "U-owner-1"},
    )

    metrics = client.get("/tenants/tenant-1/suggestions/metrics", params={"window_days": 7})
    assert metrics.status_code == 200
    payload = metrics.json()
    assert payload["created_count"] == 2
    assert payload["actionable_count"] == 2
    assert payload["accepted_count"] == 1
    assert payload["reported_count"] == 1
    assert payload["acceptance_rate"] == 0.5
    assert payload["average_response_rate"] == 0.4
    assert payload["total_booked_count"] == 3
    assert payload["by_type"]["content_gap"]["average_response_rate"] == 0.4
