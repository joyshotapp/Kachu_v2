"""Kachu knowledge_update workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("parse-knowledge-update",  "/tools/parse-knowledge-update",  "READONLY"),
    Step("diff-knowledge",          "/tools/diff-knowledge",           "READONLY"),
    Step("notify-approval",         "/tools/notify-approval",          "REVERSIBLE_WRITE"),
    Step("confirm-knowledge-update","/tools/confirm-knowledge-update", "REVERSIBLE_WRITE"),
    Step("apply-knowledge-update",  "/tools/apply-knowledge-update",   "IRREVERSIBLE_WRITE"),
]


def build_kachu_knowledge_update_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_knowledge_update", steps=list(_STEPS))
