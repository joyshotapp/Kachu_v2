from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Engine
from sqlmodel import Session, select

from .tables import (
    ApprovalTaskTable,
    AuditEventTable,
    ConnectorAccountTable,
    ConversationTable,
    DeferredDispatchTable,
    EditSessionTable,
    KnowledgeEntryTable,
    OnboardingStateTable,
    PushLogTable,
    SharedContextTable,
    TenantApprovalProfileTable,
    TenantAutomationSettingsTable,
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
                session.refresh(tenant)
            return tenant

    def save_tenant(self, tenant: TenantTable) -> TenantTable:
        tenant.updated_at = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            session.add(tenant)
            session.commit()
            session.refresh(tenant)
            return tenant

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
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(KnowledgeEntryTable.tenant_id == tenant_id)
            if category:
                stmt = stmt.where(KnowledgeEntryTable.category == category)
            return list(session.exec(stmt).all())

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
            step="waiting_ig",
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

    # ── ConnectorAccount ──────────────────────────────────────────────────────

    def save_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
        credentials_json: str,
        account_label: str = "",
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
                last_refreshed_at=datetime.now(timezone.utc),
            )
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

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
        with Session(self._engine) as session:
            entry = session.get(KnowledgeEntryTable, entry_id)
            if entry:
                entry.status = "superseded"
                entry.updated_at = datetime.now(timezone.utc)
                session.add(entry)
                session.commit()

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

