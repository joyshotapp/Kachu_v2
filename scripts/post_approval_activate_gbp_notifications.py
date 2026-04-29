#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx
from google.auth.transport.requests import Request
from google.oauth2.service_account import Credentials

SCOPE = ["https://www.googleapis.com/auth/business.manage"]
ACCOUNT_API = "https://mybusinessaccountmanagement.googleapis.com/v1/accounts"


def _get_token(service_account_json: str) -> str:
    creds = Credentials.from_service_account_file(service_account_json, scopes=SCOPE)
    creds.refresh(Request())
    return creds.token


def _request_json(
    client: httpx.Client,
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any] | str]:
    resp = client.request(method, url, headers=headers, json=payload)
    content_type = resp.headers.get("content-type", "")
    if "application/json" in content_type:
        data: dict[str, Any] | str = resp.json()
    else:
        data = resp.text
    return resp.status_code, data


def _list_accounts(client: httpx.Client, headers: dict[str, str]) -> list[dict[str, Any]]:
    code, data = _request_json(client, "GET", ACCOUNT_API, headers)
    if code != 200:
        raise RuntimeError(
            f"List accounts failed: HTTP {code} -> {json.dumps(data, ensure_ascii=False)}"
        )
    return data.get("accounts", []) if isinstance(data, dict) else []


def _configure_notifications(
    client: httpx.Client,
    headers: dict[str, str],
    account_name: str,
    topic_name: str,
    notification_types: list[str],
) -> tuple[str, int, dict[str, Any] | str]:
    endpoints = [
        (
            f"https://mybusiness.googleapis.com/v4/{account_name}/notifications",
            {
                "topicName": topic_name,
                "notificationTypes": notification_types,
            },
        ),
        (
            f"https://mybusiness.googleapis.com/v1/{account_name}/notifications",
            {
                "topicName": topic_name,
                "notificationTypes": notification_types,
            },
        ),
        (
            f"https://mybusinessnotifications.googleapis.com/v1/{account_name}/notificationSetting"
            "?updateMask=pubsubTopic,notificationTypes",
            {
                "name": f"{account_name}/notificationSetting",
                "pubsubTopic": topic_name,
                "notificationTypes": notification_types,
            },
        ),
    ]

    last_result: tuple[str, int, dict[str, Any] | str] | None = None
    for url, body in endpoints:
        code, data = _request_json(client, "PUT", url, headers, body)
        last_result = (url, code, data)
        if 200 <= code < 300:
            return url, code, data

    assert last_result is not None
    return last_result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Activate GBP review notifications after API allowlist approval."
    )
    parser.add_argument(
        "--service-account-json",
        default=os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "credentials/google-service-account.json"),
        help="Path to service account JSON file.",
    )
    parser.add_argument(
        "--topic-name",
        default=os.getenv("GBP_TOPIC_NAME", "projects/opsly-492412/topics/kachu-gbp-reviews"),
        help="Pub/Sub topic full name.",
    )
    parser.add_argument(
        "--account-name",
        default=os.getenv("GOOGLE_BUSINESS_ACCOUNT_NAME", ""),
        help="Optional explicit account resource name, e.g. accounts/1234567890",
    )
    parser.add_argument(
        "--notification-types",
        nargs="+",
        default=["NEW_REVIEW", "UPDATED_REVIEW"],
        help="Notification types.",
    )
    parser.add_argument("--dry-run", action="store_true", help="List accounts only.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if not os.path.exists(args.service_account_json):
        print(f"ERROR: service account json not found: {args.service_account_json}")
        return 1

    token = _get_token(args.service_account_json)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    with httpx.Client(timeout=30.0) as client:
        try:
            accounts = _list_accounts(client, headers)
        except Exception as exc:
            print(f"ERROR: {exc}")
            return 2

        if not accounts:
            print("ERROR: no GBP accounts visible for this credential")
            return 3

        print("Accounts visible:")
        for idx, acct in enumerate(accounts, start=1):
            print(f"  {idx}. {acct.get('name')} - {acct.get('accountName', '(no display name)')}")

        if args.dry_run:
            print("Dry run complete.")
            return 0

        account_name = args.account_name or accounts[0].get("name", "")
        if not account_name:
            print("ERROR: unable to determine account resource name")
            return 4

        print(f"Using account: {account_name}")
        print(f"Configuring topic: {args.topic_name}")

        url, code, data = _configure_notifications(
            client,
            headers,
            account_name,
            args.topic_name,
            args.notification_types,
        )

    if 200 <= code < 300:
        print("SUCCESS: notification configuration updated")
        print(f"Endpoint: {url}")
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return 0

    print("ERROR: failed to configure notifications")
    print(f"Last endpoint: {url}")
    print(f"HTTP {code}")
    print(json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, dict) else str(data))
    return 5


if __name__ == "__main__":
    sys.exit(main())
