from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ── Intent ───────────────────────────────────────────────────────────────────

class Intent(StrEnum):
    PHOTO_CONTENT = "photo_content"
    BUSINESS_PROFILE_UPDATE = "business_profile_update"
    KNOWLEDGE_UPDATE = "knowledge_update"
    GOOGLE_POST = "google_post"
    GA4_REPORT = "ga4_report"
    REVIEW_REPLY = "review_reply"
    META_INSIGHTS = "meta_insights"
    FAQ_QUERY = "faq_query"
    GENERAL_CHAT = "general_chat"


class BossRouteMode(StrEnum):
    CONSULT = "consult"
    CLARIFY = "clarify"
    EXECUTE = "execute"


class BossRouteDecision(BaseModel):
    mode: BossRouteMode = BossRouteMode.CONSULT
    intent: Intent = Intent.GENERAL_CHAT
    topic: str = ""
    actions: list[dict[str, str]] = Field(default_factory=list)
    clarify_question: str = ""
    small_talk: bool = False


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


class ParseBusinessProfileUpdateRequest(BaseModel):
    tenant_id: str
    boss_message: str
    run_id: str = ""


class ApplyBusinessProfileUpdateRequest(BaseModel):
    tenant_id: str
    run_id: str
    update_request: dict[str, Any]


# ── Phase 2: Google Post tool request models ─────────────────────────────────

class GenerateGooglePostRequest(BaseModel):
    tenant_id: str
    topic: str = ""
    post_type: str = "STANDARD"    # STANDARD | EVENT | OFFER
    selected_platforms: list[str] = Field(default_factory=lambda: ["google"])
    context: dict[str, Any] | None = None
    run_id: str = ""


class PublishGooglePostRequest(BaseModel):
    tenant_id: str
    run_id: str
    post_text: str = ""
    post_type: str = "STANDARD"
    selected_platforms: list[str] = Field(default_factory=lambda: ["google"])
    drafts: dict[str, Any] | None = None
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


# ── Meta Insights & Comment Management ───────────────────────────────────────

class GetFBPageInsightsRequest(BaseModel):
    """Fetch FB Page-level Insights.  Requires ``read_insights`` scope."""
    tenant_id: str
    metric_names: list[str] = Field(default_factory=list)
    period: str = "day"     # day | week | days_28 | month
    since: str = ""         # ISO-8601 date, e.g. "2026-04-25"
    until: str = ""         # ISO-8601 date, e.g. "2026-05-02"
    run_id: str = ""


class GetFBPostInsightsRequest(BaseModel):
    """Fetch Insights for a specific FB Page post.  Requires ``read_insights`` scope."""
    tenant_id: str
    post_id: str
    metric_names: list[str] = Field(default_factory=list)
    run_id: str = ""


class ListFBCommentsRequest(BaseModel):
    """List comments on a FB post/photo.  Requires ``pages_manage_engagement`` scope."""
    tenant_id: str
    object_id: str          # FB post or photo ID
    limit: int = 25
    run_id: str = ""


class ReplyFBCommentRequest(BaseModel):
    """Reply to a FB comment as the Page.  Requires ``pages_manage_engagement`` scope."""
    tenant_id: str
    comment_id: str
    message: str
    run_id: str = ""


class HideFBCommentRequest(BaseModel):
    """Hide or unhide a FB comment.  Requires ``pages_manage_engagement`` scope."""
    tenant_id: str
    comment_id: str
    is_hidden: bool = True
    run_id: str = ""


class ListIGCommentsRequest(BaseModel):
    """List comments on an IG media.  Requires ``instagram_manage_comments`` scope."""
    tenant_id: str
    media_id: str           # IG media ID
    limit: int = 25
    run_id: str = ""


class ReplyIGCommentRequest(BaseModel):
    """Reply to an IG comment.  Requires ``instagram_manage_comments`` scope."""
    tenant_id: str
    comment_id: str
    message: str
    run_id: str = ""


class HideIGCommentRequest(BaseModel):
    """Hide or unhide an IG comment.  Requires ``instagram_manage_comments`` scope."""
    tenant_id: str
    comment_id: str
    hide: bool = True
    run_id: str = ""


class FetchMetaInsightsRequest(BaseModel):
    """On-demand FB Page + recent post insights requested from LINE."""
    tenant_id: str
    period: str = "week"         # "week" | "month"
    run_id: str = ""


class GenerateMetaInsightsSummaryRequest(BaseModel):
    """Generate an LLM summary from raw Meta insights data."""
    tenant_id: str
    insights_json: str           # serialized raw insights dict
    run_id: str = ""


class SendMetaInsightsReportRequest(BaseModel):
    """Push the insights report Flex message to the boss via LINE."""
    tenant_id: str
    summary: str
    details_json: str = "[]"     # JSON array of {label, value} dicts
    run_id: str = ""


# ── LINE Postback ─────────────────────────────────────────────────────────────

class LinePostback(BaseModel):
    action: ApprovalAction
    run_id: str
    tenant_id: str
