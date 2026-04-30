from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── Intent ───────────────────────────────────────────────────────────────────

class Intent(StrEnum):
    PHOTO_CONTENT = "photo_content"
    KNOWLEDGE_UPDATE = "knowledge_update"
    GOOGLE_POST = "google_post"
    GA4_REPORT = "ga4_report"
    REVIEW_REPLY = "review_reply"
    FAQ_QUERY = "faq_query"
    GENERAL_CHAT = "general_chat"


# ── Approval ─────────────────────────────────────────────────────────────────

class ApprovalAction(StrEnum):
    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


# ── AgentOS API request/response models ──────────────────────────────────────

class AgentOSTaskRequest(BaseModel):
    tenant_id: str
    domain: str
    objective: str
    risk_level: str = "medium"
    workflow_input: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None


class AgentOSTaskView(BaseModel):
    task: dict[str, Any]
    plan: dict[str, Any]


class AgentOSApproval(BaseModel):
    id: str
    task_id: str
    run_id: str
    checkpoint: str
    step_id: str
    decision: str
    created_at: str
    expires_at: str | None = None


class AgentOSRunView(BaseModel):
    run: dict[str, Any]
    run_state: dict[str, Any]
    approvals: list[dict[str, Any]] = Field(default_factory=list)
    checkpoints: list[dict[str, Any]] = Field(default_factory=list)


class AgentOSApprovalDecision(BaseModel):
    decision: str  # "approved" | "rejected" | "edited"
    actor_id: str
    edited_payload: dict[str, Any] | None = None
    edited_payload_ref: str | None = None


# ── Tool API request/response models ─────────────────────────────────────────

class AnalyzePhotoRequest(BaseModel):
    tenant_id: str
    photo_url: str
    line_message_id: str


class RetrieveContextRequest(BaseModel):
    tenant_id: str
    query: str
    workflow_type: str = ""   # e.g. kachu_photo_content — used to filter episode hints
    run_id: str = ""


class CheckDraftDirectionRequest(BaseModel):
    tenant_id: str
    analysis: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    run_id: str = ""


class GenerateDraftsRequest(BaseModel):
    tenant_id: str
    analysis: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    workflow_input: dict[str, Any] | None = None
    run_id: str = ""


class NotifyApprovalRequest(BaseModel):
    tenant_id: str
    run_id: str
    workflow: str
    drafts: dict[str, Any]


class PublishContentRequest(BaseModel):
    tenant_id: str
    run_id: str
    selected_platforms: list[str]
    drafts: dict[str, Any]


class FetchReviewRequest(BaseModel):
    tenant_id: str
    review_id: str


class GenerateReviewReplyRequest(BaseModel):
    tenant_id: str
    review: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    sentiment: dict[str, Any] | None = None  # from analyze-sentiment step
    run_id: str = ""


class PostReviewReplyRequest(BaseModel):
    tenant_id: str
    run_id: str
    review_id: str
    reply: dict[str, Any] | None = None
    confirmation: dict[str, Any] = Field(default_factory=dict)


class ClassifyMessageRequest(BaseModel):
    tenant_id: str
    message: str
    customer_line_id: str
    run_id: str = ""


class AnalyzeSentimentRequest(BaseModel):
    tenant_id: str
    review: dict[str, Any] | None = None
    run_id: str = ""


class GenerateResponseRequest(BaseModel):
    tenant_id: str
    message: str
    answer: dict[str, Any] | None = None        # from retrieve-answer
    classification: dict[str, Any] | None = None  # from classify-message
    run_id: str = ""


class RetrieveAnswerRequest(BaseModel):
    tenant_id: str
    message: str
    classification: dict[str, Any] | None = None
    run_id: str = ""


class SendOrEscalateRequest(BaseModel):
    tenant_id: str
    customer_line_id: str
    answer: dict[str, Any] | None = None
    run_id: str = ""


# ── Phase 2: Knowledge Update tool request models ────────────────────────────

class ParseKnowledgeUpdateRequest(BaseModel):
    tenant_id: str
    boss_message: str
    run_id: str = ""


class DiffKnowledgeRequest(BaseModel):
    tenant_id: str
    parsed_update: dict[str, Any]   # output of parse-knowledge-update
    run_id: str = ""


class ApplyKnowledgeUpdateRequest(BaseModel):
    tenant_id: str
    run_id: str
    diff: dict[str, Any]            # output of diff-knowledge


# ── Phase 2: Google Post tool request models ─────────────────────────────────

class GenerateGooglePostRequest(BaseModel):
    tenant_id: str
    topic: str = ""
    post_type: str = "STANDARD"    # STANDARD | EVENT | OFFER
    context: dict[str, Any] | None = None
    run_id: str = ""


class PublishGooglePostRequest(BaseModel):
    tenant_id: str
    run_id: str
    post_text: str
    post_type: str = "STANDARD"
    call_to_action_url: str = ""


# ── Phase 2: GA4 Report tool request models ──────────────────────────────────

class FetchGA4DataRequest(BaseModel):
    tenant_id: str
    period: str = "7daysAgo"       # "7daysAgo" | "30daysAgo" | "yesterday"
    run_id: str = ""


class GenerateGA4InsightsRequest(BaseModel):
    tenant_id: str
    ga4_data: dict[str, Any]
    run_id: str = ""


class GenerateRecommendationsRequest(BaseModel):
    """Request to generate standalone action recommendations from GA4 insights."""
    tenant_id: str
    ga4_data: dict[str, Any]
    insights: dict[str, Any]        # output of generate-ga4-insights
    run_id: str = ""


class DeterminePostTypeRequest(BaseModel):
    """Request to determine the best GBP post type for a given topic."""
    tenant_id: str
    topic: str
    context: dict[str, Any] | None = None  # optional brand context
    run_id: str = ""


class SendGA4ReportRequest(BaseModel):
    tenant_id: str
    run_id: str
    insights: dict[str, Any]


# ── LINE Postback ─────────────────────────────────────────────────────────────

class LinePostback(BaseModel):
    action: ApprovalAction
    run_id: str
    tenant_id: str
