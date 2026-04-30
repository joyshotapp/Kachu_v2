"""Kachu photo_content workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("analyze-photo",    "/tools/analyze-photo",    "READONLY"),
    Step("retrieve-context", "/tools/retrieve-context", "READONLY"),
    Step("generate-drafts",  "/tools/generate-drafts",  "READONLY"),
    Step("notify-approval",  "/tools/notify-approval",  "REVERSIBLE_WRITE"),
    Step("confirm-publish",  "/tools/confirm-publish",  "REVERSIBLE_WRITE"),
    Step("publish-content",  "/tools/publish-content",  "IRREVERSIBLE_WRITE"),
]


def build_kachu_photo_content_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_photo_content", steps=list(_STEPS))
