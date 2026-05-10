from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from typing import Any

from kachu_plus.line.flex_builder import build_photo_content_flex
from kachu_plus.line.push import push_line_messages, resolve_tenant_line_recipients
from kachu_plus.tools_router import build_content_drafts, build_content_plan_payload

logger = logging.getLogger(__name__)


class ContentPlanService:
    def __init__(self, repo: Any, settings: Any, consultant: Any) -> None:
        self._repo = repo
        self._settings = settings
        self._consultant = consultant

    async def create_plan(
        self,
        *,
        tenant_id: str,
        objective: str,
        context: dict[str, Any],
        selected_platforms: list[str],
        scheduled_for: datetime | None,
        source_conversation_id: str = "",
        created_by_line_user_id: str = "",
    ) -> dict[str, Any]:
        tenant = self._repo.get_tenant(tenant_id)
        plan_payload = await build_content_plan_payload(
            consultant=self._consultant,
            tenant_name=getattr(tenant, "name", "") or str(context.get("brand_name", "你的店") or "你的店"),
            industry_type=getattr(tenant, "industry_type", "") or "一般服務業",
            objective=objective,
            selected_platforms=selected_platforms,
            context=context,
        )
        plan = self._repo.create_content_plan(
            tenant_id=tenant_id,
            objective=objective,
            plan_payload=plan_payload,
            source_conversation_id=source_conversation_id,
            created_by_line_user_id=created_by_line_user_id,
        )
        item = self._repo.create_content_plan_item(
            content_plan_id=plan.id,
            tenant_id=tenant_id,
            title=str(plan_payload.get("headline") or objective),
            selected_platforms=plan_payload.get("selected_platforms") or selected_platforms,
            scheduled_for=scheduled_for,
        )
        return {
            "content_plan": plan,
            "content_plan_payload": plan_payload,
            "item": item,
        }

    async def process_due_items(self, *, limit: int = 20) -> dict[str, int]:
        due_items = self._repo.list_due_content_plan_items(limit=limit)
        dispatched = 0
        failed = 0
        for item in due_items:
            try:
                await self.dispatch_item(item_id=item.id)
                dispatched += 1
            except Exception:  # noqa: BLE001
                logger.exception("content plan dispatch failed item_id=%s tenant_id=%s", item.id, item.tenant_id)
                self._repo.update_content_plan_item(item_id=item.id, status="failed")
                failed += 1
        return {"due": len(due_items), "dispatched": dispatched, "failed": failed}

    async def dispatch_item(self, *, item_id: str) -> dict[str, Any]:
        item = self._repo.get_content_plan_item(item_id)
        if item is None:
            raise LookupError("content plan item not found")
        plan = self._repo.get_content_plan(item.content_plan_id)
        if plan is None:
            raise LookupError("content plan not found")
        plan_payload = json.loads(plan.plan_payload_json or "{}")
        tenant = self._repo.get_tenant(item.tenant_id)
        context = {
            "brand_name": getattr(tenant, "name", "") or "你的店",
            "brand_tone": plan_payload.get("brand_tone", "親切真誠"),
            "content_plan": plan_payload,
        }
        drafts = await build_content_drafts(
            consultant=self._consultant,
            context=context,
            analysis={},
            content_plan=plan_payload,
        )
        drafts["content_plan_id"] = plan.id
        drafts["content_plan_item_id"] = item.id
        run_id = f"content-plan:{item.id}"
        self._repo.update_content_plan_item(
            item_id=item.id,
            status="awaiting_approval",
            draft_payload=drafts,
            pending_run_id=run_id,
        )
        self._repo.save_pending_approval(
            tenant_id=item.tenant_id,
            agentos_task_id=item.id,
            agentos_run_id=run_id,
            workflow_type="kachu_planned_content",
            draft_content=json.dumps(drafts, ensure_ascii=False),
        )
        recipients = resolve_tenant_line_recipients(repo=self._repo, settings=self._settings, tenant_id=item.tenant_id)
        if recipients and self._settings.LINE_CHANNEL_ACCESS_TOKEN:
            flex = build_photo_content_flex(run_id=run_id, tenant_id=item.tenant_id, drafts=drafts)
            for recipient in recipients:
                await push_line_messages(
                    to=recipient,
                    messages=[
                        {"type": "text", "text": f"企劃排程已到期，我先把這則內容草稿準備好了：{item.title}"},
                        {"type": "flex", "altText": "企劃排程草稿", "contents": flex},
                    ],
                    access_token=self._settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
        return {"item_id": item.id, "run_id": run_id, "drafts": drafts}


class ContentPlanScheduler:
    def __init__(self, service: ContentPlanService, *, interval_seconds: int = 60) -> None:
        self._service = service
        self._interval_seconds = max(int(interval_seconds), 60)
        self._task = None

    def start(self) -> None:
        import asyncio

        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def shutdown(self) -> None:
        import asyncio

        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> dict[str, int]:
        return await self._service.process_due_items()

    async def _run_loop(self) -> None:
        import asyncio

        while True:
            await self.run_once()
            await asyncio.sleep(self._interval_seconds)