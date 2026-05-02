from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from uuid import uuid4

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TenantTable(SQLModel, table=True):
    __tablename__ = "kachu_tenants"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(default="")
    industry_type: str = Field(default="")
    address: str = Field(default="")
    # v1 alignment: line_boss_user_id → line_user_id (the owner's LINE user ID)
    line_user_id: str = Field(default="")
    timezone: str = Field(default="Asia/Taipei")
    plan: str = Field(default="trial")              # trial | starter | growth | pro
    plan_expires_at: Optional[datetime] = Field(default=None)
    is_active: bool = Field(default=True)
    quiet_hours_start: Optional[int] = Field(default=None)   # hour 0-23
    quiet_hours_end: Optional[int] = Field(default=None)     # hour 0-23
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WorkflowRunTable(SQLModel, table=True):
    """Renamed from WorkflowRecordTable to align with v1 WorkflowRun."""
    __tablename__ = "kachu_workflow_runs"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    agentos_run_id: str = Field(index=True)
    agentos_task_id: str = Field(default="")
    workflow_type: str = Field(default="")    # photo_content | review_reply | line_faq
    trigger_source: str = Field(default="")  # line | google_webhook | schedule
    trigger_payload: str = Field(default="{}")  # JSON string
    status: str = Field(default="running")   # running | completed | failed
    # v1 alignment additions
    langfuse_trace_id: Optional[str] = Field(default=None)
    langgraph_thread_id: Optional[str] = Field(default=None)
    input_data: Optional[str] = Field(default=None)    # JSON
    output_data: Optional[str] = Field(default=None)   # JSON
    error_message: Optional[str] = Field(default=None)
    completed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ApprovalTaskTable(SQLModel, table=True):
    """Renamed from PendingApprovalTable to align with v1 ApprovalTask."""
    __tablename__ = "kachu_approval_tasks"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    agentos_run_id: str = Field(index=True, unique=True)
    workflow_type: str = Field(default="")
    task_type: Optional[str] = Field(default=None)   # photo_content | review_reply | google_post
    draft_content: str = Field(default="{}")          # JSON string of draft content
    ai_draft: Optional[str] = Field(default=None)     # original AI-generated draft
    final_content: Optional[str] = Field(default=None)  # after owner edits
    edit_diff: Optional[str] = Field(default=None)    # JSON diff for preference learning
    priority: str = Field(default="normal")           # urgent | normal | low
    line_message_id: Optional[str] = Field(default=None)
    # v1 status: pending | approved | modified | cancelled | timeout
    status: str = Field(default="pending")
    decision: Optional[str] = Field(default=None)     # approved | rejected | modified
    actor_line_id: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    decided_at: Optional[datetime] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)


class ScheduledPublishTable(SQLModel, table=True):
    __tablename__ = "kachu_scheduled_publishes"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    source_run_id: str = Field(default="", index=True)
    workflow_type: str = Field(default="")
    selected_platforms: str = Field(default="[]")  # JSON list[str]
    draft_content: str = Field(default="{}")       # JSON payload used at publish time
    status: str = Field(default="pending", index=True)
    actor_line_id: str = Field(default="")
    scheduled_for: datetime = Field(index=True)
    confirmed_at: Optional[datetime] = Field(default=None)
    published_at: Optional[datetime] = Field(default=None)
    cancelled_at: Optional[datetime] = Field(default=None)
    error_message: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeEntryTable(SQLModel, table=True):
    __tablename__ = "kachu_knowledge_entries"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    # Categories: core_value | pain_point | goal | product | style | contact
    #             basic_info | document | preference | episode
    # (v1 folds preference & episodic memory into knowledge_entries)
    category: str = Field(default="")
    content: str = Field(default="")
    source_type: str = Field(default="conversation")
    # document | conversation | photo | review | platform_data | edit
    source_id: Optional[str] = Field(default=None)
    # v1 alignment: qdrant_point_id replaces inline embedding for production
    qdrant_point_id: Optional[str] = Field(default=None)
    # status: active | conflict | superseded | archived
    status: str = Field(default="active")
    conflict_with: Optional[str] = Field(default=None)  # ID of conflicting entry
    # Phase 1 compat: inline embedding for in-process semantic search
    # Production path: use qdrant_point_id + Qdrant
    embedding: Optional[str] = Field(default=None)   # JSON-serialised list[float]
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeDocumentTable(SQLModel, table=True):
    """Uploaded documents (PDF, Word, images) before chunking."""
    __tablename__ = "kachu_knowledge_documents"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    filename: str = Field(default="")
    file_type: str = Field(default="")       # pdf | docx | image | text
    storage_path: str = Field(default="")
    parse_status: str = Field(default="pending")  # pending | processing | done | failed
    created_at: datetime = Field(default_factory=utcnow)


class KnowledgeChunkTable(SQLModel, table=True):
    """Scene-aware chunks derived from KnowledgeDocuments."""
    __tablename__ = "kachu_knowledge_chunks"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    document_id: Optional[str] = Field(default=None, index=True)
    # chunk_type: menu_item | marketing_copy | qa_pair | review | weekly_report | photo_analysis
    chunk_type: str = Field(default="")
    content: str = Field(default="")
    qdrant_point_id: Optional[str] = Field(default=None)
    version: int = Field(default=1)
    is_archived: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow)


class ConnectorAccountTable(SQLModel, table=True):
    """OAuth tokens for connected platforms (LINE OA, Google, Meta, etc.)."""
    __tablename__ = "kachu_connector_accounts"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    platform: str = Field(default="")          # line | google_business | ga4 | meta
    account_label: str = Field(default="")
    # Encrypted JSON blob: {access_token, refresh_token, expires_at, scope, ...}
    credentials_encrypted: str = Field(default="")
    is_active: bool = Field(default=True)
    last_refreshed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TenantLlmBudgetTable(SQLModel, table=True):
    """Per-tenant LiteLLM virtual key and budget tracking."""
    __tablename__ = "kachu_tenant_llm_budgets"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True, unique=True)
    monthly_budget_usd: float = Field(default=5.0)
    budget_duration: str = Field(default="monthly")
    litellm_key_alias: Optional[str] = Field(default=None)
    litellm_virtual_key_encrypted: Optional[str] = Field(default=None)
    enabled: bool = Field(default=True)
    last_synced_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class RetrievalFeedbackTable(SQLModel, table=True):
    """Owner feedback on RAG retrieval quality (thumbs up/down)."""
    __tablename__ = "kachu_retrieval_feedback"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_run_id: Optional[str] = Field(default=None)
    query: str = Field(default="")
    retrieved_chunk_id: Optional[str] = Field(default=None)
    feedback: str = Field(default="")     # positive | negative
    created_at: datetime = Field(default_factory=utcnow)


class InviteCodeTable(SQLModel, table=True):
    """Invite codes for tenant registration."""
    __tablename__ = "kachu_invite_codes"

    id: str = Field(default_factory=new_id, primary_key=True)
    code: str = Field(index=True, unique=True)
    max_uses: int = Field(default=1)
    used_count: int = Field(default=0)
    is_active: bool = Field(default=True)
    created_by: Optional[str] = Field(default=None)
    expires_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class EditSessionTable(SQLModel, table=True):
    """Tracks an active in-progress edit conversation with the boss (v2-specific)."""
    __tablename__ = "kachu_edit_sessions"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    run_id: str = Field(default="")
    original_ig_draft: str = Field(default="")
    original_google_draft: str = Field(default="")
    edited_ig_draft: str = Field(default="")  # Set by boss when responding to IG edit prompt
    edited_google_draft: str = Field(default="")  # Set by boss when responding to Google edit prompt
    step: str = Field(default="waiting_feedback")  # waiting_feedback | waiting_ig | waiting_google | completed
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ConversationTable(SQLModel, table=True):
    __tablename__ = "kachu_conversations"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    # v1 roles: ai | owner | customer | platform
    role: str = Field(default="")
    content: str = Field(default="")
    conversation_type: str = Field(default="onboarding")  # onboarding | general
    timestamp: datetime = Field(default_factory=utcnow)


class OnboardingStateTable(SQLModel, table=True):
    __tablename__ = "kachu_onboarding_states"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True, unique=True)
    step: str = Field(default="new")
    # new | asking_name | asking_industry | asking_address
    # awaiting_docs | interview_q1 | interview_q2 | interview_q3 | completed
    extra: str = Field(default="{}")  # JSON for temp storage
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


# ── Backward-compatibility aliases (old names used in migration paths) ────────
WorkflowRecordTable = WorkflowRunTable
PendingApprovalTable = ApprovalTaskTable


class PushLogTable(SQLModel, table=True):
    """Records each LINE push message for rate-limiting enforcement."""
    __tablename__ = "kachu_push_logs"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    recipient_line_id: str = Field(default="")
    message_type: str = Field(default="")   # approval | report | escalation | general
    pushed_at: datetime = Field(default_factory=utcnow, index=True)


class AuditEventTable(SQLModel, table=True):
    """Phase 6 audit trail for approval, push, and publish activity."""
    __tablename__ = "kachu_audit_events"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    agentos_run_id: str = Field(default="", index=True)
    agentos_task_id: str = Field(default="", index=True)
    workflow_type: str = Field(default="", index=True)
    event_type: str = Field(default="", index=True)
    actor_id: Optional[str] = Field(default=None)
    source: str = Field(default="")
    payload: str = Field(default="{}")
    created_at: datetime = Field(default_factory=utcnow, index=True)


# ── Phase 4: Adaptive Approval Policy ────────────────────────────────────────

class TenantApprovalProfileTable(SQLModel, table=True):
    """Computed approval behaviour statistics per tenant (Phase 4)."""
    __tablename__ = "kachu_tenant_approval_profiles"

    tenant_id: str = Field(primary_key=True)
    # Fraction of approval decisions that were "approved" (not rejected/edited) in last 30 days.
    recent_acceptance_rate: float = Field(default=0.0)
    # Average edit magnitude (0.0 = no change, 1.0 = total rewrite).
    median_edit_delta: float = Field(default=0.0)
    # Seconds between approval creation and boss decision.
    avg_approval_latency_seconds: float = Field(default=86400.0)
    # Total decisions included in the above stats.
    total_decisions: int = Field(default=0)
    updated_at: datetime = Field(default_factory=utcnow)


class TenantAutomationSettingsTable(SQLModel, table=True):
    """Per-tenant automation cadence and timing configuration."""
    __tablename__ = "kachu_tenant_automation_settings"

    tenant_id: str = Field(primary_key=True)
    ga_report_enabled: bool = Field(default=True)
    ga_report_frequency: str = Field(default="weekly")
    ga_report_weekday: str = Field(default="mon")
    ga_report_hour: int = Field(default=8)
    google_post_enabled: bool = Field(default=True)
    google_post_frequency: str = Field(default="weekly")
    google_post_weekday: str = Field(default="thu")
    google_post_hour: int = Field(default=10)
    meta_post_enabled: bool = Field(default=False)
    meta_post_frequency: str = Field(default="weekly")
    meta_post_weekday: str = Field(default="fri")
    meta_post_hour: int = Field(default=11)
    proactive_enabled: bool = Field(default=True)
    proactive_hour: int = Field(default=7)
    content_calendar_enabled: bool = Field(default=True)
    content_calendar_day: int = Field(default=1)
    content_calendar_hour: int = Field(default=9)
    updated_at: datetime = Field(default_factory=utcnow)


# ── Phase 5: Cross-Workflow Shared Context ────────────────────────────────────

class SharedContextTable(SQLModel, table=True):
    """Key-value store for cross-workflow context hints (Phase 5)."""
    __tablename__ = "kachu_shared_contexts"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    # context_type: ga4_recommendations | last_post_topic | review_faq_candidate
    context_type: str = Field(index=True)
    content: str = Field(default="{}")   # JSON payload
    source_run_id: str = Field(default="")
    expires_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)


class DeferredDispatchTable(SQLModel, table=True):
    """Recoverable AgentOS dispatch backlog for transient outages."""
    __tablename__ = "kachu_deferred_dispatches"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_type: str = Field(default="", index=True)
    task_request_json: str = Field(default="{}")
    trigger_source: str = Field(default="")
    trigger_payload: str = Field(default="{}")
    status: str = Field(default="pending", index=True)  # pending | dispatched | failed
    attempts: int = Field(default=0)
    last_error: str = Field(default="")
    next_retry_at: datetime = Field(default_factory=utcnow, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
