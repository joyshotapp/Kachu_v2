from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
from pathlib import Path
from typing import Any

import httpx
from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import test_chen_session as chen_session

from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.services import AgentOSTaskDispatcher, _build_agentos_task_request


EXIT_OK = 0
EXIT_AGENTOS_NOT_READY = 10
EXIT_PENDING_ASSET_NOT_CREATED = 11
EXIT_WEBHOOK_NON_200 = 12
EXIT_CREATE_TASK_FAILED = 13
EXIT_RUN_TASK_FAILED = 14
EXIT_TASK_RECORD_MISSING = 15
EXIT_PENDING_ASSET_NOT_RESOLVED = 16

EXIT_CODE_MESSAGES = {
    EXIT_OK: "success",
    EXIT_AGENTOS_NOT_READY: "agentos_dispatcher_not_real_or_unreachable",
    EXIT_PENDING_ASSET_NOT_CREATED: "pending_asset_intent_not_created",
    EXIT_WEBHOOK_NON_200: "line_webhook_non_200",
    EXIT_CREATE_TASK_FAILED: "agentos_create_task_failed",
    EXIT_RUN_TASK_FAILED: "agentos_run_task_failed",
    EXIT_TASK_RECORD_MISSING: "execute_task_record_missing",
    EXIT_PENDING_ASSET_NOT_RESOLVED: "pending_asset_not_resolved",
}


def _print_exit_codes() -> None:
    print("Section 6 probe exit codes:")
    for code in sorted(EXIT_CODE_MESSAGES):
        print(f"  {code}: {EXIT_CODE_MESSAGES[code]}")


def _determine_exit_code(
    *,
    webhook_status_code: int,
    latest_task_present: bool,
    pending_asset_status: str,
    dispatch_summary: dict[str, Any],
) -> int:
    if webhook_status_code != 200:
        return EXIT_WEBHOOK_NON_200

    create_task = dispatch_summary.get("create_task", {}) if isinstance(dispatch_summary, dict) else {}
    run_task = dispatch_summary.get("run_task", {}) if isinstance(dispatch_summary, dict) else {}

    if create_task and not bool(create_task.get("ok", False)):
        return EXIT_CREATE_TASK_FAILED
    if run_task and not bool(run_task.get("ok", False)):
        return EXIT_RUN_TASK_FAILED
    if pending_asset_status != "resolved":
        return EXIT_PENDING_ASSET_NOT_RESOLVED
    if not latest_task_present:
        return EXIT_TASK_RECORD_MISSING
    return EXIT_OK


def _sign(body: bytes) -> str:
    digest = hmac.new(chen_session.CHANNEL_SECRET.encode("utf-8"), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode("utf-8")


class TracingDispatcher:
    def __init__(self, inner: AgentOSTaskDispatcher, *, omit_photo_url: bool) -> None:
        self._inner = inner
        self._omit_photo_url = omit_photo_url
        self.summary: dict[str, Any] = {}

    async def dispatch(
        self,
        *,
        tenant_id: str,
        text: str,
        intent_label: str,
        workflow_input_patch: dict[str, Any] | None = None,
    ) -> ExecutionTaskResult:
        patch = dict(workflow_input_patch or {})
        if self._omit_photo_url:
            patch.pop("photo_url", None)

        payload = _build_agentos_task_request(
            tenant_id=tenant_id,
            text=text,
            intent_label=intent_label,
            workflow_input_patch=patch,
        )
        workflow_input = payload.get("workflow_input", {})
        photo_url = str(workflow_input.get("photo_url") or "")
        payload_json = json.dumps(payload, ensure_ascii=False)

        print("\n=== dispatch payload summary ===")
        print(f"intent_label={intent_label}")
        print(f"payload_bytes={len(payload_json.encode('utf-8'))}")
        print(f"photo_url_present={bool(photo_url)}")
        print(f"photo_url_prefix={photo_url[:32] if photo_url else ''}")
        print(f"photo_url_chars={len(photo_url)}")
        print(f"analysis_keys={sorted((workflow_input.get('analysis') or {}).keys())}")
        self.summary = {
            "intent_label": intent_label,
            "payload_bytes": len(payload_json.encode("utf-8")),
            "photo_url_present": bool(photo_url),
            "photo_url_chars": len(photo_url),
            "analysis_keys": sorted((workflow_input.get("analysis") or {}).keys()),
            "create_task": {"ok": False},
            "run_task": {"ok": False},
        }

        try:
            task_view, _ = await self._inner.create_task(
                tenant_id=tenant_id,
                text=text,
                intent_label=intent_label,
                workflow_input_patch=patch,
            )
            print(f"create_task ok task_id={task_view.task.get('id')} status={task_view.task.get('status')}")
            self.summary["create_task"] = {
                "ok": True,
                "task_id": str(task_view.task.get("id") or ""),
                "status": str(task_view.task.get("status") or ""),
            }
        except httpx.HTTPStatusError as exc:
            print(f"create_task HTTPStatusError status={exc.response.status_code}")
            print(exc.response.text[:2000])
            self.summary["create_task"] = {
                "ok": False,
                "error_type": "HTTPStatusError",
                "status_code": exc.response.status_code,
                "body": exc.response.text[:2000],
            }
            raise
        except httpx.HTTPError as exc:
            print(f"create_task HTTPError type={type(exc).__name__} detail={exc}")
            self.summary["create_task"] = {
                "ok": False,
                "error_type": type(exc).__name__,
                "detail": str(exc),
            }
            raise

        task_id = str(task_view.task["id"])
        task_status = str(task_view.task.get("status", "created"))
        current_run_id = task_view.task.get("current_run_id")
        approval_count = 0
        waiting_approval = False
        status = task_status

        if getattr(self._inner, "_auto_run", False):
            try:
                run_view = await self._inner.ensure_run(task_id)
                print(f"run_task ok run_id={run_view.run.get('id')} status={run_view.run.get('status')}")
                self.summary["run_task"] = {
                    "ok": True,
                    "run_id": str(run_view.run.get("id") or ""),
                    "status": str(run_view.run.get("status") or ""),
                }
            except httpx.HTTPStatusError as exc:
                print(f"run_task HTTPStatusError status={exc.response.status_code}")
                print(exc.response.text[:2000])
                self.summary["run_task"] = {
                    "ok": False,
                    "error_type": "HTTPStatusError",
                    "status_code": exc.response.status_code,
                    "body": exc.response.text[:2000],
                }
                raise
            except httpx.HTTPError as exc:
                print(f"run_task HTTPError type={type(exc).__name__} detail={exc}")
                self.summary["run_task"] = {
                    "ok": False,
                    "error_type": type(exc).__name__,
                    "detail": str(exc),
                }
                raise
            current_run_id = run_view.run.get("id")
            status = str(run_view.run.get("status", task_status))
            approval_count = len(run_view.approvals)
            waiting_approval = status == "waiting_approval"

        return ExecutionTaskResult(
            task_id=task_id,
            domain=str(payload["domain"]),
            status=status,
            objective=str(payload["objective"]),
            current_run_id=str(current_run_id) if current_run_id is not None else None,
            waiting_approval=waiting_approval,
            approval_count=approval_count,
        )

    async def get_task(self, task_id: str) -> Any:
        return await self._inner.get_task(task_id)

    async def get_run(self, run_id: str) -> Any:
        return await self._inner.get_run(run_id)


def _post(client: TestClient, body_dict: dict[str, Any]) -> Any:
    body = json.dumps(body_dict, ensure_ascii=False).encode("utf-8")
    return client.post(
        f"/webhooks/line/{chen_session.TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": _sign(body), "Content-Type": "application/json"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Section 6 photo_content postback without running the full chen session")
    parser.add_argument("--omit-photo-url", action="store_true", help="Drop photo_url before AgentOS dispatch to test whether payload size/schema is the cause")
    parser.add_argument("--seed-pending-directly", action="store_true", help="Skip image webhook and seed pending_asset_intent directly from local JPEG")
    parser.add_argument("--summary-json", action="store_true", help="Print a final machine-readable JSON summary")
    parser.add_argument("--print-exit-codes", action="store_true", help="Print the fixed exit code contract and exit")
    args = parser.parse_args()

    if args.print_exit_codes:
        _print_exit_codes()
        return EXIT_OK

    db_dir = ROOT / "test_data"
    db_dir.mkdir(exist_ok=True)
    db_path = db_dir / f"section6_probe_{'omit' if args.omit_photo_url else 'full'}.db"
    if db_path.exists():
        db_path.unlink()

    engine = create_engine(f"sqlite:///{db_path}")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    chen_session._seed(repo)
    repo.update_onboarding_step(chen_session.TENANT_ID, "completed")

    app, pushed, briefs = chen_session._build_app(repo)
    dispatcher = getattr(app.state, "execute_dispatcher", None)
    if not isinstance(dispatcher, AgentOSTaskDispatcher):
        print("AgentOS real dispatcher is not active. Open the tunnel and set AGENTOS_BASE_URL first.")
        exit_code = EXIT_AGENTOS_NOT_READY
        if args.summary_json:
            print(json.dumps({
                "ok": False,
                "reason": EXIT_CODE_MESSAGES[exit_code],
                "exit_code": exit_code,
                "agentos_base_url": getattr(app.state.settings, "AGENTOS_BASE_URL", ""),
            }, ensure_ascii=False))
        return exit_code
    tracing_dispatcher = TracingDispatcher(dispatcher, omit_photo_url=args.omit_photo_url)
    app.state.execute_dispatcher = tracing_dispatcher

    client = TestClient(app)
    sess = chen_session.LineSession(client, pushed, briefs)

    print("=== section6 probe setup ===")
    print(f"db_path={db_path}")
    print(f"agentos_base_url={app.state.settings.AGENTOS_BASE_URL}")
    print(f"omit_photo_url={args.omit_photo_url}")
    print(f"seed_pending_directly={args.seed_pending_directly}")

    if args.seed_pending_directly:
        img_bytes = chen_session.IMG_AD_REFERRAL.read_bytes()
        photo_url = "data:image/jpeg;base64," + base64.b64encode(img_bytes).decode("ascii")
        analysis = {
            "scene_description": "Section 6 probe 廣告圖",
            "upload_intent": "老朋友推薦",
            "detected_objects": ["人物", "產品", "文案"],
            "suggested_tags": ["#老朋友推薦"],
            "quality_score": 0.9,
            "needs_manual_review": False,
        }
        pending = repo.save_pending_asset_intent(
            tenant_id=chen_session.TENANT_ID,
            line_user_id=chen_session.BOSS_USER_ID,
            line_message_id="probe-img-0001",
            payload={
                "line_message_id": "probe-img-0001",
                "photo_url": photo_url,
                "analysis": analysis,
                "source_conversation_id": "probe-conv-0001",
            },
        )
        print(f"seeded pending_asset_intent={pending.id} photo_url_chars={len(photo_url)}")
    else:
        sess.send_image(chen_session.IMG_AD_REFERRAL, label="Section 6 probe image")
        pending = sess.get_pending_asset()
        if pending is None:
            print("No pending_asset_intent created after image upload")
            exit_code = EXIT_PENDING_ASSET_NOT_CREATED
            if args.summary_json:
                print(json.dumps({
                    "ok": False,
                    "reason": EXIT_CODE_MESSAGES[exit_code],
                    "exit_code": exit_code,
                    "db_path": str(db_path),
                    "agentos_base_url": app.state.settings.AGENTOS_BASE_URL,
                }, ensure_ascii=False))
            return exit_code
        payload = json.loads(pending.payload_json or "{}")
        photo_url = str(payload.get("photo_url") or "")
        print(f"created pending_asset_intent={pending.id} photo_url_chars={len(photo_url)}")

    response = _post(
        client,
        {
            "events": [
                {
                    "type": "postback",
                    "source": {"type": "user", "userId": chen_session.BOSS_USER_ID},
                    "postback": {
                        "data": (
                            "action=asset_intent&decision=photo_content"
                            f"&asset_intent_id={pending.id}&tenant_id={chen_session.TENANT_ID}"
                        )
                    },
                }
            ]
        },
    )

    print("\n=== webhook response ===")
    print(f"status_code={response.status_code}")
    print(response.text[:2000])

    latest_task = repo.get_latest_execute_task_record(
        tenant_id=chen_session.TENANT_ID,
        line_user_id=chen_session.BOSS_USER_ID,
    )
    print("\n=== repo state ===")
    print(f"latest_task_record={'present' if latest_task is not None else 'missing'}")
    if latest_task is not None:
        print(f"task_id={latest_task.task_id} status={latest_task.status} run_id={latest_task.run_id}")
    resolved = repo.get_pending_asset_intent(pending.id)
    print(f"pending_asset_status={getattr(resolved, 'status', 'missing')} decision={getattr(resolved, 'selected_decision', '')}")

    exit_code = _determine_exit_code(
        webhook_status_code=response.status_code,
        latest_task_present=latest_task is not None,
        pending_asset_status=str(getattr(resolved, "status", "missing") or "missing"),
        dispatch_summary=tracing_dispatcher.summary,
    )

    summary = {
        "ok": exit_code == EXIT_OK,
        "exit_code": exit_code,
        "reason": EXIT_CODE_MESSAGES[exit_code],
        "mode": "seed_pending_directly" if args.seed_pending_directly else "full_image_then_postback",
        "omit_photo_url": args.omit_photo_url,
        "db_path": str(db_path),
        "agentos_base_url": app.state.settings.AGENTOS_BASE_URL,
        "webhook_status_code": response.status_code,
        "pending_asset_id": pending.id,
        "pending_asset_status": getattr(resolved, "status", "missing"),
        "selected_decision": getattr(resolved, "selected_decision", ""),
        "latest_task_record": {
            "present": latest_task is not None,
            "task_id": getattr(latest_task, "task_id", "") if latest_task is not None else "",
            "status": getattr(latest_task, "status", "") if latest_task is not None else "",
            "run_id": getattr(latest_task, "run_id", "") if latest_task is not None else "",
        },
        "dispatch": tracing_dispatcher.summary,
    }
    if args.summary_json:
        print(json.dumps(summary, ensure_ascii=False))

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())