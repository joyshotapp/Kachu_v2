from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class BossRouteMode(str, Enum):
    EXECUTE = "execute"
    CONSULT = "consult"
    CLARIFY = "clarify"


class BossRouteDecision(BaseModel):
    mode: BossRouteMode
    intent_label: str = ""       # 給 EXECUTE 用：描述要執行什麼 action
    clarify_question: str = ""   # 給 CLARIFY 用：追問句
    consult_reply: str = ""      # 給 CONSULT 用：諮詢回覆


class ExecutionTaskResult(BaseModel):
    task_id: str
    domain: str
    status: str
    objective: str
    current_run_id: str | None = None
    waiting_approval: bool = False
    approval_count: int = 0


class AgentOSTaskView(BaseModel):
    task: dict[str, Any]
    plan: dict[str, Any]


class AgentOSRunView(BaseModel):
    run: dict[str, Any]
    run_state: dict[str, Any]
    approvals: list[dict[str, Any]] = []
    checkpoints: list[dict[str, Any]] = []


class AgentOSApprovalDecision(BaseModel):
    decision: str
    actor_id: str
    edited_payload_ref: str | None = None
    edited_payload: dict[str, Any] | None = None


class ApprovalAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    EDIT = "edit"
    SCHEDULE = "schedule_publish"
