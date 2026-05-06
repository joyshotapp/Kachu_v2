from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from kachu.config import Settings
from kachu.main import create_app


def test_demo_merchant_page_is_publicly_served() -> None:
    client = TestClient(
        create_app(
            Settings(
                LINE_CHANNEL_ACCESS_TOKEN="",
                LINE_CHANNEL_SECRET="",
                AGENTOS_BASE_URL="http://agentos-mock",
                KACHU_BASE_URL="http://localhost:8001",
                DATABASE_URL="sqlite://",
            )
        )
    )

    response = client.get("/merchants/demo-sishixunyangtang")

    assert response.status_code == 200
    assert "四時循養堂" in response.text
    assert "示範 tenant 商家頁" in response.text
    assert "節氣調理諮詢" in response.text
    assert "https://kachu.tw/merchants/demo-sishixunyangtang" in response.text


def test_homepage_links_to_public_assets_and_demo_merchant_page() -> None:
    client = TestClient(
        create_app(
            Settings(
                LINE_CHANNEL_ACCESS_TOKEN="",
                LINE_CHANNEL_SECRET="",
                AGENTOS_BASE_URL="http://agentos-mock",
                KACHU_BASE_URL="http://localhost:8001",
                DATABASE_URL="sqlite://",
            )
        )
    )

    response = client.get("/")

    assert response.status_code == 200
    assert "/privacy" in response.text
    assert "/terms" in response.text
    assert "/merchants/demo-sishixunyangtang" in response.text


def test_unknown_merchant_page_returns_not_found() -> None:
    client = TestClient(
        create_app(
            Settings(
                LINE_CHANNEL_ACCESS_TOKEN="",
                LINE_CHANNEL_SECRET="",
                AGENTOS_BASE_URL="http://agentos-mock",
                KACHU_BASE_URL="http://localhost:8001",
                DATABASE_URL="sqlite://",
            )
        )
    )

    response = client.get("/merchants/does-not-exist")

    assert response.status_code == 404


def test_incomplete_merchant_page_data_returns_server_error() -> None:
    client = TestClient(
        create_app(
            Settings(
                LINE_CHANNEL_ACCESS_TOKEN="",
                LINE_CHANNEL_SECRET="",
                AGENTOS_BASE_URL="http://agentos-mock",
                KACHU_BASE_URL="http://localhost:8001",
                DATABASE_URL="sqlite://",
            )
        )
    )

    broken_payload = {
        "merchant_name": "Broken Merchant",
        "category": "測試分類"
    }

    with patch("pathlib.Path.exists", return_value=True), patch(
        "pathlib.Path.open"
    ) as open_mock:
        open_mock.return_value.__enter__.return_value.read.return_value = json.dumps(broken_payload)
        response = client.get("/merchants/broken-merchant")

    assert response.status_code == 500
    assert "Merchant page data is incomplete" in response.json()["detail"]