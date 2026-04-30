"""Kachu google_post workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("determine-post-type",  "/tools/determine-post-type",  "READONLY"),
    Step("retrieve-context",     "/tools/retrieve-context",     "READONLY"),
    Step("generate-google-post", "/tools/generate-google-post", "READONLY"),
    Step("notify-approval",      "/tools/notify-approval",      "REVERSIBLE_WRITE"),
    Step("confirm-google-post",  "/tools/confirm-google-post",  "REVERSIBLE_WRITE"),
    Step("publish-google-post",  "/tools/publish-google-post",  "IRREVERSIBLE_WRITE"),
]


def build_kachu_google_post_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_google_post", steps=list(_STEPS))
