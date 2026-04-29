from __future__ import annotations

from pathlib import Path

from agent_platform.kachu_workflows.line_faq_pipeline import build_kachu_line_faq_plan
from agent_platform.kachu_workflows.photo_content_pipeline import build_kachu_photo_content_plan
from agent_platform.kachu_workflows.review_reply_pipeline import build_kachu_review_reply_plan
from agent_platform.models import TaskCreateRequest


def test_photo_content_pipeline_builds_plan_phase6() -> None:
    plan = build_kachu_photo_content_plan(
        TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_photo_content",
            objective="photo",
            workflow_input={
                "tenant_id": "T001",
                "line_message_id": "msg-1",
                "photo_url": "https://example.com/photo.jpg",
            },
        )
    )
    assert [step.name for step in plan.steps] == [
        "analyze-photo",
        "retrieve-context",
        "generate-drafts",
        "notify-approval",
        "confirm-publish",
        "publish-content",
    ]


def test_review_reply_pipeline_builds_plan_phase6() -> None:
    plan = build_kachu_review_reply_plan(
        TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_review_reply",
            objective="reply",
            workflow_input={"tenant_id": "T001", "review_id": "review-1"},
        )
    )
    assert [step.name for step in plan.steps] == [
        "fetch-review",
        "analyze-sentiment",
        "retrieve-context",
        "generate-review-reply",
        "notify-approval",
        "confirm-reply",
        "post-review-reply",
    ]


def test_line_faq_pipeline_builds_plan_phase6() -> None:
    plan = build_kachu_line_faq_plan(
        TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_line_faq",
            objective="faq",
            workflow_input={"tenant_id": "T001", "customer_line_id": "U1", "message": "營業到幾點"},
        )
    )
    assert [step.name for step in plan.steps] == [
        "classify-message",
        "retrieve-answer",
        "generate-response",
        "send-or-escalate",
    ]


def test_phase6_workflow_contract_guard() -> None:
    root = Path(__file__).resolve().parents[1]
    agentos_root = root.parent / "AgentOS"
    matrix = {
        "kachu_photo_content": {
            "plan_tests": [(root / "tests" / "test_phase6_workflow_guard.py", "test_photo_content_pipeline_builds_plan_phase6")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_retrieve_context_forwards_workflow_type_and_run_id")],
        },
        "kachu_review_reply": {
            "plan_tests": [(root / "tests" / "test_phase6_workflow_guard.py", "test_review_reply_pipeline_builds_plan_phase6")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_generate_review_reply_forwards_review_context")],
        },
        "kachu_line_faq": {
            "plan_tests": [(root / "tests" / "test_phase6_workflow_guard.py", "test_line_faq_pipeline_builds_plan_phase6")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_send_or_escalate_forwards_generated_response")],
        },
        "kachu_google_post": {
            "plan_tests": [(root / "tests" / "test_phase2_workflows.py", "test_google_post_pipeline_builds_plan")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_generate_google_post_forwards_post_type_and_context")],
        },
        "kachu_ga4_report": {
            "plan_tests": [(root / "tests" / "test_phase2_workflows.py", "test_ga4_report_pipeline_builds_plan")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_send_ga4_report_merges_recommendations_into_insights")],
        },
        "kachu_knowledge_update": {
            "plan_tests": [(root / "tests" / "test_phase2_workflows.py", "test_knowledge_update_pipeline_builds_plan")],
            "adapter_tests": [(agentos_root / "tests" / "test_kachu_adapter.py", "test_diff_knowledge_forwards_parsed_update")],
        },
    }

    for workflow_name, requirements in matrix.items():
        for file_path, test_name in requirements["plan_tests"] + requirements["adapter_tests"]:
            assert file_path.exists(), f"{workflow_name}: missing file {file_path}"
            content = file_path.read_text(encoding="utf-8")
            assert f"def {test_name}" in content or f"async def {test_name}" in content, (
                f"{workflow_name}: missing required test {test_name} in {file_path.name}"
            )