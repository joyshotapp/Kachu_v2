from __future__ import annotations

from typing import Any

import httpx

from .config import Settings
from .models import AgentOSApprovalDecision, AgentOSRunView, AgentOSTaskRequest, AgentOSTaskView


class AgentOSClient:
    """HTTP client for AgentOS REST API."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.AGENTOS_BASE_URL.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(base_url=self._base_url, timeout=30.0)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def create_task(self, request: AgentOSTaskRequest) -> AgentOSTaskView:
        client = self._get_client()
        resp = await client.post("/tasks", json=request.model_dump(exclude_none=True))
        resp.raise_for_status()
        return AgentOSTaskView.model_validate(resp.json())

    async def run_task(self, task_id: str) -> AgentOSRunView:
        client = self._get_client()
        resp = await client.post(f"/tasks/{task_id}/run")
        resp.raise_for_status()
        return AgentOSRunView.model_validate(resp.json())

    async def get_run(self, run_id: str) -> AgentOSRunView:
        client = self._get_client()
        resp = await client.get(f"/runs/{run_id}")
        resp.raise_for_status()
        return AgentOSRunView.model_validate(resp.json())

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        client = self._get_client()
        resp = await client.get("/approvals")
        resp.raise_for_status()
        return resp.json()

    async def decide_approval(self, approval_id: str, decision: AgentOSApprovalDecision) -> AgentOSRunView:
        client = self._get_client()
        resp = await client.post(
            f"/approvals/{approval_id}/decision",
            json=decision.model_dump(exclude_none=True),
        )
        resp.raise_for_status()
        return AgentOSRunView.model_validate(resp.json())

    async def get_pending_approval_id_for_run(self, run_id: str) -> str | None:
        """Fetch the run and return the ID of the first pending approval, if any."""
        run_view = await self.get_run(run_id)
        for approval in run_view.approvals:
            if approval.get("decision") == "pending":
                return approval["id"]
        return None

    async def cancel_task(self, task_id: str) -> dict[str, Any]:
        """Cancel a running or pending task."""
        client = self._get_client()
        resp = await client.post(f"/tasks/{task_id}/cancel")
        resp.raise_for_status()
        return resp.json()

    async def retry_run(self, run_id: str) -> AgentOSRunView:
        """Retry a FAILED run from the step that failed."""
        client = self._get_client()
        resp = await client.post(f"/runs/{run_id}/retry")
        resp.raise_for_status()
        return AgentOSRunView.model_validate(resp.json())

    async def replay_run(self, run_id: str) -> AgentOSRunView:
        """Create a fresh run for the same task (full replay from step 1)."""
        client = self._get_client()
        resp = await client.post(f"/runs/{run_id}/replay")
        resp.raise_for_status()
        return AgentOSRunView.model_validate(resp.json())
