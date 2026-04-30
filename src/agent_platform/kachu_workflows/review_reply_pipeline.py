"""Kachu review_reply workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("fetch-review",          "/tools/fetch-review",          "READONLY"),
    Step("analyze-sentiment",     "/tools/analyze-sentiment",      "READONLY"),
    Step("retrieve-context",      "/tools/retrieve-context",       "READONLY"),
    Step("generate-review-reply", "/tools/generate-review-reply",  "READONLY"),
    Step("notify-approval",       "/tools/notify-approval",        "REVERSIBLE_WRITE"),
    Step("confirm-reply",         "/tools/confirm-reply",          "REVERSIBLE_WRITE"),
    Step("post-review-reply",     "/tools/post-review-reply",      "IRREVERSIBLE_WRITE"),
]


def build_kachu_review_reply_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_review_reply", steps=list(_STEPS))
