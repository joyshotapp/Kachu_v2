from __future__ import annotations

from typing import Any

import httpx

from kachu_plus.models import AgentOSApprovalDecision, AgentOSRunView, AgentOSTaskView


class AgentOSWorkflowClient:
    def __init__(self, base_url: str, http_client: httpx.AsyncClient | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = http_client or httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        self._owns_client = http_client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def create_task(self, payload: dict[str, Any]) -> AgentOSTaskView:
        response = await self._client.post("/tasks", json=payload)
        response.raise_for_status()
        return AgentOSTaskView.model_validate(response.json())

    async def get_task(self, task_id: str) -> AgentOSTaskView:
        response = await self._client.get(f"/tasks/{task_id}")
        response.raise_for_status()
        return AgentOSTaskView.model_validate(response.json())

    async def run_task(self, task_id: str) -> AgentOSRunView:
        response = await self._client.post(f"/tasks/{task_id}/run")
        response.raise_for_status()
        return AgentOSRunView.model_validate(response.json())

    async def get_run(self, run_id: str) -> AgentOSRunView:
        response = await self._client.get(f"/runs/{run_id}")
        response.raise_for_status()
        return AgentOSRunView.model_validate(response.json())

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        response = await self._client.get("/approvals")
        response.raise_for_status()
        return response.json()

    async def decide_approval(self, approval_id: str, decision: AgentOSApprovalDecision) -> AgentOSRunView:
        response = await self._client.post(
            f"/approvals/{approval_id}/decision",
            json=decision.model_dump(exclude_none=True),
        )
        response.raise_for_status()
        return AgentOSRunView.model_validate(response.json())

    async def get_pending_approval_id_for_run(self, run_id: str) -> str | None:
        run_view = await self.get_run(run_id)
        for approval in run_view.approvals:
            if approval.get("decision") == "pending":
                return str(approval["id"])
        return None