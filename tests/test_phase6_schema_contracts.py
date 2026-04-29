from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from kachu.config import Settings
from kachu.main import create_app


@pytest.fixture
def client() -> TestClient:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_phase6",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
        )
    )
    return TestClient(app)


@pytest.mark.parametrize(
    ("path", "body", "missing_field"),
    [
        ("/tools/retrieve-context", {"tenant_id": "tenant-1"}, "query"),
        ("/tools/check-draft-direction", {"analysis": {}}, "tenant_id"),
        ("/tools/generate-drafts", {"analysis": {}}, "tenant_id"),
        (
            "/tools/notify-approval",
            {"tenant_id": "tenant-1", "run_id": "run-1", "workflow": "kachu_photo_content"},
            "drafts",
        ),
    ],
)
def test_high_frequency_tool_requests_validate_required_fields(
    client: TestClient,
    path: str,
    body: dict,
    missing_field: str,
) -> None:
    response = client.post(path, json=body)
    assert response.status_code == 422
    payload = response.json()
    assert any(err["loc"][-1] == missing_field for err in payload["detail"])


def test_check_draft_direction_response_shape(client: TestClient) -> None:
    response = client.post(
        "/tools/check-draft-direction",
        json={
            "tenant_id": "tenant-1",
            "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
            "context": {"brand_name": "測試店", "shared_context_hints": {"calendar_topic": "春季新品"}},
            "run_id": "run-1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("direction_summary"), str)
    assert isinstance(payload.get("focus_points"), list)
    assert isinstance(payload.get("avoidances"), list)


def test_generate_drafts_response_shape(client: TestClient) -> None:
    response = client.post(
        "/tools/generate-drafts",
        json={
            "tenant_id": "tenant-1",
            "run_id": "run-1",
            "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
            "context": {
                "brand_name": "測試店",
                "shared_context_hints": {"calendar_topic": "春季新品"},
                "direction_check": {"direction_summary": "聚焦春季新品", "focus_points": ["新品"], "avoidances": ["空泛"]},
            },
            "workflow_input": {"policy_generation_context": "請避免制式語氣"},
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert isinstance(payload.get("ig_fb"), str)
    assert isinstance(payload.get("google"), str)