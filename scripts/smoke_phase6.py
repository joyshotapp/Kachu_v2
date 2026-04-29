#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
AGENTOS_SRC = ROOT.parent / "AgentOS" / "src"

for path in (SRC, AGENTOS_SRC):
    if path.exists():
        sys.path.insert(0, str(path))

from kachu.config import Settings
from kachu.goal_parser import GoalParser
from kachu.main import create_app
from kachu.persistence.tables import (  # noqa: E402
    ApprovalTaskTable,
    KnowledgeEntryTable,
    PushLogTable,
    SharedContextTable,
    TenantTable,
    WorkflowRunTable,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 6 in-process smoke checks for Kachu v2.")
    parser.add_argument("--tenant-id", default="phase6-smoke", help="Temporary tenant id")
    parser.add_argument("--database-url", default="sqlite://", help="Database URL for the in-process app")
    return parser.parse_args()


def _cleanup(repo, tenant_id: str) -> dict[str, int]:
    deleted: dict[str, int] = {}
    models = [KnowledgeEntryTable, SharedContextTable, WorkflowRunTable, ApprovalTaskTable, PushLogTable]
    with Session(repo._engine) as session:  # noqa: SLF001
        for model in models:
            rows = list(session.exec(select(model).where(model.tenant_id == tenant_id)).all())
            deleted[model.__name__] = len(rows)
            for row in rows:
                session.delete(row)
        tenant = session.get(TenantTable, tenant_id)
        deleted["TenantTable"] = 1 if tenant else 0
        if tenant is not None:
            session.delete(tenant)
        session.commit()
    return deleted


def main() -> int:
    args = parse_args()
    settings = Settings(
        DATABASE_URL=args.database_url,
        LINE_CHANNEL_ACCESS_TOKEN="",
        LINE_CHANNEL_SECRET="",
        LINE_BOSS_USER_ID="U_phase6_boss",
        AGENTOS_BASE_URL="http://agentos.local",
        KACHU_BASE_URL="http://kachu.local",
    )
    client = TestClient(create_app(settings))
    repo = client.app.state.repository

    try:
        tenant = repo.get_or_create_tenant(args.tenant_id)
        tenant.name = "Phase 6 Smoke"
        tenant.industry_type = "restaurant"
        tenant.address = "Taipei"
        repo.save_tenant(tenant)
        repo.save_preference_memory(
            tenant_id=args.tenant_id,
            platform="ig_fb",
            original_draft="原版 IG",
            edited_draft="修改後 IG",
            diff_notes="更口語",
            run_id="smoke-run",
        )
        repo.save_episodic_memory(
            tenant_id=args.tenant_id,
            workflow_type="kachu_photo_content",
            outcome="rejected",
            context_summary="smoke rejection",
        )
        repo.save_shared_context(
            tenant_id=args.tenant_id,
            context_type="monthly_content_calendar",
            content={"weeks": [{"week": 1, "topic": "春季新品", "channel": "ig_fb"}]},
            source_run_id="smoke-run",
        )

        context_resp = client.post(
            "/tools/retrieve-context",
            json={
                "tenant_id": args.tenant_id,
                "query": "春季新品",
                "workflow_type": "kachu_photo_content",
                "run_id": "smoke-run",
            },
        )
        assert context_resp.status_code == 200
        context = context_resp.json()
        assert context["preference_hints"]["ig_fb"]
        assert context["episode_hints"]
        assert context["shared_context_hints"]["calendar_topic"]

        direction_resp = client.post(
            "/tools/check-draft-direction",
            json={
                "tenant_id": args.tenant_id,
                "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
                "context": context,
                "run_id": "smoke-run",
            },
        )
        assert direction_resp.status_code == 200
        direction = direction_resp.json()
        assert direction["direction_summary"]

        drafts_resp = client.post(
            "/tools/generate-drafts",
            json={
                "tenant_id": args.tenant_id,
                "run_id": "smoke-run",
                "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
                "context": {**context, "direction_check": direction},
                "workflow_input": {"policy_generation_context": "請避免制式語氣"},
            },
        )
        assert drafts_resp.status_code == 200
        drafts = drafts_resp.json()
        assert drafts["ig_fb"]
        assert drafts["google"]

        parser = GoalParser(settings)
        quick_reply = parser.build_line_quick_reply([
            {"label": "查看流量報告", "intent": "ga4_report", "topic": "本週"},
        ])
        assert "workflow=kachu_ga4_report" in quick_reply["items"][0]["action"]["data"]

        from agent_platform.kachu_workflows.photo_content_pipeline import build_kachu_photo_content_plan
        from agent_platform.models import TaskCreateRequest

        plan = build_kachu_photo_content_plan(
            TaskCreateRequest(
                tenant_id=args.tenant_id,
                domain="kachu_photo_content",
                objective="smoke",
                workflow_input={
                    "tenant_id": args.tenant_id,
                    "line_message_id": "msg-1",
                    "photo_url": "https://example.com/photo.jpg",
                    "require_direction_check": True,
                    "approval_timeout_seconds": 21600,
                    "policy_generation_context": "請先收斂主題",
                },
            )
        )
        assert any(step.name == "check-draft-direction" for step in plan.steps)

        summary = {
            "tenant_id": args.tenant_id,
            "retrieve_context_keys": sorted(context.keys()),
            "direction_summary": direction["direction_summary"],
            "draft_keys": sorted(drafts.keys()),
            "plan_steps": [step.name for step in plan.steps],
            "quick_reply_data": quick_reply["items"][0]["action"]["data"],
        }
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    finally:
        cleanup = _cleanup(repo, args.tenant_id)
        print(json.dumps({"cleanup": cleanup}, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())