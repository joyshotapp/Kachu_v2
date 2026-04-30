"""Kachu line_faq workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("classify-message",  "/tools/classify-message",  "READONLY"),
    Step("retrieve-answer",   "/tools/retrieve-answer",   "READONLY"),
    Step("generate-response", "/tools/generate-response", "READONLY"),
    Step("send-or-escalate",  "/tools/send-or-escalate",  "REVERSIBLE_WRITE"),
]


def build_kachu_line_faq_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_line_faq", steps=list(_STEPS))
