from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from .tables import (
    ApprovalTaskTable,
    AuditEventTable,
    ConnectorAccountTable,
    ConversationTable,
    DeferredDispatchTable,
    EditSessionTable,
    KnowledgeChunkTable,
    KnowledgeDocumentTable,
    KnowledgeEntryTable,
    OnboardingStateTable,
    PushLogTable,
    RetrievalFeedbackTable,
    ScheduledPublishTable,
    SharedContextTable,
    TenantApprovalProfileTable,
    TenantAutomationSettingsTable,
    TenantFeatureFlagTable,
    TenantLlmBudgetTable,
    TenantMembershipTable,
    TenantTable,
    WorkflowRunTable,
    # Backward-compat aliases
    PendingApprovalTable,
    WorkflowRecordTable,
)


def _normalize_google_location(value: str) -> str:
    text = str(value or "").strip().strip("/")
    if not text:
        return ""
    if "/locations/" in text:
        return text.rsplit("/", 1)[-1]
    if text.startswith("locations/"):
        return text.split("/", 1)[-1]
    return text


def _serialize_export_value(value):
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_serialize_export_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize_export_value(item) for key, item in value.items()}
    return value


def _serialize_model(model: object) -> dict:
    if hasattr(model, "model_dump"):
        return _serialize_export_value(model.model_dump())
    return {
        key: _serialize_export_value(value)
        for key, value in vars(model).items()
        if not key.startswith("_")
    }


def _load_connector_credentials(account: ConnectorAccountTable | None) -> dict[str, object]:
    if account is None or not getattr(account, "credentials_encrypted", ""):
        return {}
    try:
        payload = json.loads(account.credentials_encrypted)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


_KNOWLEDGE_VALID_STATUSES = {"active", "stale", "conflict", "superseded", "archived"}
_KNOWLEDGE_AUTO_STALE_EXCLUDED_CATEGORIES = {"basic_info", "core_value", "preference", "episode"}


def _knowledge_reference_at(entry: KnowledgeEntryTable) -> datetime:
    candidates = [
        _normalize_utc_datetime(entry.updated_at),
        _normalize_utc_datetime(getattr(entry, "last_retrieved_at", None)),
        _normalize_utc_datetime(getattr(entry, "last_reviewed_at", None)),
    ]
    timestamps = [timestamp for timestamp in candidates if timestamp is not None]
    return max(timestamps) if timestamps else datetime.now(timezone.utc)


def _normalize_utc_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _knowledge_is_auto_stale_candidate(
    entry: KnowledgeEntryTable,
    *,
    cutoff: datetime,
) -> bool:
    if entry.status != "active":
        return False
    if entry.category in _KNOWLEDGE_AUTO_STALE_EXCLUDED_CATEGORIES:
        return False
    return _knowledge_reference_at(entry) <= cutoff


def _knowledge_status_counts(entries: list[KnowledgeEntryTable]) -> dict[str, int]:
    counts = {status: 0 for status in _KNOWLEDGE_VALID_STATUSES}
    for entry in entries:
        counts[entry.status] = counts.get(entry.status, 0) + 1
    return counts


class KachuRepository:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # ── Tenant ────────────────────────────────────────────────────────────────

    def get_or_create_tenant(self, tenant_id: str) -> TenantTable:
        with Session(self._engine) as session:
            tenant = session.get(TenantTable, tenant_id)
            if tenant is None:
                tenant = TenantTable(id=tenant_id)
                session.add(tenant)
                session.commit()
                tenant = session.get(TenantTable, tenant_id) or tenant
            return tenant

    def save_tenant(self, tenant: TenantTable) -> TenantTable:
        tenant.updated_at = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
            return tenant

    def update_tenant_plan(
        self,
        tenant_id: str,
        *,
        plan: str,
        plan_expires_at: datetime | None,
        merchant_slug: str | None = None,
    ) -> TenantTable:
        tenant = self.get_or_create_tenant(tenant_id)
        tenant.plan = str(plan or "trial").strip().lower() or "trial"
        tenant.plan_expires_at = plan_expires_at
        if merchant_slug is not None:
            tenant.merchant_slug = str(merchant_slug or "").strip()
        return self.save_tenant(tenant)

    def list_active_tenant_ids(self) -> list[str]:
        """Return IDs of all tenants with is_active=True."""
        with Session(self._engine) as session:
            results = session.exec(
                select(TenantTable).where(TenantTable.is_active == True)  # noqa: E712
            ).all()
            return [t.id for t in results]

    def get_tenant(self, tenant_id: str) -> TenantTable | None:
        with Session(self._engine) as session:
            return session.get(TenantTable, tenant_id)

    def create_tenant_membership(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        role: str = "owner",
        display_name: str = "",
    ) -> TenantMembershipTable:
        normalized_line_user_id = str(line_user_id).strip()
        if not normalized_line_user_id:
            raise ValueError("line_user_id is required")

        normalized_role = str(role or "owner").strip().lower() or "owner"
        if normalized_role not in {"owner", "manager"}:
            raise ValueError("role must be one of: owner, manager")

        normalized_display_name = str(display_name or "").strip()
        self.get_or_create_tenant(tenant_id)

        with Session(self._engine) as session:
            conflict_stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.line_user_id == normalized_line_user_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
                .where(TenantMembershipTable.tenant_id != tenant_id)
            )
            conflict = session.exec(conflict_stmt).first()
            if conflict is not None:
                raise ValueError("line_user_id is already bound to another active tenant")

            existing_stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.line_user_id == normalized_line_user_id)
            )
            existing = session.exec(existing_stmt).first()
            if existing is not None:
                existing.role = normalized_role
                existing.display_name = normalized_display_name
                existing.is_active = True
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing

            membership = TenantMembershipTable(
                tenant_id=tenant_id,
                line_user_id=normalized_line_user_id,
                role=normalized_role,
                display_name=normalized_display_name,
            )
            session.add(membership)
            session.commit()
            session.refresh(membership)
            return membership

    def get_active_membership_by_line_user_id(self, line_user_id: str) -> TenantMembershipTable | None:
        normalized_line_user_id = str(line_user_id).strip()
        if not normalized_line_user_id:
            return None
        with Session(self._engine) as session:
            stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.line_user_id == normalized_line_user_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
            )
            return session.exec(stmt).first()

    def list_active_memberships(self, tenant_id: str) -> list[TenantMembershipTable]:
        with Session(self._engine) as session:
            stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
                .order_by(TenantMembershipTable.created_at.asc())
            )
            return list(session.exec(stmt).all())

    def get_owner_line_user_ids(self, tenant_id: str) -> list[str]:
        with Session(self._engine) as session:
            stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
                .where(TenantMembershipTable.role == "owner")
                .order_by(TenantMembershipTable.created_at.asc())
            )
            return [row.line_user_id for row in session.exec(stmt).all() if row.line_user_id]

    def get_notification_line_user_ids(self, tenant_id: str) -> list[str]:
        with Session(self._engine) as session:
            stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
                .where(TenantMembershipTable.role.in_(["owner", "manager"]))
                .order_by(TenantMembershipTable.created_at.asc())
            )
            recipients: list[str] = []
            for row in session.exec(stmt).all():
                normalized_line_user_id = str(row.line_user_id or "").strip()
                if normalized_line_user_id and normalized_line_user_id not in recipients:
                    recipients.append(normalized_line_user_id)
            return recipients

    def deactivate_tenant_membership(self, membership_id: str) -> TenantMembershipTable | None:
        with Session(self._engine) as session:
            membership = session.get(TenantMembershipTable, membership_id)
            if membership is None:
                return None
            membership.is_active = False
            membership.updated_at = datetime.now(timezone.utc)
            session.add(membership)
            session.commit()
            session.refresh(membership)
            return membership

    def deactivate_tenant(self, tenant_id: str) -> TenantTable | None:
        with Session(self._engine) as session:
            tenant = session.get(TenantTable, tenant_id)
            if tenant is None:
                return None

            tenant.is_active = False
            tenant.updated_at = datetime.now(timezone.utc)
            session.add(tenant)

            membership_stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
            )
            for membership in session.exec(membership_stmt).all():
                membership.is_active = False
                membership.updated_at = datetime.now(timezone.utc)
                session.add(membership)

            connector_stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            for connector in session.exec(connector_stmt).all():
                connector.is_active = False
                connector.updated_at = datetime.now(timezone.utc)
                session.add(connector)

            session.commit()
            session.refresh(tenant)
            return tenant

    def export_tenant_bundle(self, tenant_id: str) -> dict | None:
        with Session(self._engine) as session:
            tenant = session.get(TenantTable, tenant_id)
            if tenant is None:
                return None

            def _all(model):
                return list(session.exec(select(model).where(model.tenant_id == tenant_id)).all())

            return {
                "tenant": _serialize_model(tenant),
                "memberships": [_serialize_model(row) for row in _all(TenantMembershipTable)],
                "connectors": [_serialize_model(row) for row in _all(ConnectorAccountTable)],
                "workflow_runs": [_serialize_model(row) for row in _all(WorkflowRunTable)],
                "approval_tasks": [_serialize_model(row) for row in _all(ApprovalTaskTable)],
                "scheduled_publishes": [_serialize_model(row) for row in _all(ScheduledPublishTable)],
                "knowledge_entries": [_serialize_model(row) for row in _all(KnowledgeEntryTable)],
                "knowledge_documents": [_serialize_model(row) for row in _all(KnowledgeDocumentTable)],
                "knowledge_chunks": [_serialize_model(row) for row in _all(KnowledgeChunkTable)],
                "conversations": [_serialize_model(row) for row in _all(ConversationTable)],
                "onboarding_states": [_serialize_model(row) for row in _all(OnboardingStateTable)],
                "shared_contexts": [_serialize_model(row) for row in _all(SharedContextTable)],
                "deferred_dispatches": [_serialize_model(row) for row in _all(DeferredDispatchTable)],
                "push_logs": [_serialize_model(row) for row in _all(PushLogTable)],
                "audit_events": [_serialize_model(row) for row in _all(AuditEventTable)],
                "approval_profile": [_serialize_model(row) for row in _all(TenantApprovalProfileTable)],
                "automation_settings": [_serialize_model(row) for row in _all(TenantAutomationSettingsTable)],
                "feature_flags": [_serialize_model(row) for row in _all(TenantFeatureFlagTable)],
                "llm_budgets": [_serialize_model(row) for row in _all(TenantLlmBudgetTable)],
                "retrieval_feedback": [_serialize_model(row) for row in _all(RetrievalFeedbackTable)],
                "edit_sessions": [_serialize_model(row) for row in _all(EditSessionTable)],
            }

    def delete_tenant_bundle(self, tenant_id: str) -> dict[str, int] | None:
        with Session(self._engine) as session:
            tenant = session.get(TenantTable, tenant_id)
            if tenant is None:
                return None

            counts: dict[str, int] = {}
            tenant_scoped_models = [
                DeferredDispatchTable,
                SharedContextTable,
                AuditEventTable,
                PushLogTable,
                EditSessionTable,
                RetrievalFeedbackTable,
                TenantLlmBudgetTable,
                TenantFeatureFlagTable,
                TenantAutomationSettingsTable,
                TenantApprovalProfileTable,
                KnowledgeChunkTable,
                KnowledgeDocumentTable,
                KnowledgeEntryTable,
                ScheduledPublishTable,
                ApprovalTaskTable,
                WorkflowRunTable,
                ConnectorAccountTable,
                ConversationTable,
                OnboardingStateTable,
                TenantMembershipTable,
            ]
            for model in tenant_scoped_models:
                rows = list(session.exec(select(model).where(model.tenant_id == tenant_id)).all())
                counts[model.__name__] = len(rows)
                for row in rows:
                    session.delete(row)

            counts["TenantTable"] = 1
            session.delete(tenant)
            session.commit()
            return counts

    # ── WorkflowRun (v1-aligned; backward-compat aliases kept below) ──────────

    def create_workflow_run(
        self,
        *,
        tenant_id: str,
        agentos_run_id: str,
        agentos_task_id: str,
        workflow_type: str,
        trigger_source: str,
        trigger_payload: dict,
    ) -> WorkflowRunTable:
        record = WorkflowRunTable(
            tenant_id=tenant_id,
            agentos_run_id=agentos_run_id,
            agentos_task_id=agentos_task_id,
            workflow_type=workflow_type,
            trigger_source=trigger_source,
            trigger_payload=json.dumps(trigger_payload, ensure_ascii=False),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    # Backward-compat alias
    def create_workflow_record(self, **kwargs) -> WorkflowRunTable:
        return self.create_workflow_run(**kwargs)

    def update_workflow_run_status(self, record_id: str, status: str) -> None:
        with Session(self._engine) as session:
            record = session.get(WorkflowRunTable, record_id)
            if record:
                record.status = status
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    # Backward-compat alias
    def update_workflow_record_status(self, record_id: str, status: str) -> None:
        self.update_workflow_run_status(record_id, status)

    def get_workflow_run_by_run_id(self, agentos_run_id: str) -> WorkflowRunTable | None:
        with Session(self._engine) as session:
            stmt = select(WorkflowRunTable).where(WorkflowRunTable.agentos_run_id == agentos_run_id)
            return session.exec(stmt).first()

    # Backward-compat alias
    def get_workflow_record_by_run_id(self, agentos_run_id: str) -> WorkflowRunTable | None:
        return self.get_workflow_run_by_run_id(agentos_run_id)

    def update_workflow_run_output(self, agentos_run_id: str, output_data: dict) -> None:
        """Persist publish results (including fb_post_id) into the workflow run record."""
        with Session(self._engine) as session:
            stmt = select(WorkflowRunTable).where(WorkflowRunTable.agentos_run_id == agentos_run_id)
            record = session.exec(stmt).first()
            if record:
                record.output_data = json.dumps(output_data, ensure_ascii=False)
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    def list_completed_photo_runs_for_perf_check(
        self, tenant_id: str, since_hours: int = 48, max_age_hours: int = 23
    ) -> list[WorkflowRunTable]:
        """Return completed photo_content runs whose created_at is between 23–48 h ago
        and that have output_data containing a fb_post_id."""
        from datetime import timedelta
        now = datetime.now(timezone.utc)
        lower = now - timedelta(hours=since_hours)
        upper = now - timedelta(hours=max_age_hours)
        with Session(self._engine) as session:
            stmt = (
                select(WorkflowRunTable)
                .where(WorkflowRunTable.tenant_id == tenant_id)
                .where(WorkflowRunTable.workflow_type == "photo_content")
                .where(WorkflowRunTable.status == "completed")
                .where(WorkflowRunTable.created_at >= lower)
                .where(WorkflowRunTable.created_at <= upper)
                .where(WorkflowRunTable.output_data.isnot(None))
            )
            rows = session.exec(stmt).all()
        result = []
        for row in rows:
            try:
                data = json.loads(row.output_data or "{}")
                if data.get("fb_post_id"):
                    result.append(row)
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    def list_comment_trackable_runs(
        self, tenant_id: str, within_days: int = 7
    ) -> list[tuple[str, str]]:
        """Return [(fb_post_id, agentos_run_id), ...] for recent completed photo_content runs
        that have a fb_post_id stored in output_data."""
        from datetime import timedelta
        lower = datetime.now(timezone.utc) - timedelta(days=within_days)
        with Session(self._engine) as session:
            stmt = (
                select(WorkflowRunTable)
                .where(WorkflowRunTable.tenant_id == tenant_id)
                .where(WorkflowRunTable.workflow_type == "photo_content")
                .where(WorkflowRunTable.status == "completed")
                .where(WorkflowRunTable.created_at >= lower)
                .where(WorkflowRunTable.output_data.isnot(None))
            )
            rows = session.exec(stmt).all()
        result = []
        for row in rows:
            try:
                data = json.loads(row.output_data or "{}")
                fb_post_id = data.get("fb_post_id", "")
                if fb_post_id:
                    result.append((fb_post_id, row.agentos_run_id))
            except (json.JSONDecodeError, TypeError):
                pass
        return result

    # ── PendingApproval ───────────────────────────────────────────────────────

    def create_pending_approval(
        self,
        *,
        tenant_id: str,
        agentos_run_id: str,
        workflow_type: str,
        draft_content: dict,
        expires_at: datetime | None = None,
    ) -> PendingApprovalTable:
        # Guard against AgentOS retries re-inserting the same run_id.
        existing = self.get_pending_approval_by_run_id(agentos_run_id)
        if existing is not None:
            return existing
        record = PendingApprovalTable(
            tenant_id=tenant_id,
            agentos_run_id=agentos_run_id,
            workflow_type=workflow_type,
            draft_content=json.dumps(draft_content, ensure_ascii=False),
            expires_at=expires_at,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_pending_approval_by_run_id(self, agentos_run_id: str) -> PendingApprovalTable | None:
        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == agentos_run_id)
            return session.exec(stmt).first()

    def decide_pending_approval(
        self,
        *,
        agentos_run_id: str,
        decision: str,
        actor_line_id: str,
    ) -> PendingApprovalTable | None:
        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == agentos_run_id)
            record = session.exec(stmt).first()
            if record is None:
                return None
            record.status = "decided"
            record.decision = decision
            record.actor_line_id = actor_line_id
            record.decided_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    # ── ScheduledPublish ─────────────────────────────────────────────────────

    def create_scheduled_publish(
        self,
        *,
        tenant_id: str,
        source_run_id: str,
        workflow_type: str,
        selected_platforms: list[str],
        draft_content: dict,
        scheduled_for: datetime,
        actor_line_id: str,
    ) -> ScheduledPublishTable:
        record = ScheduledPublishTable(
            tenant_id=tenant_id,
            source_run_id=source_run_id,
            workflow_type=workflow_type,
            selected_platforms=json.dumps(selected_platforms, ensure_ascii=False),
            draft_content=json.dumps(draft_content, ensure_ascii=False),
            actor_line_id=actor_line_id,
            scheduled_for=scheduled_for,
            confirmed_at=datetime.now(timezone.utc),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_scheduled_publish(self, scheduled_publish_id: str) -> ScheduledPublishTable | None:
        with Session(self._engine) as session:
            return session.get(ScheduledPublishTable, scheduled_publish_id)

    def get_latest_scheduled_publish_by_source_run_id(
        self,
        source_run_id: str,
    ) -> ScheduledPublishTable | None:
        with Session(self._engine) as session:
            stmt = (
                select(ScheduledPublishTable)
                .where(ScheduledPublishTable.source_run_id == source_run_id)
                .order_by(ScheduledPublishTable.created_at.desc())
            )
            return session.exec(stmt).first()

    def list_due_scheduled_publishes(
        self,
        *,
        due_before: datetime | None = None,
        limit: int = 50,
    ) -> list[ScheduledPublishTable]:
        cutoff = due_before or datetime.now(timezone.utc)
        with Session(self._engine) as session:
            stmt = (
                select(ScheduledPublishTable)
                .where(ScheduledPublishTable.status == "pending")
                .where(ScheduledPublishTable.scheduled_for <= cutoff)
                .order_by(ScheduledPublishTable.scheduled_for.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def update_scheduled_publish_status(
        self,
        scheduled_publish_id: str,
        *,
        status: str,
        error_message: str | None = None,
        published_at: datetime | None = None,
        cancelled_at: datetime | None = None,
    ) -> ScheduledPublishTable | None:
        with Session(self._engine) as session:
            record = session.get(ScheduledPublishTable, scheduled_publish_id)
            if record is None:
                return None
            record.status = status
            record.error_message = error_message
            record.published_at = published_at
            record.cancelled_at = cancelled_at
            record.updated_at = datetime.now(timezone.utc)
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    # ── KnowledgeEntry ────────────────────────────────────────────────────────

    def save_knowledge_entry(
        self,
        *,
        tenant_id: str,
        category: str,
        content: str,
        source_type: str = "conversation",
        source_id: str | None = None,
    ) -> KnowledgeEntryTable:
        entry = KnowledgeEntryTable(
            tenant_id=tenant_id,
            category=category,
            content=content,
            source_type=source_type,
            source_id=source_id,
            last_reviewed_at=datetime.now(timezone.utc),
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_knowledge_entries(
        self,
        tenant_id: str,
        category: str | None = None,
        status: str | None = None,
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .order_by(KnowledgeEntryTable.updated_at.desc(), KnowledgeEntryTable.created_at.desc())
            )
            if category:
                stmt = stmt.where(KnowledgeEntryTable.category == category)
            if status:
                stmt = stmt.where(KnowledgeEntryTable.status == status)
            return list(session.exec(stmt).all())

    def list_knowledge_entries(
        self,
        tenant_id: str,
        *,
        category: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[KnowledgeEntryTable]:
        entries = self.get_knowledge_entries(tenant_id, category=category, status=status)
        return entries[:limit]

    def get_active_knowledge_entries(
        self,
        tenant_id: str,
        *,
        categories: list[str] | None = None,
        limit: int | None = None,
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.status == "active")
                .order_by(KnowledgeEntryTable.updated_at.desc())
            )
            if categories:
                from sqlalchemy import or_

                stmt = stmt.where(or_(*(KnowledgeEntryTable.category == category for category in categories)))
            if limit is not None:
                stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def mark_knowledge_entries_retrieved(self, entry_ids: list[str]) -> int:
        normalized_entry_ids = [entry_id for entry_id in entry_ids if str(entry_id or "").strip()]
        if not normalized_entry_ids:
            return 0

        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(KnowledgeEntryTable.id.in_(normalized_entry_ids))
            entries = list(session.exec(stmt).all())
            if not entries:
                return 0

            retrieved_at = datetime.now(timezone.utc)
            for entry in entries:
                entry.last_retrieved_at = retrieved_at
                session.add(entry)
            session.commit()
            return len(entries)

    def update_knowledge_entry_status(
        self,
        entry_id: str,
        *,
        status: str,
        conflict_with: str | None = None,
    ) -> KnowledgeEntryTable | None:
        normalized_status = (status or "").strip().lower()
        if normalized_status not in _KNOWLEDGE_VALID_STATUSES:
            raise ValueError(f"Unsupported knowledge status: {status}")

        with Session(self._engine) as session:
            entry = session.get(KnowledgeEntryTable, entry_id)
            if entry is None:
                return None

            entry.status = normalized_status
            entry.conflict_with = (conflict_with or "").strip() or None
            entry.last_reviewed_at = datetime.now(timezone.utc)
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_knowledge_lifecycle_summary(
        self,
        tenant_id: str,
        *,
        stale_after_days: int = 60,
    ) -> dict[str, object]:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max(int(stale_after_days), 1))
        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(KnowledgeEntryTable.tenant_id == tenant_id)
            entries = list(session.exec(stmt).all())

        counts = _knowledge_status_counts(entries)
        stale_candidates = sum(
            1 for entry in entries if _knowledge_is_auto_stale_candidate(entry, cutoff=cutoff)
        )
        return {
            "total": len(entries),
            "stale_after_days": max(int(stale_after_days), 1),
            "by_status": counts,
            "stale_candidates": stale_candidates,
        }

    def refresh_knowledge_lifecycle(
        self,
        tenant_id: str,
        *,
        stale_after_days: int = 60,
    ) -> dict[str, object]:
        stale_after_days = max(int(stale_after_days), 1)
        cutoff = datetime.now(timezone.utc) - timedelta(days=stale_after_days)
        reviewed_at = datetime.now(timezone.utc)

        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(KnowledgeEntryTable.tenant_id == tenant_id)
            entries = list(session.exec(stmt).all())
            touched = 0
            for entry in entries:
                if not _knowledge_is_auto_stale_candidate(entry, cutoff=cutoff):
                    continue
                entry.status = "stale"
                entry.last_reviewed_at = reviewed_at
                session.add(entry)
                touched += 1
            session.commit()

        summary = self.get_knowledge_lifecycle_summary(
            tenant_id,
            stale_after_days=stale_after_days,
        )
        return {
            "updated": touched,
            "summary": summary,
        }

    # ── Conversation ──────────────────────────────────────────────────────────

    def save_conversation(
        self,
        *,
        tenant_id: str,
        role: str,
        content: str,
        conversation_type: str = "onboarding",
    ) -> ConversationTable:
        conv = ConversationTable(
            tenant_id=tenant_id,
            role=role,
            content=content,
            conversation_type=conversation_type,
        )
        with Session(self._engine) as session:
            session.add(conv)
            session.commit()
            session.refresh(conv)
            return conv

    def list_recent_conversations(
        self,
        tenant_id: str,
        *,
        role: str | None = None,
        conversation_type: str | None = None,
        limit: int = 20,
    ) -> list[ConversationTable]:
        with Session(self._engine) as session:
            stmt = select(ConversationTable).where(ConversationTable.tenant_id == tenant_id)
            if role:
                stmt = stmt.where(ConversationTable.role == role)
            if conversation_type:
                stmt = stmt.where(ConversationTable.conversation_type == conversation_type)
            stmt = stmt.order_by(ConversationTable.timestamp.desc()).limit(limit)
            return list(session.exec(stmt).all())

    # ── OnboardingState ───────────────────────────────────────────────────────

    def get_onboarding_state(self, tenant_id: str) -> OnboardingStateTable | None:
        with Session(self._engine) as session:
            stmt = select(OnboardingStateTable).where(OnboardingStateTable.tenant_id == tenant_id)
            return session.exec(stmt).first()

    def get_or_create_onboarding_state(self, tenant_id: str) -> OnboardingStateTable:
        with Session(self._engine) as session:
            stmt = select(OnboardingStateTable).where(OnboardingStateTable.tenant_id == tenant_id)
            state = session.exec(stmt).first()
            if state is None:
                state = OnboardingStateTable(tenant_id=tenant_id)
                session.add(state)
                session.commit()
                session.refresh(state)
            return state

    def update_onboarding_state(
        self,
        tenant_id: str,
        step: str,
        extra: dict | None = None,
    ) -> OnboardingStateTable:
        with Session(self._engine) as session:
            stmt = select(OnboardingStateTable).where(OnboardingStateTable.tenant_id == tenant_id)
            state = session.exec(stmt).first()
            if state is None:
                state = OnboardingStateTable(tenant_id=tenant_id)
            state.step = step
            if extra is not None:
                state.extra = json.dumps(extra, ensure_ascii=False)
            state.updated_at = datetime.now(timezone.utc)
            session.add(state)
            session.commit()
            session.refresh(state)
            return state

    # ── KnowledgeEntry embedding ──────────────────────────────────────────────

    def update_knowledge_entry_embedding(self, entry_id: str, embedding_json: str) -> None:
        with Session(self._engine) as session:
            entry = session.get(KnowledgeEntryTable, entry_id)
            if entry:
                entry.embedding = embedding_json
                entry.updated_at = datetime.now(timezone.utc)
                session.add(entry)
                session.commit()

    # ── PreferenceMemory → KnowledgeEntry(category="preference") ────────────────

    def save_preference_memory(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        diff_notes: str = "",
        run_id: str = "",
    ) -> KnowledgeEntryTable:
        """Store boss edit diff as KnowledgeEntry(category='preference').
        Content is JSON; source_id stores platform for filtering.
        """
        content = json.dumps(
            {
                "platform": platform,
                "original": original_draft,
                "edited": edited_draft,
                "diff_notes": diff_notes,
                "run_id": run_id,
            },
            ensure_ascii=False,
        )
        return self.save_knowledge_entry(
            tenant_id=tenant_id,
            category="preference",
            content=content,
            source_type="edit",
            source_id=platform,
        )

    def get_preference_memories(
        self,
        tenant_id: str,
        platform: str | None = None,
        limit: int = 10,
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.category == "preference")
            )
            if platform:
                stmt = stmt.where(KnowledgeEntryTable.source_id == platform)
            stmt = stmt.order_by(KnowledgeEntryTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    # ── EpisodicMemory → KnowledgeEntry(category="episode") ─────────────────────

    def save_episodic_memory(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: str = "{}",
    ) -> KnowledgeEntryTable:
        """Store workflow outcome as KnowledgeEntry(category='episode').
        Content is JSON; source_id stores workflow_type for filtering.
        """
        content = json.dumps(
            {
                "workflow_type": workflow_type,
                "outcome": outcome,
                "context_summary": context_summary,
            },
            ensure_ascii=False,
        )
        return self.save_knowledge_entry(
            tenant_id=tenant_id,
            category="episode",
            content=content,
            source_type="workflow",
            source_id=workflow_type,
        )

    def get_episodic_memories(
        self,
        tenant_id: str,
        workflow_type: str | None = None,
        limit: int = 10,
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.category == "episode")
            )
            if workflow_type:
                stmt = stmt.where(KnowledgeEntryTable.source_id == workflow_type)
            stmt = stmt.order_by(KnowledgeEntryTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    # ── EditSession ───────────────────────────────────────────────────────────

    def create_edit_session(
        self,
        *,
        tenant_id: str,
        run_id: str,
        ig_draft: str,
        google_draft: str,
    ) -> EditSessionTable:
        session_record = EditSessionTable(
            tenant_id=tenant_id,
            run_id=run_id,
            original_ig_draft=ig_draft,
            original_google_draft=google_draft,
            step="waiting_feedback",
        )
        with Session(self._engine) as session:
            session.add(session_record)
            session.commit()
            session.refresh(session_record)
            return session_record

    def get_active_edit_session(self, tenant_id: str) -> EditSessionTable | None:
        with Session(self._engine) as session:
            stmt = (
                select(EditSessionTable)
                .where(EditSessionTable.tenant_id == tenant_id)
                .where(EditSessionTable.step != "completed")
                .order_by(EditSessionTable.created_at.desc())
            )
            return session.exec(stmt).first()

    def advance_edit_session(self, session_id: str, next_step: str) -> None:
        with Session(self._engine) as session:
            record = session.get(EditSessionTable, session_id)
            if record:
                record.step = next_step
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    def update_edit_session_draft(
        self, session_id: str, platform: str, edited_text: str
    ) -> None:
        """Update the edited draft for IG or Google platform."""
        with Session(self._engine) as session:
            record = session.get(EditSessionTable, session_id)
            if record:
                if platform == "ig_fb":
                    record.edited_ig_draft = edited_text
                elif platform == "google":
                    record.edited_google_draft = edited_text
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    def complete_edit_session(self, session_id: str) -> None:
        self.advance_edit_session(session_id, "completed")

    def update_approval_draft_content(self, run_id: str, draft_content: dict) -> None:
        """Merge draft_content fields into the existing kachu_approval_tasks record.

        Preserves fields like image_url that are not in draft_content.
        """
        with Session(self._engine) as session:
            stmt = select(ApprovalTaskTable).where(ApprovalTaskTable.agentos_run_id == run_id)
            record = session.exec(stmt).first()
            if record:
                existing: dict = {}
                if record.draft_content:
                    try:
                        existing = json.loads(record.draft_content)
                    except Exception:  # noqa: BLE001
                        pass
                existing.update(draft_content)
                record.draft_content = json.dumps(existing, ensure_ascii=False)
                session.add(record)
                session.commit()

    # ── ConnectorAccount ──────────────────────────────────────────────────────

    def save_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
        credentials_json: str,
        account_label: str = "",
        touch_refreshed_at: bool = True,
    ) -> "ConnectorAccountTable":
        """Upsert a connector account (one active per tenant+platform)."""
        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            existing = session.exec(stmt).first()
            if existing:
                existing.credentials_encrypted = credentials_json
                existing.account_label = account_label
                if touch_refreshed_at:
                    existing.last_refreshed_at = datetime.now(timezone.utc)
                existing.updated_at = datetime.now(timezone.utc)
                session.add(existing)
                session.commit()
                session.refresh(existing)
                return existing
            record = ConnectorAccountTable(
                tenant_id=tenant_id,
                platform=platform,
                account_label=account_label,
                credentials_encrypted=credentials_json,
                last_refreshed_at=datetime.now(timezone.utc) if touch_refreshed_at else None,
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def update_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
        credentials_json: str | None = None,
        account_label: str | None = None,
        touch_refreshed_at: bool = False,
    ) -> ConnectorAccountTable | None:
        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            account = session.exec(stmt).first()
            if account is None:
                return None
            if credentials_json is not None:
                account.credentials_encrypted = credentials_json
            if account_label is not None:
                account.account_label = account_label
            if touch_refreshed_at:
                account.last_refreshed_at = datetime.now(timezone.utc)
            account.updated_at = datetime.now(timezone.utc)
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_connector_account(
        self, tenant_id: str, platform: str
    ) -> "ConnectorAccountTable | None":
        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            return session.exec(stmt).first()

    def list_connector_accounts(
        self,
        tenant_id: str,
        *,
        include_inactive: bool = True,
    ) -> list[ConnectorAccountTable]:
        with Session(self._engine) as session:
            stmt = select(ConnectorAccountTable).where(ConnectorAccountTable.tenant_id == tenant_id)
            if not include_inactive:
                stmt = stmt.where(ConnectorAccountTable.is_active == True)  # noqa: E712
            stmt = stmt.order_by(ConnectorAccountTable.created_at.asc())
            return list(session.exec(stmt).all())

    def disconnect_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
    ) -> ConnectorAccountTable | None:
        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            account = session.exec(stmt).first()
            if account is None:
                return None
            account.is_active = False
            account.updated_at = datetime.now(timezone.utc)
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_tenant_feature_flags(self, tenant_id: str) -> TenantFeatureFlagTable | None:
        with Session(self._engine) as session:
            return session.get(TenantFeatureFlagTable, tenant_id)

    def get_or_create_tenant_feature_flags(self, tenant_id: str) -> TenantFeatureFlagTable:
        with Session(self._engine) as session:
            flags = session.get(TenantFeatureFlagTable, tenant_id)
            if flags is None:
                flags = TenantFeatureFlagTable(tenant_id=tenant_id)
                session.add(flags)
                session.commit()
                session.refresh(flags)
            return flags

    def update_tenant_feature_flags(self, tenant_id: str, **updates) -> TenantFeatureFlagTable:
        with Session(self._engine) as session:
            flags = session.get(TenantFeatureFlagTable, tenant_id)
            if flags is None:
                flags = TenantFeatureFlagTable(tenant_id=tenant_id)
            for key, value in updates.items():
                if hasattr(flags, key):
                    setattr(flags, key, bool(value))
            flags.updated_at = datetime.now(timezone.utc)
            session.add(flags)
            session.commit()
            session.refresh(flags)
            return flags

    def find_tenant_ids_by_google_location(self, location_name: str) -> list[str]:
        target = _normalize_google_location(location_name)
        if not target:
            return []

        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.platform == "google_business")
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            matches: list[str] = []
            for account in session.exec(stmt).all():
                try:
                    credentials = json.loads(account.credentials_encrypted or "{}")
                except json.JSONDecodeError:
                    continue

                candidates = {
                    _normalize_google_location(credentials.get("location_name", "")),
                    _normalize_google_location(credentials.get("locationName", "")),
                    _normalize_google_location(credentials.get("location_id", "")),
                    _normalize_google_location(credentials.get("locationId", "")),
                    _normalize_google_location(credentials.get("google_business_location_id", "")),
                }
                candidates.discard("")
                if target in candidates:
                    matches.append(account.tenant_id)

            return matches

    # ── Knowledge Update helpers ──────────────────────────────────────────────

    def mark_knowledge_entry_superseded(self, entry_id: str) -> None:
        """Mark a single knowledge entry as superseded (no replacement created)."""
        self.update_knowledge_entry_status(entry_id, status="superseded")

    def supersede_knowledge_entry(
        self,
        *,
        old_entry_id: str,
        tenant_id: str,
        category: str,
        new_content: str,
        source_type: str = "boss_update",
    ) -> "KnowledgeEntryTable":
        """Mark old entry as superseded and create exactly one replacement.

        NOTE: Call this only when replacing a *single* entry.
        When superseding multiple entries, call mark_knowledge_entry_superseded()
        for each, then save_knowledge_entry() once for the replacement.
        """
        self.mark_knowledge_entry_superseded(old_entry_id)
        return self.save_knowledge_entry(
            tenant_id=tenant_id,
            category=category,
            content=new_content,
            source_type=source_type,
            source_id=old_entry_id,
        )

    def search_knowledge_entries_by_keywords(
        self,
        tenant_id: str,
        keywords: list[str],
        categories: list[str] | None = None,
        limit: int = 10,
    ) -> list["KnowledgeEntryTable"]:
        """Simple keyword match in content (for diff-knowledge step)."""
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.status == "active")
            )
            if categories:
                from sqlalchemy import or_
                stmt = stmt.where(
                    or_(*(KnowledgeEntryTable.category == c for c in categories))
                )
            entries = list(session.exec(stmt).all())
            # Filter in Python for keyword containment
            matched = [
                e for e in entries
                if any(kw.lower() in e.content.lower() for kw in keywords)
            ]
            return matched[:limit]

    # ── PushLog / rate limiting ───────────────────────────────────────────────

    def record_push(
        self,
        *,
        tenant_id: str,
        recipient_line_id: str,
        message_type: str = "approval",
    ) -> "PushLogTable":
        """Record a push message for rate-limiting tracking."""
        record = PushLogTable(
            tenant_id=tenant_id,
            recipient_line_id=recipient_line_id,
            message_type=message_type,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def count_pushes_today(self, tenant_id: str) -> int:
        """Count pushes sent to this tenant since midnight UTC today."""
        from sqlalchemy import func

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with Session(self._engine) as session:
            stmt = (
                select(func.count(PushLogTable.id))
                .where(PushLogTable.tenant_id == tenant_id)
                .where(PushLogTable.pushed_at >= today_start)
            )
            result = session.exec(stmt).one()
            return result if result else 0

    def can_push(
        self,
        tenant_id: str,
        max_per_day: int = 3,
        quiet_hours_start: int | None = None,
        quiet_hours_end: int | None = None,
    ) -> bool:
        """Return True if a push is allowed (not over daily limit, not quiet hours)."""
        if self.count_pushes_today(tenant_id) >= max_per_day:
            return False
        if quiet_hours_start is not None and quiet_hours_end is not None:
            current_hour = datetime.now(timezone.utc).hour
            if quiet_hours_start <= quiet_hours_end:
                if quiet_hours_start <= current_hour < quiet_hours_end:
                    return False
            else:
                # wraps midnight: e.g. 22-07
                if current_hour >= quiet_hours_start or current_hour < quiet_hours_end:
                    return False
        return True

    # ── Dashboard list queries ────────────────────────────────────────────────

    def list_workflow_runs(
        self,
        tenant_id: str | None = None,
        workflow_type: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> list[WorkflowRunTable]:
        with Session(self._engine) as session:
            stmt = select(WorkflowRunTable).order_by(WorkflowRunTable.created_at.desc())
            if tenant_id:
                stmt = stmt.where(WorkflowRunTable.tenant_id == tenant_id)
            if workflow_type:
                stmt = stmt.where(WorkflowRunTable.workflow_type == workflow_type)
            if status:
                stmt = stmt.where(WorkflowRunTable.status == status)
            stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def list_pending_approvals(
        self,
        tenant_id: str | None = None,
        status: str | None = None,
    ) -> list[ApprovalTaskTable]:
        with Session(self._engine) as session:
            stmt = select(ApprovalTaskTable).order_by(ApprovalTaskTable.created_at.desc())
            if tenant_id:
                stmt = stmt.where(ApprovalTaskTable.tenant_id == tenant_id)
            if status:
                stmt = stmt.where(ApprovalTaskTable.status == status)
            return list(session.exec(stmt).all())

    def list_push_logs(
        self,
        tenant_id: str | None = None,
        limit: int = 100,
    ) -> list[PushLogTable]:
        with Session(self._engine) as session:
            stmt = select(PushLogTable).order_by(PushLogTable.pushed_at.desc())
            if tenant_id:
                stmt = stmt.where(PushLogTable.tenant_id == tenant_id)
            stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def save_audit_event(
        self,
        *,
        tenant_id: str,
        event_type: str,
        agentos_run_id: str = "",
        agentos_task_id: str = "",
        workflow_type: str = "",
        actor_id: str | None = None,
        source: str = "",
        payload: dict | None = None,
    ) -> AuditEventTable:
        record = AuditEventTable(
            tenant_id=tenant_id,
            agentos_run_id=agentos_run_id,
            agentos_task_id=agentos_task_id,
            workflow_type=workflow_type,
            event_type=event_type,
            actor_id=actor_id,
            source=source,
            payload=json.dumps(payload or {}, ensure_ascii=False),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_audit_events(
        self,
        *,
        tenant_id: str | None = None,
        agentos_run_id: str | None = None,
        workflow_type: str | None = None,
        event_type: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[AuditEventTable]:
        with Session(self._engine) as session:
            stmt = select(AuditEventTable).order_by(AuditEventTable.created_at.desc())
            if tenant_id:
                stmt = stmt.where(AuditEventTable.tenant_id == tenant_id)
            if agentos_run_id:
                stmt = stmt.where(AuditEventTable.agentos_run_id == agentos_run_id)
            if workflow_type:
                stmt = stmt.where(AuditEventTable.workflow_type == workflow_type)
            if event_type:
                stmt = stmt.where(AuditEventTable.event_type == event_type)
            if source:
                stmt = stmt.where(AuditEventTable.source == source)
            stmt = stmt.limit(limit)
            return list(session.exec(stmt).all())

    def get_tenant_health_snapshot(self, tenant_id: str) -> dict | None:
        tenant = self.get_tenant(tenant_id)
        if tenant is None:
            return None

        feature_flags = self.get_or_create_tenant_feature_flags(tenant_id)
        now = datetime.now(timezone.utc)
        lower = now - timedelta(hours=24)

        with Session(self._engine) as session:
            connector_rows = list(
                session.exec(
                    select(ConnectorAccountTable)
                    .where(ConnectorAccountTable.tenant_id == tenant_id)
                    .order_by(ConnectorAccountTable.created_at.asc())
                ).all()
            )
            workflow_rows = list(
                session.exec(
                    select(WorkflowRunTable)
                    .where(WorkflowRunTable.tenant_id == tenant_id)
                    .where(WorkflowRunTable.created_at >= lower)
                    .order_by(WorkflowRunTable.created_at.desc())
                ).all()
            )
            recent_failures = list(
                session.exec(
                    select(WorkflowRunTable)
                    .where(WorkflowRunTable.tenant_id == tenant_id)
                    .where(WorkflowRunTable.status == "failed")
                    .order_by(WorkflowRunTable.created_at.desc())
                    .limit(5)
                ).all()
            )
            pending_approvals = session.exec(
                select(ApprovalTaskTable)
                .where(ApprovalTaskTable.tenant_id == tenant_id)
                .where(ApprovalTaskTable.status == "pending")
            ).all()
            active_knowledge = session.exec(
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.status == "active")
            ).all()
            memberships = session.exec(
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.is_active == True)  # noqa: E712
            ).all()
            budget = session.exec(
                select(TenantLlmBudgetTable).where(TenantLlmBudgetTable.tenant_id == tenant_id)
            ).first()

        connector_alerts = 0
        connector_summaries: list[dict[str, object]] = []
        for connector in connector_rows:
            creds = _load_connector_credentials(connector)
            refresh_error = str(creds.get("last_refresh_error", "") or "").strip()
            refresh_status = str(creds.get("refresh_status", "healthy") or "healthy").strip()
            can_refresh = connector.platform in {"google_business", "ga4"} and bool(creds.get("refresh_token"))
            if refresh_error or (connector.is_active and not creds.get("access_token")):
                connector_alerts += 1
            connector_summaries.append(
                {
                    "platform": connector.platform,
                    "is_active": connector.is_active,
                    "account_label": connector.account_label,
                    "last_refreshed_at": connector.last_refreshed_at.isoformat() if connector.last_refreshed_at else None,
                    "refresh_status": refresh_status,
                    "refresh_error": refresh_error or None,
                    "can_refresh": can_refresh,
                }
            )

        last_successful_run = next((row for row in workflow_rows if row.status == "completed"), None)
        last_failed_run = next((row for row in workflow_rows if row.status == "failed"), None)
        plan_status = "active"
        plan_expires_at = tenant.plan_expires_at
        if plan_expires_at and plan_expires_at.tzinfo is None:
            plan_expires_at = plan_expires_at.replace(tzinfo=timezone.utc)
        elif plan_expires_at:
            plan_expires_at = plan_expires_at.astimezone(timezone.utc)
        if plan_expires_at and plan_expires_at <= now:
            plan_status = "expired"
        elif plan_expires_at:
            plan_status = "scheduled_expiry"

        return {
            "tenant": {
                "id": tenant.id,
                "name": tenant.name,
                "plan": tenant.plan,
                "plan_expires_at": tenant.plan_expires_at.isoformat() if tenant.plan_expires_at else None,
                "merchant_slug": tenant.merchant_slug,
                "plan_status": plan_status,
                "is_active": tenant.is_active,
                "timezone": tenant.timezone,
            },
            "feature_flags": _serialize_model(feature_flags),
            "connectors": connector_summaries,
            "alerts": {
                "connector_alerts": connector_alerts,
                "recent_failures": len(recent_failures),
            },
            "recent_activity": {
                "runs_last_24h": len(workflow_rows),
                "failed_runs_last_24h": sum(1 for row in workflow_rows if row.status == "failed"),
                "pending_approvals": len(pending_approvals),
                "active_knowledge_entries": len(active_knowledge),
                "active_memberships": len(memberships),
                "last_successful_run_at": last_successful_run.created_at.isoformat() if last_successful_run else None,
                "last_failed_run_at": last_failed_run.created_at.isoformat() if last_failed_run else None,
            },
            "recent_failures": [
                {
                    "workflow_type": row.workflow_type,
                    "error_message": row.error_message,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in recent_failures
            ],
            "llm_budget": {
                "enabled": bool(getattr(budget, "enabled", False)) if budget else None,
                "monthly_budget_usd": getattr(budget, "monthly_budget_usd", None) if budget else None,
                "last_synced_at": budget.last_synced_at.isoformat() if budget and budget.last_synced_at else None,
            },
        }

    def has_recent_audit_event(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        event_type: str,
        source: str,
        since: datetime,
        payload_subset: dict | None = None,
        limit: int = 50,
    ) -> bool:
        with Session(self._engine) as session:
            stmt = (
                select(AuditEventTable)
                .where(AuditEventTable.tenant_id == tenant_id)
                .where(AuditEventTable.workflow_type == workflow_type)
                .where(AuditEventTable.event_type == event_type)
                .where(AuditEventTable.source == source)
                .where(AuditEventTable.created_at >= since)
                .order_by(AuditEventTable.created_at.desc())
                .limit(limit)
            )
            events = list(session.exec(stmt).all())

        if not payload_subset:
            return bool(events)

        for event in events:
            try:
                payload = json.loads(event.payload or "{}")
            except (json.JSONDecodeError, TypeError):
                continue
            if all(payload.get(key) == value for key, value in payload_subset.items()):
                return True
        return False

    def get_knowledge_entry(self, entry_id: str) -> KnowledgeEntryTable | None:
        with Session(self._engine) as session:
            return session.get(KnowledgeEntryTable, entry_id)

    def delete_knowledge_entry(self, entry_id: str) -> bool:
        with Session(self._engine) as session:
            entry = session.get(KnowledgeEntryTable, entry_id)
            if entry is None:
                return False
            session.delete(entry)
            session.commit()
            return True

    def update_knowledge_entry_content(
        self,
        entry_id: str,
        content: str,
        category: str | None = None,
    ) -> KnowledgeEntryTable | None:
        with Session(self._engine) as session:
            entry = session.get(KnowledgeEntryTable, entry_id)
            if entry is None:
                return None
            entry.content = content
            if category:
                entry.category = category
            entry.updated_at = datetime.now(timezone.utc)
            entry.status = "active"
            entry.conflict_with = None
            entry.last_reviewed_at = datetime.now(timezone.utc)
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_dashboard_stats(self, tenant_id: str | None = None) -> dict:
        """Aggregate stats for the dashboard overview."""
        from sqlalchemy import func
        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        with Session(self._engine) as session:
            # Total workflow runs
            run_stmt = select(func.count(WorkflowRunTable.id))
            if tenant_id:
                run_stmt = run_stmt.where(WorkflowRunTable.tenant_id == tenant_id)
            total_runs = session.exec(run_stmt).one() or 0

            # Active runs
            active_stmt = select(func.count(WorkflowRunTable.id)).where(
                WorkflowRunTable.status == "running"
            )
            if tenant_id:
                active_stmt = active_stmt.where(WorkflowRunTable.tenant_id == tenant_id)
            active_runs = session.exec(active_stmt).one() or 0

            # Pending approvals
            approval_stmt = select(func.count(ApprovalTaskTable.id)).where(
                ApprovalTaskTable.status == "pending"
            )
            if tenant_id:
                approval_stmt = approval_stmt.where(ApprovalTaskTable.tenant_id == tenant_id)
            pending_approvals = session.exec(approval_stmt).one() or 0

            # Active knowledge entries
            kb_stmt = select(func.count(KnowledgeEntryTable.id)).where(
                KnowledgeEntryTable.status == "active"
            )
            if tenant_id:
                kb_stmt = kb_stmt.where(KnowledgeEntryTable.tenant_id == tenant_id)
            knowledge_entries = session.exec(kb_stmt).one() or 0

            stale_kb_stmt = select(func.count(KnowledgeEntryTable.id)).where(
                KnowledgeEntryTable.status == "stale"
            )
            if tenant_id:
                stale_kb_stmt = stale_kb_stmt.where(KnowledgeEntryTable.tenant_id == tenant_id)
            stale_knowledge_entries = session.exec(stale_kb_stmt).one() or 0

            conflict_kb_stmt = select(func.count(KnowledgeEntryTable.id)).where(
                KnowledgeEntryTable.status == "conflict"
            )
            if tenant_id:
                conflict_kb_stmt = conflict_kb_stmt.where(KnowledgeEntryTable.tenant_id == tenant_id)
            conflict_knowledge_entries = session.exec(conflict_kb_stmt).one() or 0

            # Today's pushes
            push_stmt = select(func.count(PushLogTable.id)).where(
                PushLogTable.pushed_at >= today_start
            )
            if tenant_id:
                push_stmt = push_stmt.where(PushLogTable.tenant_id == tenant_id)
            today_pushes = session.exec(push_stmt).one() or 0

            # Runs per workflow type
            type_stmt = (
                select(WorkflowRunTable.workflow_type, func.count(WorkflowRunTable.id))
                .group_by(WorkflowRunTable.workflow_type)
            )
            if tenant_id:
                type_stmt = type_stmt.where(WorkflowRunTable.tenant_id == tenant_id)
            type_counts = {row[0]: row[1] for row in session.exec(type_stmt).all()}

        return {
            "total_runs": total_runs,
            "active_runs": active_runs,
            "pending_approvals": pending_approvals,
            "knowledge_entries": knowledge_entries,
            "stale_knowledge_entries": stale_knowledge_entries,
            "conflict_knowledge_entries": conflict_knowledge_entries,
            "today_pushes": today_pushes,
            "runs_by_type": type_counts,
        }

    # ── Phase 4: Tenant Approval Profile ─────────────────────────────────────

    def get_approval_profile(self, tenant_id: str) -> TenantApprovalProfileTable | None:
        with Session(self._engine) as session:
            return session.get(TenantApprovalProfileTable, tenant_id)

    def compute_and_save_approval_profile(self, tenant_id: str) -> TenantApprovalProfileTable:
        """Recompute approval stats from last 30 days and persist."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        with Session(self._engine) as session:
            stmt = select(ApprovalTaskTable).where(
                ApprovalTaskTable.tenant_id == tenant_id,
                ApprovalTaskTable.decided_at >= cutoff,
                ApprovalTaskTable.status != "pending",
            )
            decisions = list(session.exec(stmt).all())

        total = len(decisions)
        approved = sum(1 for d in decisions if d.decision == "approved")
        edited = sum(1 for d in decisions if d.decision in ("modified", "approved") and d.edit_diff)

        acceptance_rate = approved / total if total else 0.0

        # Compute median edit delta from edit_diff entries
        deltas: list[float] = []
        for d in decisions:
            if d.ai_draft and d.final_content:
                orig_len = len(d.ai_draft)
                if orig_len > 0:
                    deltas.append(abs(len(d.final_content) - orig_len) / orig_len)
        median_delta = sorted(deltas)[len(deltas) // 2] if deltas else 0.0

        # Average latency in seconds
        latencies = [
            (d.decided_at - d.created_at).total_seconds()
            for d in decisions
            if d.decided_at and d.created_at
        ]
        avg_latency = sum(latencies) / len(latencies) if latencies else 86400.0

        with Session(self._engine) as session:
            profile = session.get(TenantApprovalProfileTable, tenant_id)
            if profile is None:
                profile = TenantApprovalProfileTable(tenant_id=tenant_id)
            profile.recent_acceptance_rate = round(acceptance_rate, 4)
            profile.median_edit_delta = round(median_delta, 4)
            profile.avg_approval_latency_seconds = round(avg_latency, 1)
            profile.total_decisions = total
            profile.updated_at = datetime.now(timezone.utc)
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    # ── Automation Settings ──────────────────────────────────────────────────

    def get_or_create_automation_settings(self, tenant_id: str) -> TenantAutomationSettingsTable:
        with Session(self._engine) as session:
            settings = session.get(TenantAutomationSettingsTable, tenant_id)
            if settings is None:
                settings = TenantAutomationSettingsTable(tenant_id=tenant_id)
                session.add(settings)
                session.commit()
                session.refresh(settings)
            return settings

    def update_automation_settings(self, tenant_id: str, **updates) -> TenantAutomationSettingsTable:
        with Session(self._engine) as session:
            settings = session.get(TenantAutomationSettingsTable, tenant_id)
            if settings is None:
                settings = TenantAutomationSettingsTable(tenant_id=tenant_id)
            for key, value in updates.items():
                if hasattr(settings, key):
                    setattr(settings, key, value)
            settings.updated_at = datetime.now(timezone.utc)
            session.add(settings)
            session.commit()
            session.refresh(settings)
            return settings

    # ── Phase 5: Shared Context ───────────────────────────────────────────────

    def save_shared_context(
        self,
        *,
        tenant_id: str,
        context_type: str,
        content: dict,
        source_run_id: str = "",
        ttl_hours: int = 168,  # 7 days default
    ) -> SharedContextTable:
        """Upsert a cross-workflow context hint (replaces existing same type)."""
        from datetime import timedelta
        expires_at = datetime.now(timezone.utc) + timedelta(hours=ttl_hours)
        with Session(self._engine) as session:
            # Delete any previous entry of same type for this tenant
            old = session.exec(
                select(SharedContextTable)
                .where(SharedContextTable.tenant_id == tenant_id)
                .where(SharedContextTable.context_type == context_type)
            ).first()
            if old:
                session.delete(old)
                session.flush()
            entry = SharedContextTable(
                tenant_id=tenant_id,
                context_type=context_type,
                content=json.dumps(content, ensure_ascii=False),
                source_run_id=source_run_id,
                expires_at=expires_at,
            )
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_shared_context(
        self,
        tenant_id: str,
        context_type: str,
    ) -> dict | None:
        """Return unexpired shared context, or None."""
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            entry = session.exec(
                select(SharedContextTable)
                .where(SharedContextTable.tenant_id == tenant_id)
                .where(SharedContextTable.context_type == context_type)
            ).first()
            if entry is None:
                return None
            if entry.expires_at:
                # SQLite stores naive datetimes; normalise to UTC for comparison
                exp = entry.expires_at
                if exp.tzinfo is None:
                    exp = exp.replace(tzinfo=timezone.utc)
                if exp < now:
                    return None
            try:
                return json.loads(entry.content)
            except (json.JSONDecodeError, TypeError):
                return None

    def get_last_published_at(self, tenant_id: str) -> datetime | None:
        """Return the most recent completed workflow run datetime for the tenant."""
        with Session(self._engine) as session:
            stmt = (
                select(WorkflowRunTable)
                .where(WorkflowRunTable.tenant_id == tenant_id)
                .where(WorkflowRunTable.status == "completed")
                .where(WorkflowRunTable.workflow_type.in_(["photo_content", "google_post"]))
                .order_by(WorkflowRunTable.created_at.desc())
            )
            row = session.exec(stmt).first()
            return row.created_at if row else None

    # ── Deferred AgentOS Dispatches ─────────────────────────────────────────

    def create_deferred_dispatch(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        task_request: dict,
        trigger_source: str,
        trigger_payload: dict,
        error: str,
    ) -> DeferredDispatchTable:
        record = DeferredDispatchTable(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            task_request_json=json.dumps(task_request, ensure_ascii=False),
            trigger_source=trigger_source,
            trigger_payload=json.dumps(trigger_payload, ensure_ascii=False),
            last_error=error,
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def list_due_deferred_dispatches(self, limit: int = 20) -> list[DeferredDispatchTable]:
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            stmt = (
                select(DeferredDispatchTable)
                .where(DeferredDispatchTable.status == "pending")
                .where(DeferredDispatchTable.next_retry_at <= now)
                .order_by(DeferredDispatchTable.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def mark_deferred_dispatch_dispatched(self, dispatch_id: str) -> None:
        with Session(self._engine) as session:
            record = session.get(DeferredDispatchTable, dispatch_id)
            if record:
                record.status = "dispatched"
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    def mark_deferred_dispatch_retry(self, dispatch_id: str, error: str) -> None:
        from datetime import timedelta

        with Session(self._engine) as session:
            record = session.get(DeferredDispatchTable, dispatch_id)
            if record:
                record.attempts += 1
                record.last_error = error
                delay_minutes = min(5 * (2 ** max(record.attempts - 1, 0)), 60)
                record.next_retry_at = datetime.now(timezone.utc) + timedelta(minutes=delay_minutes)
                record.updated_at = datetime.now(timezone.utc)
                session.add(record)
                session.commit()

    def get_pending_negative_reviews(self, tenant_id: str) -> int:
        """Count pending approval tasks of type review_reply older than 1 hour."""
        from datetime import timedelta
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        with Session(self._engine) as session:
            stmt = select(ApprovalTaskTable).where(
                ApprovalTaskTable.tenant_id == tenant_id,
                ApprovalTaskTable.status == "pending",
                ApprovalTaskTable.workflow_type == "kachu_review_reply",
                ApprovalTaskTable.created_at <= cutoff,
            )
            return len(list(session.exec(stmt).all()))

    def get_knowledge_last_updated_at(self, tenant_id: str) -> datetime | None:
        """Return the most recent knowledge entry updated_at for tenant."""
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .where(KnowledgeEntryTable.status == "active")
                .order_by(KnowledgeEntryTable.updated_at.desc())
            )
            row = session.exec(stmt).first()
            return row.updated_at if row else None

