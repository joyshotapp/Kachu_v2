#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCAL_AGENTOS_ROOT = ROOT.parent / "AgentOS"
SYNC_EXCLUDE_NAMES = {
    ".git",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "__pycache__",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "htmlcov",
    ".coverage",
    ".env",
    "credentials",
}
SYNC_EXCLUDE_SUFFIXES = {".pyc", ".pyo", ".tar.gz"}


KACHU_REMOTE_SMOKE = r'''
import json
import urllib.request

from kachu.config import get_settings
from kachu.goal_parser import GoalParser
from kachu.persistence import KachuRepository, create_db_engine
from sqlmodel import Session, select
from kachu.persistence.tables import ApprovalTaskTable, KnowledgeEntryTable, PushLogTable, SharedContextTable, TenantTable, WorkflowRunTable

TENANT_ID = "phase6-prod-smoke"
repo = KachuRepository(create_db_engine(get_settings().DATABASE_URL))

def cleanup():
    models = [KnowledgeEntryTable, SharedContextTable, WorkflowRunTable, ApprovalTaskTable, PushLogTable]
    with Session(repo._engine) as session:
        for model in models:
            rows = list(session.exec(select(model).where(model.tenant_id == TENANT_ID)).all())
            for row in rows:
                session.delete(row)
        tenant = session.get(TenantTable, TENANT_ID)
        if tenant is not None:
            session.delete(tenant)
        session.commit()

def post(path, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:8001{path}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read().decode("utf-8"))

try:
    tenant = repo.get_or_create_tenant(TENANT_ID)
    tenant.name = "Phase 6 Production Smoke"
    tenant.industry_type = "restaurant"
    tenant.address = "Taipei"
    repo.save_tenant(tenant)
    repo.save_preference_memory(
        tenant_id=TENANT_ID,
        platform="ig_fb",
        original_draft="原版 IG",
        edited_draft="修改後 IG",
        diff_notes="更口語",
        run_id="smoke-run",
    )
    repo.save_episodic_memory(
        tenant_id=TENANT_ID,
        workflow_type="kachu_photo_content",
        outcome="rejected",
        context_summary="smoke rejection",
    )
    repo.save_shared_context(
        tenant_id=TENANT_ID,
        context_type="monthly_content_calendar",
        content={"weeks": [{"week": 1, "topic": "春季新品", "channel": "ig_fb"}]},
        source_run_id="smoke-run",
    )

    ctx = post("/tools/retrieve-context", {
        "tenant_id": TENANT_ID,
        "query": "春季新品",
        "workflow_type": "kachu_photo_content",
        "run_id": "smoke-run",
    })
    direction = post("/tools/check-draft-direction", {
        "tenant_id": TENANT_ID,
        "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
        "context": ctx,
        "run_id": "smoke-run",
    })
    drafts = post("/tools/generate-drafts", {
        "tenant_id": TENANT_ID,
        "run_id": "smoke-run",
        "analysis": {"scene_description": "春季新品甜點", "suggested_tags": ["#新品"]},
        "context": {**ctx, "direction_check": direction},
        "workflow_input": {"policy_generation_context": "請避免制式語氣"},
    })
    parser = GoalParser(get_settings())
    quick_reply = parser.build_line_quick_reply([
        {"label": "查看流量報告", "intent": "ga4_report", "topic": "本週"},
    ])
    print(json.dumps({
        "kachu": {
            "retrieve_context_keys": sorted(ctx.keys()),
            "direction_summary": direction.get("direction_summary", ""),
            "draft_keys": sorted(drafts.keys()),
            "quick_reply_data": quick_reply["items"][0]["action"]["data"],
        }
    }, ensure_ascii=False))
finally:
    cleanup()
'''


AGENTOS_REMOTE_SMOKE = r'''
import json
from agent_platform.kachu_workflows.photo_content_pipeline import build_kachu_photo_content_plan
from agent_platform.models import TaskCreateRequest

plan = build_kachu_photo_content_plan(
    TaskCreateRequest(
        tenant_id="phase6-prod-smoke",
        domain="kachu_photo_content",
        objective="smoke",
        workflow_input={
            "tenant_id": "phase6-prod-smoke",
            "line_message_id": "msg-1",
            "photo_url": "https://example.com/photo.jpg",
            "require_direction_check": True,
            "approval_timeout_seconds": 21600,
            "policy_generation_context": "請先收斂主題",
        },
    )
)

print(json.dumps({
    "agentos": {
        "plan_steps": [step.name for step in plan.steps],
        "has_direction_step": any(step.name == "check-draft-direction" for step in plan.steps),
    }
}, ensure_ascii=False))
'''


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy Kachu Phase 6 with explicit build/up/smoke stages.")
    parser.add_argument("--host", required=True, help="SSH host, for example root@172.234.85.159")
    parser.add_argument("--remote-root", default="/opt/kachu-v2", help="Remote compose project root")
    parser.add_argument("--remote-agentos-root", default="/opt/agentOS-v2", help="Remote AgentOS project root")
    parser.add_argument("--compose-file", default="docker-compose.prod.yml", help="Remote compose file name")
    parser.add_argument("--local-agentos-root", default=str(DEFAULT_LOCAL_AGENTOS_ROOT), help="Local AgentOS project root to sync")
    parser.add_argument("--kachu-container", default="kachu-v2-kachu-1")
    parser.add_argument("--agentos-container", default="kachu-v2-agentos-1")
    parser.add_argument("--services", nargs="*", default=["kachu", "agentos"], help="Compose services to build/up")
    parser.add_argument("--skip-sync", action="store_true")
    parser.add_argument("--skip-release-check", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--skip-up", action="store_true")
    parser.add_argument("--skip-smoke", action="store_true")
    return parser.parse_args()


def _run(command: list[str], *, cwd: Path | None = None, input_text: str | None = None) -> None:
    print(f"\n==> {' '.join(command)}")
    completed = subprocess.run(
        command,
        cwd=str(cwd) if cwd is not None else None,
        input=input_text,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.returncode)


def _ssh(host: str, remote_command: str, *, input_text: str | None = None) -> None:
    _run(["ssh", host, remote_command], input_text=input_text)


def _should_skip_path(relative_path: Path) -> bool:
    parts = set(relative_path.parts)
    if parts & SYNC_EXCLUDE_NAMES:
        return True
    name = relative_path.name
    if name in SYNC_EXCLUDE_NAMES:
        return True
    return any(name.endswith(suffix) for suffix in SYNC_EXCLUDE_SUFFIXES)


def _create_tarball(source_root: Path) -> Path:
    fd, tar_path = tempfile.mkstemp(suffix=".tar.gz")
    os.close(fd)
    tar_file = Path(tar_path)
    with tarfile.open(tar_file, "w:gz") as archive:
        for current_root, dirs, files in os.walk(source_root):
            current_path = Path(current_root)
            relative_dir = current_path.relative_to(source_root)
            dirs[:] = [directory for directory in dirs if not _should_skip_path(relative_dir / directory)]
            for file_name in files:
                relative_file = relative_dir / file_name
                if _should_skip_path(relative_file):
                    continue
                archive.add(source_root / relative_file, arcname=relative_file.as_posix())
    return tar_file


def _sync_tree(host: str, local_root: Path, remote_root: str) -> None:
    if not local_root.exists():
        raise SystemExit(f"Local sync root does not exist: {local_root}")

    tar_file = _create_tarball(local_root)
    remote_tar = f"/tmp/{local_root.name}-phase6-sync.tar.gz"
    try:
        _run(["scp", str(tar_file), f"{host}:{remote_tar}"])
        _ssh(
            host,
            f"mkdir -p {remote_root} && tar -xzf {remote_tar} -C {remote_root} && rm -f {remote_tar}",
        )
    finally:
        tar_file.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    services = " ".join(args.services)
    compose = f"docker compose -f {args.compose_file}"
    local_agentos_root = Path(args.local_agentos_root).resolve()

    if not args.skip_sync:
        _sync_tree(args.host, ROOT, args.remote_root)
        _sync_tree(args.host, local_agentos_root, args.remote_agentos_root)

    if not args.skip_release_check:
        _run([sys.executable, str(ROOT / "scripts" / "release_check.py")], cwd=ROOT)

    if not args.skip_build:
        _ssh(args.host, f"cd {args.remote_root} && {compose} build {services}")

    if not args.skip_up:
        _ssh(args.host, f"cd {args.remote_root} && {compose} up -d {services} && {compose} ps")

    if not args.skip_smoke:
        _ssh(args.host, f"docker exec -i {args.kachu_container} python -", input_text=KACHU_REMOTE_SMOKE)
        _ssh(args.host, f"docker exec -i {args.agentos_container} python -", input_text=AGENTOS_REMOTE_SMOKE)

    print("\nPhase 6 production deploy flow completed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())