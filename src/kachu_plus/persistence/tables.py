from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Optional
from uuid import uuid4

from sqlalchemy import Column, Text
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return str(uuid4())


class TenantTable(SQLModel, table=True):
    """
    Kachu+ 租戶主檔。
    - sleep_threshold：模組三 sleep 計算用，onboarding 時由商家設定（預設 60 天）
    - line_user_id 不帶入（Kachu_v2 legacy），owner 身份由 kachu_tenant_memberships 管理（任務 1-2 後建）
    """
    __tablename__ = "kachu_tenants"

    id: str = Field(default_factory=new_id, primary_key=True)
    name: str = Field(default="")
    industry_type: str = Field(default="")          # beauty / restaurant / cafe / retail
    address: str = Field(default="")
    timezone: str = Field(default="Asia/Taipei")
    plan: str = Field(default="trial")               # trial / starter / growth / pro
    plan_expires_at: Optional[datetime] = Field(default=None)
    merchant_slug: str = Field(default="")
    is_active: bool = Field(default=True)
    sleep_threshold: int = Field(default=60)         # 沉睡天數閾值，模組三排程計算用
    quiet_hours_start: Optional[int] = Field(default=None)  # 0–23
    quiet_hours_end: Optional[int] = Field(default=None)    # 0–23
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class TenantMembershipTable(SQLModel, table=True):
    __tablename__ = "kachu_tenant_memberships"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    line_user_id: str = Field(index=True)
    role: str = Field(default="owner")
    display_name: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class LineChannelConfigTable(SQLModel, table=True):
    """
    每個 tenant 的 LINE Messaging API 憑證。
    - 一個 tenant 只能綁定一組 LINE channel（UNIQUE tenant_id）
    - channel_secret 用於 webhook signature 驗證（渠道 R3）
    - 憑證值在 application layer 加密後存入，不明文儲存（TODO: 換 secrets manager）
    """
    __tablename__ = "kachu_line_channel_configs"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True, unique=True)
    channel_access_token: str = Field(default="")
    channel_secret: str = Field(default="")
    channel_id: str = Field(default="")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class WebhookEventTable(SQLModel, table=True):
    """
    供 webhook event 去重與 raw payload 審計使用。
    同一 tenant/provider/dedupe_key 只能存在一筆有效記錄。
    """
    __tablename__ = "kachu_webhook_events"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    provider: str = Field(default="line", index=True)
    dedupe_key: str = Field(default="", index=True)
    event_type: str = Field(default="", index=True)
    external_event_id: str = Field(default="", index=True)
    external_user_id: str = Field(default="", index=True)
    external_thread_id: str = Field(default="", index=True)
    occurred_at: Optional[datetime] = Field(default=None, index=True)
    received_at: datetime = Field(default_factory=utcnow, index=True)
    raw_payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)


class ConversationTable(SQLModel, table=True):
    """
    原始對話記憶層。
    保存 boss / ai / customer / platform 的逐輪對話，供 brief、follow-up 判定與後續 memory promotion 使用。
    """

    __tablename__ = "kachu_conversations"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    line_user_id: str = Field(default="", index=True)
    actor_role: str = Field(default="", index=True)
    channel_type: str = Field(default="line", index=True)
    conversation_kind: str = Field(default="", index=True)
    content_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    source_message_id: str = Field(default="", index=True)
    related_task_id: str = Field(default="", index=True)
    related_run_id: str = Field(default="", index=True)
    metadata_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class OnboardingStateTable(SQLModel, table=True):
    """
    Onboarding 狀態機追蹤（每個 tenant 一筆）。

    Steps：new → asking_name → asking_industry → asking_sleep_threshold
           → asking_address → awaiting_docs
           → interview_q1 → interview_q2 → interview_q3 → completed
    """
    __tablename__ = "kachu_onboarding_states"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True, unique=True)
    step: str = Field(default="new")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class KnowledgeEntryTable(SQLModel, table=True):
    """
    儲存 onboarding 訪談答案與後續知識累積。
    categories: core_value | pain_point | goal | basic_info
    """
    __tablename__ = "kachu_knowledge_entries"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    category: str = Field(default="")
    content: str = Field(default="")
    source_type: str = Field(default="conversation")
    source_conversation_id: str = Field(default="", index=True)
    status: str = Field(default="active", index=True)
    confidence_score: float = Field(default=1.0)
    supersedes_entry_id: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow)


class CustomerProfileTable(SQLModel, table=True):
    """
    顧客主檔。
    模組三先用這張表支撐沉睡查詢；status 與 opt_out 會影響查詢結果是否可見。
    """
    __tablename__ = "kachu_customer_profiles"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    display_name: str = Field(default="")
    custom_name: str = Field(default="")
    status: str = Field(default="active")
    last_interaction_at: Optional[datetime] = Field(default=None)
    interaction_count: int = Field(default=0)
    sleep_since_days: int = Field(default=0, index=True)
    opt_out: bool = Field(default=False)
    merged_into_profile_id: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ChannelEntityTable(SQLModel, table=True):
    """
    渠道身份表。
    v1 只有 LINE，但 schema 從第一天保留多渠道結構。
    """
    __tablename__ = "kachu_channel_entities"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    channel_type: str = Field(default="line")
    external_user_id: str = Field(default="", index=True)
    reachability_status: str = Field(default="reachable")
    occurred_at: Optional[datetime] = Field(default=None)
    received_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProfileLinkTable(SQLModel, table=True):
    """
    profile 與 channel entity 的連結。
    """
    __tablename__ = "kachu_profile_links"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    profile_id: str = Field(index=True)
    channel_entity_id: str = Field(index=True, unique=True)
    confidence_score: float = Field(default=1.0)
    resolution_source: str = Field(default="manual")
    resolution_note: str = Field(default="")
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ProfileMergeAuditTable(SQLModel, table=True):
    __tablename__ = "kachu_profile_merge_audits"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    source_profile_id: str = Field(index=True)
    target_profile_id: str = Field(index=True)
    actor_line_id: str = Field(default="", index=True)
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    summary_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ConversationHandoffLockTable(SQLModel, table=True):
    __tablename__ = "kachu_conversation_handoff_locks"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    channel_type: str = Field(default="line", index=True)
    external_user_id: str = Field(default="", index=True)
    reason: str = Field(default="human_handoff", sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=True, index=True)
    locked_by_line_user_id: str = Field(default="", index=True)
    released_by_line_user_id: str = Field(default="", index=True)
    locked_at: datetime = Field(default_factory=utcnow, index=True)
    released_at: Optional[datetime] = Field(default=None, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class CustomerTagDefinitionTable(SQLModel, table=True):
    """
    手動 tag 定義。
    R8 要求刪除 tag 僅移除未來可用性，因此這裡採 soft delete。
    """
    __tablename__ = "kachu_customer_tag_definitions"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    name: str = Field(default="")
    color: str = Field(default="")
    source: str = Field(default="manual")
    is_active: bool = Field(default=True)
    deleted_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CustomerTagAssignmentTable(SQLModel, table=True):
    """
    profile 與 tag 的目前關聯；移除時保留 row，避免歷史資料失聯。
    """
    __tablename__ = "kachu_customer_tag_assignments"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    profile_id: str = Field(index=True)
    tag_id: str = Field(index=True)
    applied_source: str = Field(default="manual")
    applied_at: datetime = Field(default_factory=utcnow)
    removed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CustomerTimelineEventTable(SQLModel, table=True):
    """
    客戶時間線事件。
    payload_json 以 snapshot 形式保留 tag 當下名稱，避免 tag 停用後歷史失真。
    """
    __tablename__ = "kachu_customer_timeline_events"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    profile_id: str = Field(index=True)
    activity_type: str = Field(default="")
    title: str = Field(default="")
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)

    @classmethod
    def build_payload(cls, **payload: object) -> str:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class ConnectorAccountTable(SQLModel, table=True):
    """
    平台 connector 憑證。
    v1 先供 google_business 使用，schema 保持通用以支援後續渠道擴充。
    """
    __tablename__ = "kachu_connector_accounts"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    platform: str = Field(default="")
    account_label: str = Field(default="")
    credentials_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    is_active: bool = Field(default=True)
    last_refreshed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class MetaOAuthSessionTable(SQLModel, table=True):
    """
    Meta OAuth 流程暫存狀態。
    用來承接 LINE 發起 -> Meta callback -> Page 選擇 -> 覆蓋確認 -> 完成綁定 的跨頁流程。
    """
    __tablename__ = "kachu_meta_oauth_sessions"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    line_user_id: str = Field(default="", index=True)
    state: str = Field(default="", index=True, unique=True)
    status: str = Field(default="pending", index=True)
    requested_platform: str = Field(default="meta")
    page_candidates_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    selected_page_id: str = Field(default="")
    selected_page_name: str = Field(default="")
    selected_ig_user_id: str = Field(default="")
    user_access_token: str = Field(default="")
    fb_page_access_token: str = Field(default="")
    error_code: str = Field(default="")
    error_message: str = Field(default="")
    expires_at: Optional[datetime] = Field(default=None, index=True)
    completed_at: Optional[datetime] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class ExecuteTaskRecordTable(SQLModel, table=True):
    __tablename__ = "kachu_execute_task_records"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    line_user_id: str = Field(default="", index=True)
    intent_label: str = Field(default="", index=True)
    source_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    objective: str = Field(default="", sa_column=Column(Text, nullable=False))
    task_id: str = Field(default="", index=True, unique=True)
    run_id: str = Field(default="", index=True)
    related_conversation_id: str = Field(default="", index=True)
    status: str = Field(default="created", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class PendingAssetIntentTable(SQLModel, table=True):
    __tablename__ = "kachu_pending_asset_intents"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    line_user_id: str = Field(default="", index=True)
    line_message_id: str = Field(default="", index=True)
    asset_type: str = Field(default="image", index=True)
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", index=True)
    selected_decision: str = Field(default="", index=True)
    expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)
    resolved_at: Optional[datetime] = Field(default=None, index=True)


class PendingApprovalTable(SQLModel, table=True):
    __tablename__ = "kachu_pending_approvals"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    agentos_task_id: str = Field(default="", index=True)
    agentos_run_id: str = Field(default="", index=True, unique=True)
    workflow_type: str = Field(default="")
    review_id: str = Field(default="")
    draft_content: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", index=True)
    actor_line_id: str = Field(default="")
    decision_payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    decided_at: Optional[datetime] = Field(default=None)


class PublishedContentRecordTable(SQLModel, table=True):
    __tablename__ = "kachu_published_content_records"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_type: str = Field(default="", index=True)
    channel: str = Field(default="", index=True)
    source_id: str = Field(default="", index=True)
    source_ref: str = Field(default="")
    content_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ContentPlanTable(SQLModel, table=True):
    __tablename__ = "kachu_content_plans"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    source_conversation_id: str = Field(default="", index=True)
    created_by_line_user_id: str = Field(default="", index=True)
    objective: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="active", index=True)
    plan_payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class ContentPlanItemTable(SQLModel, table=True):
    __tablename__ = "kachu_content_plan_items"

    id: str = Field(default_factory=new_id, primary_key=True)
    content_plan_id: str = Field(index=True)
    tenant_id: str = Field(index=True)
    title: str = Field(default="")
    workflow_type: str = Field(default="kachu_planned_content", index=True)
    selected_platforms_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    draft_payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    status: str = Field(default="planned", index=True)
    scheduled_for: Optional[datetime] = Field(default=None, index=True)
    pending_run_id: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class ExternalEngagementTable(SQLModel, table=True):
    __tablename__ = "kachu_external_engagements"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    platform: str = Field(default="meta", index=True)
    engagement_type: str = Field(default="comment", index=True)
    external_thread_id: str = Field(default="", index=True)
    external_message_id: str = Field(default="", index=True, unique=True)
    author_name: str = Field(default="")
    author_id: str = Field(default="", index=True)
    content_text: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="received", index=True)
    reply_draft: str = Field(default="", sa_column=Column(Text, nullable=False))
    related_run_id: str = Field(default="", index=True)
    source_payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow, index=True)


class SuggestionTable(SQLModel, table=True):
    __tablename__ = "kachu_suggestions"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    suggestion_type: str = Field(default="", index=True)
    category: str = Field(default="", index=True)
    title: str = Field(default="")
    reason: str = Field(default="", sa_column=Column(Text, nullable=False))
    body: str = Field(default="", sa_column=Column(Text, nullable=False))
    affected_profile_ids_json: str = Field(default="[]", sa_column=Column(Text, nullable=False))
    profile_count: int = Field(default=0)
    suggested_action: str = Field(default="", sa_column=Column(Text, nullable=False))
    draft_message: str = Field(default="", sa_column=Column(Text, nullable=False))
    status: str = Field(default="pending", index=True)
    related_run_id: str = Field(default="", index=True)
    payload_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    expires_at: Optional[datetime] = Field(default=None, index=True)
    sent_at: Optional[datetime] = Field(default=None, index=True)
    result_snapshot_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class RecurringJobTable(SQLModel, table=True):
    __tablename__ = "kachu_recurring_jobs"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    job_type: str = Field(default="", index=True)
    next_run_at: datetime = Field(default_factory=utcnow, index=True)
    last_run_at: Optional[datetime] = Field(default=None, index=True)
    locked_until: Optional[datetime] = Field(default=None, index=True)
    last_result_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class PreferenceMemoryTable(SQLModel, table=True):
    __tablename__ = "kachu_preference_memories"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    platform: str = Field(default="", index=True)
    original_draft: str = Field(default="", sa_column=Column(Text, nullable=False))
    edited_draft: str = Field(default="", sa_column=Column(Text, nullable=False))
    diff_notes: str = Field(default="", sa_column=Column(Text, nullable=False))
    run_id: str = Field(default="", index=True)
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ContextBriefTable(SQLModel, table=True):
    __tablename__ = "kachu_context_briefs"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    brief_type: str = Field(default="", index=True)
    content_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    expires_at: Optional[datetime] = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class EpisodicMemoryTable(SQLModel, table=True):
    __tablename__ = "kachu_episodic_memories"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True)
    workflow_type: str = Field(default="", index=True)
    outcome: str = Field(default="", index=True)
    context_summary_json: str = Field(default="{}", sa_column=Column(Text, nullable=False))
    created_at: datetime = Field(default_factory=utcnow, index=True)


class ApprovalProfileTable(SQLModel, table=True):
    __tablename__ = "kachu_approval_profiles"

    id: str = Field(default_factory=new_id, primary_key=True)
    tenant_id: str = Field(index=True, unique=True)
    total_decisions: int = Field(default=0)
    recent_acceptance_rate: float = Field(default=0.0)
    median_edit_delta: float = Field(default=0.0)
    updated_at: datetime = Field(default_factory=utcnow)
