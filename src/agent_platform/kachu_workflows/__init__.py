"""agent_platform.kachu_workflows — stub pipeline builders + workflow definitions."""
from __future__ import annotations

from .photo_content_pipeline import build_kachu_photo_content_plan
from .review_reply_pipeline import build_kachu_review_reply_plan
from .line_faq_pipeline import build_kachu_line_faq_plan
from .business_profile_update_pipeline import build_kachu_business_profile_update_plan
from .knowledge_update_pipeline import build_kachu_knowledge_update_plan
from .google_post_pipeline import build_kachu_google_post_plan
from .ga4_report_pipeline import build_kachu_ga4_report_plan
from ..models import WorkflowDefinition


def kachu_photo_content_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_photo_content", description="照片貼文工作流")


def kachu_review_reply_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_review_reply", description="評論回覆工作流")


def kachu_line_faq_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_line_faq", description="LINE FAQ 工作流")


def kachu_business_profile_update_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_business_profile_update", description="Google 商家營運資訊更新工作流")


def kachu_knowledge_update_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_knowledge_update", description="知識庫更新工作流")


def kachu_google_post_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_google_post", description="Google 商家動態工作流")


def kachu_ga4_report_workflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(domain="kachu_ga4_report", description="GA4 週報工作流")


__all__ = [
    "build_kachu_photo_content_plan",
    "build_kachu_review_reply_plan",
    "build_kachu_line_faq_plan",
    "build_kachu_business_profile_update_plan",
    "build_kachu_knowledge_update_plan",
    "build_kachu_google_post_plan",
    "build_kachu_ga4_report_plan",
    "kachu_photo_content_workflow_definition",
    "kachu_review_reply_workflow_definition",
    "kachu_line_faq_workflow_definition",
    "kachu_business_profile_update_workflow_definition",
    "kachu_knowledge_update_workflow_definition",
    "kachu_google_post_workflow_definition",
    "kachu_ga4_report_workflow_definition",
]
