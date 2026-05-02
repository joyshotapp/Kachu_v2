"""Kachu photo_content workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_BASE_STEPS = [
    Step("analyze-photo",    "/tools/analyze-photo",    "READONLY"),
    Step("retrieve-context", "/tools/retrieve-context", "READONLY"),
    Step("generate-drafts",  "/tools/generate-drafts",  "READONLY"),
    Step("notify-approval",  "/tools/notify-approval",  "REVERSIBLE_WRITE"),
    Step("confirm-publish",  "/tools/confirm-publish",  "REVERSIBLE_WRITE"),
    Step("publish-content",  "/tools/publish-content",  "IRREVERSIBLE_WRITE"),
]

_DIRECTION_STEP = Step("check-draft-direction", "/tools/check-draft-direction", "READONLY")


def build_kachu_photo_content_plan(request: TaskCreateRequest) -> Plan:
    steps = list(_BASE_STEPS)
    if request.workflow_input and request.workflow_input.get("require_direction_check"):
        # Insert check-draft-direction after retrieve-context (index 2), before generate-drafts
        steps.insert(2, _DIRECTION_STEP)
    return Plan(domain="kachu_photo_content", steps=steps)
