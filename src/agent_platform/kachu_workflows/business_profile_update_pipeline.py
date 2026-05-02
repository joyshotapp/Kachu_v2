"""Kachu business_profile_update workflow plan builder stub."""
from __future__ import annotations

from ..models import Plan, Step, TaskCreateRequest

_STEPS = [
    Step("parse-business-profile-update", "/tools/parse-business-profile-update", "READONLY"),
    Step("notify-approval", "/tools/notify-approval", "REVERSIBLE_WRITE"),
    Step("confirm-business-profile-update", "/tools/confirm-business-profile-update", "REVERSIBLE_WRITE"),
    Step("apply-business-profile-update", "/tools/apply-business-profile-update", "IRREVERSIBLE_WRITE"),
]


def build_kachu_business_profile_update_plan(request: TaskCreateRequest) -> Plan:
    return Plan(domain="kachu_business_profile_update", steps=list(_STEPS))