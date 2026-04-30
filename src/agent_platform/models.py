"""agent_platform.models — minimal stub for Kachu pipeline tests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


# ── Task / Plan / Step ────────────────────────────────────────────────────────

class TaskCreateRequest(BaseModel):
    """Mirrors the real AgentOS TaskCreateRequest interface."""
    tenant_id: str
    domain: str
    objective: str
    risk_level: str = "medium"
    workflow_input: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str | None = None

    model_config = {"arbitrary_types_allowed": True}


@dataclass
class Step:
    """Represents a single workflow step."""
    name: str
    tool_endpoint: str = ""
    side_effect: str = "READONLY"


@dataclass
class Plan:
    """Represents a built workflow plan returned by plan builders."""
    domain: str
    steps: list[Step] = field(default_factory=list)


@dataclass
class WorkflowDefinition:
    """Represents an AgentOS workflow definition."""
    domain: str
    description: str = ""
