#!/usr/bin/env python3
from __future__ import annotations

import argparse
import base64
import json
import time

import httpx


def _build_pubsub_payload(review_id: str, location_name: str) -> dict[str, object]:
    data = {
        "reviewId": review_id,
        "locationName": location_name,
    }
    encoded = base64.b64encode(json.dumps(data).encode("utf-8")).decode("utf-8")
    return {
        "message": {
            "data": encoded,
            "messageId": f"manual-test-{int(time.time())}",
        },
        "subscription": "projects/opsly-492412/subscriptions/kachu-gbp-reviews-push",
    }


def _build_ping_payload() -> dict[str, object]:
    return {
        "message": {
            "data": "",
            "messageId": f"manual-ping-{int(time.time())}",
        },
        "subscription": "projects/opsly-492412/subscriptions/kachu-gbp-reviews-push",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify Google review webhook behavior.")
    parser.add_argument(
        "--webhook-url",
        default="https://app.kachu.tw/webhooks/google/review",
        help="Webhook URL",
    )
    parser.add_argument(
        "--review-id",
        default=f"test-review-{int(time.time())}",
        help="Synthetic review ID",
    )
    parser.add_argument(
        "--location-name",
        default="accounts/1234567890/locations/1234567890",
        help="Synthetic location resource name",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=15.0,
        help="HTTP timeout seconds",
    )
    return parser.parse_args()


def _post_json(url: str, payload: dict[str, object], timeout: float) -> tuple[int, str]:
    resp = httpx.post(url, json=payload, timeout=timeout)
    return resp.status_code, resp.text


def main() -> int:
    args = parse_args()

    ping_status, ping_body = _post_json(args.webhook_url, _build_ping_payload(), args.timeout)
    print("[PING]", ping_status, ping_body)

    review_status, review_body = _post_json(
        args.webhook_url,
        _build_pubsub_payload(args.review_id, args.location_name),
        args.timeout,
    )
    print("[REVIEW]", review_status, review_body)

    if ping_status != 200:
        print("ERROR: ping payload was not acknowledged with 200")
        return 1
    if review_status != 200:
        print("ERROR: review payload was not acknowledged with 200")
        return 2

    print("SUCCESS: webhook accepted both ping and review payloads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
