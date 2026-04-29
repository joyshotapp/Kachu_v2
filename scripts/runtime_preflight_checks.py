#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass

import httpx


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str


def _check(url: str, method: str = "GET", expected_status: set[int] | None = None) -> CheckResult:
    expected = expected_status or {200}
    name = f"{method} {url}"
    try:
        resp = httpx.request(method, url, timeout=12.0)
    except Exception as exc:
        return CheckResult(name, False, f"request failed: {exc}")

    ok = resp.status_code in expected
    detail = f"status={resp.status_code}"
    if len(resp.text) < 200:
        detail += f", body={resp.text}"
    return CheckResult(name, ok, detail)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run preflight checks for Kachu + AgentOS runtime.")
    parser.add_argument("--kachu-base", default="https://app.kachu.tw", help="Kachu base URL")
    parser.add_argument("--agentos-base", default="https://app.kachu.tw:8000", help="AgentOS base URL")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    checks = [
        _check(f"{args.kachu_base.rstrip('/')}/health", "GET", {200}),
        _check(f"{args.kachu_base.rstrip('/')}/webhooks/google/review", "GET", {405}),
    ]

    # Optional AgentOS health check: if publicly reachable
    checks.append(_check(f"{args.agentos_base.rstrip('/')}/health", "GET", {200, 404}))

    failed = 0
    for c in checks:
        status = "PASS" if c.ok else "FAIL"
        print(f"[{status}] {c.name} -> {c.detail}")
        if not c.ok:
            failed += 1

    if failed:
        print(f"\nPreflight finished with {failed} failing checks.")
        return 1

    print("\nPreflight checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
