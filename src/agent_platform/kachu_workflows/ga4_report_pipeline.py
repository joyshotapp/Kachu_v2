"""Kachu ga4_report workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("fetch-ga4-data",           "/tools/fetch-ga4-data",           "READONLY"),
    Step("generate-ga4-insights",    "/tools/generate-ga4-insights",    "READONLY"),
    Step("generate-recommendations", "/tools/generate-recommendations", "READONLY"),
    Step("send-ga4-report",          "/tools/send-ga4-report",          "REVERSIBLE_WRITE"),
]


def build_kachu_ga4_report_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_ga4_report", steps=list(_STEPS))
