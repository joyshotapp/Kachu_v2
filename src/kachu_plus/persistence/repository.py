from __future__ import annotations

from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
import json
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from kachu_plus.persistence.tables import (
    ApprovalProfileTable,
    ChannelEntityTable,
    ConversationHandoffLockTable,
    ConversationTable,
    ContentPlanItemTable,
    ContentPlanTable,
    ConnectorAccountTable,
    ContextBriefTable,
    CustomerTagAssignmentTable,
    CustomerTagDefinitionTable,
    CustomerTimelineEventTable,
    CustomerProfileTable,
    EpisodicMemoryTable,
    ExecuteTaskRecordTable,
    ExternalEngagementTable,
    KnowledgeEntryTable,
    LineChannelConfigTable,
    MetaOAuthSessionTable,
    OnboardingStateTable,
    PendingApprovalTable,
    PreferenceMemoryTable,
    ProfileLinkTable,
    ProfileMergeAuditTable,
    PublishedContentRecordTable,
    RecurringJobTable,
    SuggestionTable,
    TenantTable,
    TenantMembershipTable,
    WebhookEventTable,
)


class KachuPlusRepository:
    """
    Application-lifetime repository；engine 在 startup 建立，session 每次 method call 開新的。
    """

    def __init__(self, engine) -> None:
        self._engine = engine

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    # ── Tenant ────────────────────────────────────────────────────────────────

    def get_tenant(self, tenant_id: str) -> Optional[TenantTable]:
        with Session(self._engine) as session:
            return session.get(TenantTable, tenant_id)

    def list_active_tenants(self) -> list[TenantTable]:
        with Session(self._engine) as session:
            stmt = select(TenantTable).where(TenantTable.is_active == True)  # noqa: E712
            return list(session.exec(stmt).all())

    def save_tenant(self, tenant: TenantTable) -> None:
        from kachu_plus.persistence.tables import utcnow
        with Session(self._engine) as session:
            tenant.updated_at = utcnow()
            session.merge(tenant)
            session.commit()

    def create_tenant_membership(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        role: str = "owner",
        display_name: str = "",
    ) -> TenantMembershipTable:
        from kachu_plus.persistence.tables import utcnow

        normalized_line_user_id = str(line_user_id).strip()
        if not normalized_line_user_id:
            raise ValueError("line_user_id is required")
        normalized_role = str(role or "owner").strip().lower() or "owner"
        if normalized_role not in {"owner", "manager", "customer"}:
            raise ValueError("role must be owner, manager, or customer")

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

            stmt = (
                select(TenantMembershipTable)
                .where(TenantMembershipTable.tenant_id == tenant_id)
                .where(TenantMembershipTable.line_user_id == normalized_line_user_id)
            )
            membership = session.exec(stmt).first()
            now = utcnow()
            if membership is None:
                membership = TenantMembershipTable(
                    tenant_id=tenant_id,
                    line_user_id=normalized_line_user_id,
                    role=normalized_role,
                    display_name=display_name.strip(),
                    updated_at=now,
                )
            else:
                membership.role = normalized_role
                membership.display_name = display_name.strip()
                membership.is_active = True
                membership.updated_at = now
            session.add(membership)
            session.commit()
            session.refresh(membership)
            return membership

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
                line_user_id = str(row.line_user_id or "").strip()
                if line_user_id and line_user_id not in recipients:
                    recipients.append(line_user_id)
            return recipients

    def get_recurring_job(self, tenant_id: str, job_type: str) -> Optional[RecurringJobTable]:
        with Session(self._engine) as session:
            stmt = select(RecurringJobTable).where(
                RecurringJobTable.tenant_id == tenant_id,
                RecurringJobTable.job_type == job_type,
            )
            return session.exec(stmt).first()

    def claim_due_recurring_job(
        self,
        *,
        tenant_id: str,
        job_type: str,
        lease_seconds: int = 300,
    ) -> Optional[RecurringJobTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(RecurringJobTable).where(
                RecurringJobTable.tenant_id == tenant_id,
                RecurringJobTable.job_type == job_type,
            )
            job = session.exec(stmt).first()
            now = utcnow()
            if job is None:
                job = RecurringJobTable(
                    tenant_id=tenant_id,
                    job_type=job_type,
                    next_run_at=now,
                )

            locked_until = self._as_utc(job.locked_until)
            if locked_until is not None and locked_until > now:
                if job.id:
                    return None

            next_run_at = self._as_utc(job.next_run_at)
            if next_run_at is not None and next_run_at > now:
                if job.id:
                    return None

            job.locked_until = now + timedelta(seconds=max(int(lease_seconds), 60))
            job.updated_at = now
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def mark_recurring_job_completed(
        self,
        *,
        job_id: str,
        interval_seconds: int,
        result: dict[str, object] | None = None,
    ) -> Optional[RecurringJobTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            job = session.get(RecurringJobTable, job_id)
            if job is None:
                return None
            now = utcnow()
            job.last_run_at = now
            job.next_run_at = now + timedelta(seconds=max(int(interval_seconds), 300))
            job.locked_until = None
            job.last_result_json = json.dumps(result or {}, ensure_ascii=False)
            job.updated_at = now
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    def mark_recurring_job_failed(
        self,
        *,
        job_id: str,
        retry_after_seconds: int = 900,
        result: dict[str, object] | None = None,
    ) -> Optional[RecurringJobTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            job = session.get(RecurringJobTable, job_id)
            if job is None:
                return None
            now = utcnow()
            job.next_run_at = now + timedelta(seconds=max(int(retry_after_seconds), 60))
            job.locked_until = None
            job.last_result_json = json.dumps(result or {}, ensure_ascii=False)
            job.updated_at = now
            session.add(job)
            session.commit()
            session.refresh(job)
            return job

    # ── Customer Profiles ────────────────────────────────────────────────────

    def save_customer_profile(self, profile: CustomerProfileTable) -> None:
        from kachu_plus.persistence.tables import utcnow
        with Session(self._engine) as session:
            profile.updated_at = utcnow()
            session.merge(profile)
            session.commit()

    def get_customer_profile(self, profile_id: str) -> Optional[CustomerProfileTable]:
        with Session(self._engine) as session:
            return session.get(CustomerProfileTable, profile_id)

    def list_profile_active_tags(self, tenant_id: str, profile_id: str) -> list[CustomerTagDefinitionTable]:
        with Session(self._engine) as session:
            assignment_stmt = select(CustomerTagAssignmentTable).where(
                CustomerTagAssignmentTable.tenant_id == tenant_id,
                CustomerTagAssignmentTable.profile_id == profile_id,
                CustomerTagAssignmentTable.removed_at == None,  # noqa: E711
            )
            assignments = list(session.exec(assignment_stmt).all())
            if not assignments:
                return []

            tag_ids = [assignment.tag_id for assignment in assignments]
            tag_stmt = (
                select(CustomerTagDefinitionTable)
                .where(
                    CustomerTagDefinitionTable.tenant_id == tenant_id,
                    CustomerTagDefinitionTable.id.in_(tag_ids),
                    CustomerTagDefinitionTable.is_active == True,  # noqa: E712
                )
                .order_by(CustomerTagDefinitionTable.created_at.asc())
            )
            return list(session.exec(tag_stmt).all())

    def list_profile_timeline_events(
        self,
        tenant_id: str,
        profile_id: str,
    ) -> list[CustomerTimelineEventTable]:
        with Session(self._engine) as session:
            stmt = (
                select(CustomerTimelineEventTable)
                .where(
                    CustomerTimelineEventTable.tenant_id == tenant_id,
                    CustomerTimelineEventTable.profile_id == profile_id,
                )
                .order_by(CustomerTimelineEventTable.created_at.desc())
            )
            return list(session.exec(stmt).all())

    def list_profile_channel_links(
        self,
        tenant_id: str,
        profile_id: str,
    ) -> list[tuple[ProfileLinkTable, ChannelEntityTable]]:
        with Session(self._engine) as session:
            stmt = (
                select(ProfileLinkTable, ChannelEntityTable)
                .join(ChannelEntityTable, ChannelEntityTable.id == ProfileLinkTable.channel_entity_id)
                .where(ProfileLinkTable.tenant_id == tenant_id, ProfileLinkTable.profile_id == profile_id)
                .order_by(ProfileLinkTable.created_at.asc())
            )
            return list(session.exec(stmt).all())

    def relink_profile_channel_entity(
        self,
        *,
        tenant_id: str,
        target_profile_id: str,
        channel_type: str,
        external_user_id: str,
        actor_line_id: str = "",
        reason: str = "",
    ) -> tuple[ProfileLinkTable, ChannelEntityTable]:
        from kachu_plus.persistence.tables import utcnow

        normalized_channel_type = str(channel_type or "line").strip() or "line"
        normalized_user_id = str(external_user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("external_user_id is required")

        with Session(self._engine) as session:
            target = session.get(CustomerProfileTable, target_profile_id)
            if target is None or target.tenant_id != tenant_id:
                raise LookupError("target profile not found")
            if str(target.merged_into_profile_id or "").strip():
                raise ValueError("target profile is merged")

            entity_stmt = select(ChannelEntityTable).where(
                ChannelEntityTable.tenant_id == tenant_id,
                ChannelEntityTable.channel_type == normalized_channel_type,
                ChannelEntityTable.external_user_id == normalized_user_id,
            )
            entity = session.exec(entity_stmt).first()
            if entity is None:
                raise LookupError("channel entity not found")

            link_stmt = select(ProfileLinkTable).where(
                ProfileLinkTable.tenant_id == tenant_id,
                ProfileLinkTable.channel_entity_id == entity.id,
            )
            link = session.exec(link_stmt).first()
            if link is None:
                link = ProfileLinkTable(
                    tenant_id=tenant_id,
                    profile_id=target_profile_id,
                    channel_entity_id=entity.id,
                    confidence_score=1.0,
                    resolution_source="manual_relink",
                    resolution_note=str(reason or "").strip() or f"Relinked by {actor_line_id or 'system'}",
                )
                session.add(link)
                self._add_timeline_event(
                    session,
                    tenant_id=tenant_id,
                    profile_id=target_profile_id,
                    activity_type="profile_link_relinked",
                    title=f"重新綁定 {normalized_channel_type} 身份：{normalized_user_id}",
                    payload={
                        "channel_type": normalized_channel_type,
                        "external_user_id": normalized_user_id,
                        "source_profile_id": "",
                        "target_profile_id": target_profile_id,
                        "actor_line_id": str(actor_line_id or "").strip(),
                        "reason": str(reason or "").strip(),
                    },
                )
                session.commit()
                session.refresh(link)
                session.refresh(entity)
                return link, entity

            source_profile_id = str(link.profile_id or "").strip()
            if source_profile_id == target_profile_id:
                return link, entity

            source = session.get(CustomerProfileTable, source_profile_id) if source_profile_id else None
            now = utcnow()
            link.profile_id = target_profile_id
            link.resolution_source = "manual_relink"
            link.resolution_note = str(reason or "").strip() or f"Relinked by {actor_line_id or 'system'}"
            link.updated_at = now
            entity.updated_at = now
            session.add(link)
            session.add(entity)
            if source is not None:
                self._add_timeline_event(
                    session,
                    tenant_id=tenant_id,
                    profile_id=source.id,
                    activity_type="profile_link_moved_out",
                    title=f"移出 {normalized_channel_type} 身份：{normalized_user_id}",
                    payload={
                        "channel_type": normalized_channel_type,
                        "external_user_id": normalized_user_id,
                        "source_profile_id": source.id,
                        "target_profile_id": target_profile_id,
                        "actor_line_id": str(actor_line_id or "").strip(),
                        "reason": str(reason or "").strip(),
                    },
                )
            self._add_timeline_event(
                session,
                tenant_id=tenant_id,
                profile_id=target_profile_id,
                activity_type="profile_link_relinked",
                title=f"重新綁定 {normalized_channel_type} 身份：{normalized_user_id}",
                payload={
                    "channel_type": normalized_channel_type,
                    "external_user_id": normalized_user_id,
                    "source_profile_id": source_profile_id,
                    "target_profile_id": target_profile_id,
                    "actor_line_id": str(actor_line_id or "").strip(),
                    "reason": str(reason or "").strip(),
                },
            )
            session.commit()
            session.refresh(link)
            session.refresh(entity)
            return link, entity

    def count_customer_profiles(self, tenant_id: str) -> int:
        with Session(self._engine) as session:
            stmt = select(CustomerProfileTable).where(CustomerProfileTable.tenant_id == tenant_id)
            return len(list(session.exec(stmt).all()))

    def list_customer_profiles_for_tenant(self, tenant_id: str) -> list[CustomerProfileTable]:
        with Session(self._engine) as session:
            stmt = select(CustomerProfileTable).where(CustomerProfileTable.tenant_id == tenant_id)
            return list(session.exec(stmt).all())

    def merge_customer_profiles(
        self,
        *,
        tenant_id: str,
        source_profile_id: str,
        target_profile_id: str,
        actor_line_id: str = "",
        reason: str = "",
    ) -> ProfileMergeAuditTable:
        from kachu_plus.persistence.tables import utcnow

        if source_profile_id == target_profile_id:
            raise ValueError("source_profile_id and target_profile_id must be different")

        with Session(self._engine) as session:
            source = session.get(CustomerProfileTable, source_profile_id)
            target = session.get(CustomerProfileTable, target_profile_id)
            if source is None or source.tenant_id != tenant_id:
                raise LookupError("source profile not found")
            if target is None or target.tenant_id != tenant_id:
                raise LookupError("target profile not found")
            if str(source.merged_into_profile_id or "").strip():
                raise ValueError("source profile already merged")

            now = utcnow()
            target.interaction_count = int(target.interaction_count or 0) + int(source.interaction_count or 0)
            if target.last_interaction_at is None or (
                source.last_interaction_at is not None and source.last_interaction_at > target.last_interaction_at
            ):
                target.last_interaction_at = source.last_interaction_at
            target.opt_out = bool(target.opt_out or source.opt_out)
            if str(source.status or "") == "blacklisted":
                target.status = "blacklisted"
            if not str(target.display_name or "").strip() and str(source.display_name or "").strip():
                target.display_name = source.display_name
            if not str(target.custom_name or "").strip() and str(source.custom_name or "").strip():
                target.custom_name = source.custom_name
            target.updated_at = now
            session.add(target)

            moved_links = 0
            link_stmt = select(ProfileLinkTable).where(
                ProfileLinkTable.tenant_id == tenant_id,
                ProfileLinkTable.profile_id == source_profile_id,
            )
            for link in session.exec(link_stmt).all():
                link.profile_id = target_profile_id
                link.updated_at = now
                session.add(link)
                moved_links += 1

            moved_timeline_events = 0
            timeline_stmt = select(CustomerTimelineEventTable).where(
                CustomerTimelineEventTable.tenant_id == tenant_id,
                CustomerTimelineEventTable.profile_id == source_profile_id,
            )
            for event in session.exec(timeline_stmt).all():
                event.profile_id = target_profile_id
                session.add(event)
                moved_timeline_events += 1

            active_target_tag_ids = {
                row.tag_id
                for row in session.exec(
                    select(CustomerTagAssignmentTable).where(
                        CustomerTagAssignmentTable.tenant_id == tenant_id,
                        CustomerTagAssignmentTable.profile_id == target_profile_id,
                        CustomerTagAssignmentTable.removed_at == None,  # noqa: E711
                    )
                ).all()
            }
            attached_active_tags = 0
            collapsed_duplicate_tags = 0
            assignment_stmt = select(CustomerTagAssignmentTable).where(
                CustomerTagAssignmentTable.tenant_id == tenant_id,
                CustomerTagAssignmentTable.profile_id == source_profile_id,
            )
            for assignment in session.exec(assignment_stmt).all():
                if assignment.removed_at is None and assignment.tag_id in active_target_tag_ids:
                    assignment.removed_at = now
                    assignment.updated_at = now
                    session.add(assignment)
                    collapsed_duplicate_tags += 1
                    continue
                if assignment.removed_at is None:
                    active_target_tag_ids.add(assignment.tag_id)
                    attached_active_tags += 1
                assignment.profile_id = target_profile_id
                assignment.updated_at = now
                session.add(assignment)

            rewritten_suggestions = 0
            suggestion_stmt = select(SuggestionTable).where(SuggestionTable.tenant_id == tenant_id)
            for suggestion in session.exec(suggestion_stmt).all():
                try:
                    profile_ids = json.loads(suggestion.affected_profile_ids_json or "[]")
                except (TypeError, JSONDecodeError):
                    continue
                if source_profile_id not in profile_ids:
                    continue
                rewritten: list[str] = []
                for profile_id in profile_ids:
                    normalized = target_profile_id if profile_id == source_profile_id else str(profile_id or "").strip()
                    if normalized and normalized not in rewritten:
                        rewritten.append(normalized)
                suggestion.affected_profile_ids_json = json.dumps(rewritten, ensure_ascii=False)
                suggestion.updated_at = now
                session.add(suggestion)
                rewritten_suggestions += 1

            self._add_timeline_event(
                session,
                tenant_id=tenant_id,
                profile_id=target_profile_id,
                activity_type="profile_merged",
                title="合併顧客檔案",
                payload={
                    "source_profile_id": source_profile_id,
                    "target_profile_id": target_profile_id,
                    "actor_line_id": str(actor_line_id or "").strip(),
                    "reason": str(reason or "").strip(),
                },
            )

            source.status = "merged"
            source.merged_into_profile_id = target_profile_id
            source.updated_at = now
            session.add(source)

            summary = {
                "moved_links": moved_links,
                "moved_timeline_events": moved_timeline_events,
                "attached_active_tags": attached_active_tags,
                "collapsed_duplicate_tags": collapsed_duplicate_tags,
                "rewritten_suggestions": rewritten_suggestions,
            }
            audit = ProfileMergeAuditTable(
                tenant_id=tenant_id,
                source_profile_id=source_profile_id,
                target_profile_id=target_profile_id,
                actor_line_id=str(actor_line_id or "").strip(),
                reason=str(reason or "").strip(),
                summary_json=json.dumps(summary, ensure_ascii=False),
            )
            session.add(audit)
            session.commit()
            session.refresh(audit)
            return audit

    def list_profile_merge_audits(self, tenant_id: str, profile_id: str) -> list[ProfileMergeAuditTable]:
        with Session(self._engine) as session:
            stmt = (
                select(ProfileMergeAuditTable)
                .where(ProfileMergeAuditTable.tenant_id == tenant_id)
                .where(
                    (ProfileMergeAuditTable.source_profile_id == profile_id)
                    | (ProfileMergeAuditTable.target_profile_id == profile_id)
                )
                .order_by(ProfileMergeAuditTable.created_at.desc())
            )
            return list(session.exec(stmt).all())

    def upsert_conversation_handoff_lock(
        self,
        *,
        tenant_id: str,
        channel_type: str,
        external_user_id: str,
        locked_by_line_user_id: str,
        reason: str = "human_handoff",
    ) -> ConversationHandoffLockTable:
        from kachu_plus.persistence.tables import utcnow

        normalized_user_id = str(external_user_id or "").strip()
        if not normalized_user_id:
            raise ValueError("external_user_id is required")
        normalized_channel_type = str(channel_type or "line").strip() or "line"
        now = utcnow()
        with Session(self._engine) as session:
            stmt = (
                select(ConversationHandoffLockTable)
                .where(ConversationHandoffLockTable.tenant_id == tenant_id)
                .where(ConversationHandoffLockTable.channel_type == normalized_channel_type)
                .where(ConversationHandoffLockTable.external_user_id == normalized_user_id)
                .order_by(ConversationHandoffLockTable.locked_at.desc())
            )
            lock = session.exec(stmt).first()
            if lock is None:
                lock = ConversationHandoffLockTable(
                    tenant_id=tenant_id,
                    channel_type=normalized_channel_type,
                    external_user_id=normalized_user_id,
                )
            lock.reason = str(reason or "human_handoff").strip() or "human_handoff"
            lock.is_active = True
            lock.locked_by_line_user_id = str(locked_by_line_user_id or "").strip()
            lock.released_by_line_user_id = ""
            lock.locked_at = now
            lock.released_at = None
            lock.updated_at = now
            session.add(lock)
            session.commit()
            session.refresh(lock)
            return lock

    def get_active_conversation_handoff_lock(
        self,
        *,
        tenant_id: str,
        channel_type: str,
        external_user_id: str,
    ) -> Optional[ConversationHandoffLockTable]:
        normalized_user_id = str(external_user_id or "").strip()
        if not normalized_user_id:
            return None
        with Session(self._engine) as session:
            stmt = (
                select(ConversationHandoffLockTable)
                .where(ConversationHandoffLockTable.tenant_id == tenant_id)
                .where(ConversationHandoffLockTable.channel_type == (str(channel_type or "line").strip() or "line"))
                .where(ConversationHandoffLockTable.external_user_id == normalized_user_id)
                .where(ConversationHandoffLockTable.is_active == True)  # noqa: E712
                .order_by(ConversationHandoffLockTable.locked_at.desc())
            )
            return session.exec(stmt).first()

    def release_conversation_handoff_lock(
        self,
        *,
        tenant_id: str,
        channel_type: str,
        external_user_id: str,
        released_by_line_user_id: str,
    ) -> Optional[ConversationHandoffLockTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = (
                select(ConversationHandoffLockTable)
                .where(ConversationHandoffLockTable.tenant_id == tenant_id)
                .where(ConversationHandoffLockTable.channel_type == (str(channel_type or "line").strip() or "line"))
                .where(ConversationHandoffLockTable.external_user_id == str(external_user_id or "").strip())
                .where(ConversationHandoffLockTable.is_active == True)  # noqa: E712
                .order_by(ConversationHandoffLockTable.locked_at.desc())
            )
            lock = session.exec(stmt).first()
            if lock is None:
                return None
            now = utcnow()
            lock.is_active = False
            lock.released_by_line_user_id = str(released_by_line_user_id or "").strip()
            lock.released_at = now
            lock.updated_at = now
            session.add(lock)
            session.commit()
            session.refresh(lock)
            return lock

    # ── Customer Tags / Timeline ────────────────────────────────────────────

    def create_tag(
        self,
        tenant_id: str,
        *,
        name: str,
        color: str | None = None,
        source: str = "manual",
    ) -> CustomerTagDefinitionTable:
        from kachu_plus.persistence.tables import utcnow

        if self.get_tenant(tenant_id) is None:
            raise LookupError("tenant not found")

        tag = CustomerTagDefinitionTable(
            tenant_id=tenant_id,
            name=name.strip(),
            color=(color or "").strip(),
            source=source,
            updated_at=utcnow(),
        )
        with Session(self._engine) as session:
            try:
                session.add(tag)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("tag name already exists") from exc
            session.refresh(tag)
            return tag

    def list_tags(
        self,
        tenant_id: str,
        *,
        include_inactive: bool = False,
    ) -> list[CustomerTagDefinitionTable]:
        with Session(self._engine) as session:
            stmt = select(CustomerTagDefinitionTable).where(
                CustomerTagDefinitionTable.tenant_id == tenant_id,
            )
            if not include_inactive:
                stmt = stmt.where(CustomerTagDefinitionTable.is_active == True)  # noqa: E712
            stmt = stmt.order_by(CustomerTagDefinitionTable.created_at.asc())
            return list(session.exec(stmt).all())

    def get_tag(self, tenant_id: str, tag_id: str) -> Optional[CustomerTagDefinitionTable]:
        with Session(self._engine) as session:
            stmt = select(CustomerTagDefinitionTable).where(
                CustomerTagDefinitionTable.tenant_id == tenant_id,
                CustomerTagDefinitionTable.id == tag_id,
            )
            return session.exec(stmt).first()

    def update_tag(
        self,
        tenant_id: str,
        tag_id: str,
        *,
        name: str | None = None,
        color: str | None = None,
    ) -> CustomerTagDefinitionTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(CustomerTagDefinitionTable).where(
                CustomerTagDefinitionTable.tenant_id == tenant_id,
                CustomerTagDefinitionTable.id == tag_id,
            )
            tag = session.exec(stmt).first()
            if tag is None:
                raise LookupError("tag not found")

            if name is not None:
                tag.name = name.strip()
            if color is not None:
                tag.color = color.strip()
            tag.updated_at = utcnow()
            try:
                session.add(tag)
                session.commit()
            except IntegrityError as exc:
                session.rollback()
                raise ValueError("tag name already exists") from exc
            session.refresh(tag)
            return tag

    def deactivate_tag(self, tenant_id: str, tag_id: str) -> CustomerTagDefinitionTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(CustomerTagDefinitionTable).where(
                CustomerTagDefinitionTable.tenant_id == tenant_id,
                CustomerTagDefinitionTable.id == tag_id,
            )
            tag = session.exec(stmt).first()
            if tag is None:
                raise LookupError("tag not found")

            if not tag.is_active:
                return tag

            now = utcnow()
            tag.is_active = False
            tag.deleted_at = now
            tag.updated_at = now
            session.add(tag)
            session.commit()
            session.refresh(tag)
            return tag

    def assign_tag_to_profile(self, tenant_id: str, profile_id: str, tag_id: str) -> None:
        from kachu_plus.persistence.tables import utcnow

        now = utcnow()
        with Session(self._engine) as session:
            profile = session.get(CustomerProfileTable, profile_id)
            if profile is None or profile.tenant_id != tenant_id:
                raise LookupError("profile not found")

            tag = session.get(CustomerTagDefinitionTable, tag_id)
            if tag is None or tag.tenant_id != tenant_id:
                raise LookupError("tag not found")
            if not tag.is_active:
                raise ValueError("tag is inactive")

            stmt = select(CustomerTagAssignmentTable).where(
                CustomerTagAssignmentTable.tenant_id == tenant_id,
                CustomerTagAssignmentTable.profile_id == profile_id,
                CustomerTagAssignmentTable.tag_id == tag_id,
            )
            assignment = session.exec(stmt).first()
            if assignment is not None and assignment.removed_at is None:
                return

            if assignment is None:
                assignment = CustomerTagAssignmentTable(
                    tenant_id=tenant_id,
                    profile_id=profile_id,
                    tag_id=tag_id,
                    applied_at=now,
                    updated_at=now,
                )
            else:
                assignment.removed_at = None
                assignment.applied_at = now
                assignment.updated_at = now
            session.add(assignment)
            self._add_timeline_event(
                session,
                tenant_id=tenant_id,
                profile_id=profile_id,
                activity_type="tag_assigned",
                title=f"加入標籤：{tag.name}",
                payload={"tag_id": tag.id, "tag_name": tag.name, "tag_color": tag.color},
            )
            session.commit()

    def remove_tag_from_profile(self, tenant_id: str, profile_id: str, tag_id: str) -> None:
        from kachu_plus.persistence.tables import utcnow

        now = utcnow()
        with Session(self._engine) as session:
            profile = session.get(CustomerProfileTable, profile_id)
            if profile is None or profile.tenant_id != tenant_id:
                raise LookupError("profile not found")

            tag = session.get(CustomerTagDefinitionTable, tag_id)
            if tag is None or tag.tenant_id != tenant_id:
                raise LookupError("tag not found")

            stmt = select(CustomerTagAssignmentTable).where(
                CustomerTagAssignmentTable.tenant_id == tenant_id,
                CustomerTagAssignmentTable.profile_id == profile_id,
                CustomerTagAssignmentTable.tag_id == tag_id,
                CustomerTagAssignmentTable.removed_at == None,  # noqa: E711
            )
            assignment = session.exec(stmt).first()
            if assignment is None:
                return

            assignment.removed_at = now
            assignment.updated_at = now
            session.add(assignment)
            self._add_timeline_event(
                session,
                tenant_id=tenant_id,
                profile_id=profile_id,
                activity_type="tag_removed",
                title=f"移除標籤：{tag.name}",
                payload={"tag_id": tag.id, "tag_name": tag.name, "tag_color": tag.color},
            )
            session.commit()

    def _add_timeline_event(
        self,
        session: Session,
        *,
        tenant_id: str,
        profile_id: str,
        activity_type: str,
        title: str,
        payload: dict[str, object],
    ) -> None:
        event = CustomerTimelineEventTable(
            tenant_id=tenant_id,
            profile_id=profile_id,
            activity_type=activity_type,
            title=title,
            payload_json=CustomerTimelineEventTable.build_payload(**payload),
        )
        session.add(event)

    def resolve_or_create_line_profile(self, tenant_id: str, line_user_id: str) -> CustomerProfileTable:
        from kachu_plus.persistence.tables import utcnow

        now = utcnow()
        with Session(self._engine) as session:
            entity_stmt = select(ChannelEntityTable).where(
                ChannelEntityTable.tenant_id == tenant_id,
                ChannelEntityTable.channel_type == "line",
                ChannelEntityTable.external_user_id == line_user_id,
            )
            entity = session.exec(entity_stmt).first()

            if entity is not None:
                link_stmt = select(ProfileLinkTable).where(
                    ProfileLinkTable.tenant_id == tenant_id,
                    ProfileLinkTable.channel_entity_id == entity.id,
                )
                link = session.exec(link_stmt).first()
                if link is not None:
                    profile = session.get(CustomerProfileTable, link.profile_id)
                    if profile is not None:
                        profile.last_interaction_at = now
                        profile.interaction_count += 1
                        profile.updated_at = now
                        entity.occurred_at = now
                        entity.received_at = now
                        entity.updated_at = now
                        session.add(profile)
                        session.add(entity)
                        session.commit()
                        session.refresh(profile)
                        return profile

            profile = CustomerProfileTable(
                tenant_id=tenant_id,
                status="active",
                last_interaction_at=now,
                interaction_count=1,
                sleep_since_days=0,
            )
            session.add(profile)
            session.flush()

            entity = ChannelEntityTable(
                tenant_id=tenant_id,
                channel_type="line",
                external_user_id=line_user_id,
                reachability_status="reachable",
                occurred_at=now,
                received_at=now,
            )
            session.add(entity)
            session.flush()

            link = ProfileLinkTable(
                tenant_id=tenant_id,
                profile_id=profile.id,
                channel_entity_id=entity.id,
                confidence_score=1.0,
                resolution_source="inferred",
                resolution_note="Auto-created from inbound LINE event",
            )
            session.add(link)
            session.commit()
            session.refresh(profile)
            return profile

    def list_sleeping_customer_profiles(
        self,
        tenant_id: str,
        *,
        minimum_days: int,
        limit: int = 20,
    ) -> list[CustomerProfileTable]:
        with Session(self._engine) as session:
            stmt = (
                select(CustomerProfileTable)
                .where(
                    CustomerProfileTable.tenant_id == tenant_id,
                    CustomerProfileTable.sleep_since_days >= minimum_days,
                    CustomerProfileTable.opt_out == False,  # noqa: E712
                    CustomerProfileTable.status != "blacklisted",
                )
                .order_by(CustomerProfileTable.sleep_since_days.desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def get_profile_line_user_ids(self, tenant_id: str, profile_ids: list[str]) -> list[str]:
        if not profile_ids:
            return []
        with Session(self._engine) as session:
            stmt = (
                select(ChannelEntityTable.external_user_id)
                .join(ProfileLinkTable, ProfileLinkTable.channel_entity_id == ChannelEntityTable.id)
                .join(CustomerProfileTable, CustomerProfileTable.id == ProfileLinkTable.profile_id)
                .where(ProfileLinkTable.tenant_id == tenant_id)
                .where(ProfileLinkTable.profile_id.in_(profile_ids))
                .where(ChannelEntityTable.channel_type == "line")
                .where(ChannelEntityTable.reachability_status == "reachable")
                .where(CustomerProfileTable.opt_out == False)  # noqa: E712
                .where(CustomerProfileTable.status != "blacklisted")
            )
            recipients: list[str] = []
            for value in session.exec(stmt).all():
                normalized = str(value or "").strip()
                if normalized and normalized not in recipients:
                    recipients.append(normalized)
            return recipients

    # ── LINE Channel Config ───────────────────────────────────────────────────

    def get_line_channel_config(self, tenant_id: str) -> Optional[LineChannelConfigTable]:
        with Session(self._engine) as session:
            stmt = (
                select(LineChannelConfigTable)
                .where(
                    LineChannelConfigTable.tenant_id == tenant_id,
                    LineChannelConfigTable.is_active == True,  # noqa: E712
                )
            )
            return session.exec(stmt).first()

    def record_webhook_event_if_new(
        self,
        *,
        tenant_id: str,
        provider: str,
        dedupe_key: str,
        event_type: str,
        raw_payload: dict[str, object],
        external_event_id: str = "",
        external_user_id: str = "",
        external_thread_id: str = "",
        occurred_at: datetime | None = None,
        received_at: datetime | None = None,
    ) -> bool:
        normalized_key = str(dedupe_key or "").strip()
        if not normalized_key:
            raise ValueError("dedupe_key is required")
        with Session(self._engine) as session:
            stmt = (
                select(WebhookEventTable)
                .where(WebhookEventTable.tenant_id == tenant_id)
                .where(WebhookEventTable.provider == provider)
                .where(WebhookEventTable.dedupe_key == normalized_key)
            )
            existing = session.exec(stmt).first()
            if existing is not None:
                return False
            event = WebhookEventTable(
                tenant_id=tenant_id,
                provider=provider,
                dedupe_key=normalized_key,
                event_type=str(event_type or "").strip(),
                external_event_id=str(external_event_id or "").strip(),
                external_user_id=str(external_user_id or "").strip(),
                external_thread_id=str(external_thread_id or "").strip(),
                occurred_at=self._as_utc(occurred_at),
                received_at=self._as_utc(received_at) or datetime.now(timezone.utc),
                raw_payload_json=json.dumps(raw_payload, ensure_ascii=False, sort_keys=True),
            )
            session.add(event)
            session.commit()
            return True

    def list_webhook_events(
        self,
        tenant_id: str,
        *,
        provider: str | None = None,
        event_type: str | None = None,
        external_user_id: str | None = None,
        external_thread_id: str | None = None,
        limit: int = 50,
    ) -> list[WebhookEventTable]:
        with Session(self._engine) as session:
            stmt = select(WebhookEventTable).where(WebhookEventTable.tenant_id == tenant_id)
            if provider:
                stmt = stmt.where(WebhookEventTable.provider == provider)
            if event_type:
                stmt = stmt.where(WebhookEventTable.event_type == event_type)
            if external_user_id:
                stmt = stmt.where(WebhookEventTable.external_user_id == external_user_id)
            if external_thread_id:
                stmt = stmt.where(WebhookEventTable.external_thread_id == external_thread_id)
            stmt = stmt.order_by(WebhookEventTable.received_at.desc(), WebhookEventTable.created_at.desc()).limit(max(int(limit), 1))
            return list(session.exec(stmt).all())

    def get_webhook_event(self, event_id: str) -> Optional[WebhookEventTable]:
        with Session(self._engine) as session:
            return session.get(WebhookEventTable, event_id)

    def save_conversation(
        self,
        *,
        tenant_id: str,
        actor_role: str,
        channel_type: str,
        conversation_kind: str,
        content_text: str,
        line_user_id: str = "",
        source_message_id: str = "",
        related_task_id: str = "",
        related_run_id: str = "",
        metadata: dict[str, object] | None = None,
    ) -> ConversationTable:
        conversation = ConversationTable(
            tenant_id=tenant_id,
            line_user_id=str(line_user_id or "").strip(),
            actor_role=str(actor_role or "").strip(),
            channel_type=str(channel_type or "line").strip() or "line",
            conversation_kind=str(conversation_kind or "").strip(),
            content_text=str(content_text or "").strip(),
            source_message_id=str(source_message_id or "").strip(),
            related_task_id=str(related_task_id or "").strip(),
            related_run_id=str(related_run_id or "").strip(),
            metadata_json=json.dumps(metadata or {}, ensure_ascii=False),
        )
        with Session(self._engine) as session:
            session.add(conversation)
            session.commit()
            session.refresh(conversation)
            return conversation

    def list_recent_conversations(
        self,
        tenant_id: str,
        *,
        limit: int = 10,
        line_user_id: str | None = None,
        actor_roles: list[str] | None = None,
        conversation_kinds: list[str] | None = None,
    ) -> list[ConversationTable]:
        with Session(self._engine) as session:
            stmt = select(ConversationTable).where(ConversationTable.tenant_id == tenant_id)
            if line_user_id:
                stmt = stmt.where(ConversationTable.line_user_id == line_user_id)
            if actor_roles:
                stmt = stmt.where(ConversationTable.actor_role.in_(actor_roles))
            if conversation_kinds:
                stmt = stmt.where(ConversationTable.conversation_kind.in_(conversation_kinds))
            stmt = stmt.order_by(ConversationTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def list_related_conversations(
        self,
        tenant_id: str,
        *,
        related_task_id: str = "",
        related_run_id: str = "",
        line_user_id: str = "",
        limit: int = 20,
    ) -> list[ConversationTable]:
        with Session(self._engine) as session:
            stmt = select(ConversationTable).where(ConversationTable.tenant_id == tenant_id)
            if related_task_id:
                stmt = stmt.where(ConversationTable.related_task_id == related_task_id)
            if related_run_id:
                stmt = stmt.where(ConversationTable.related_run_id == related_run_id)
            if line_user_id:
                stmt = stmt.where(ConversationTable.line_user_id == line_user_id)
            stmt = stmt.order_by(ConversationTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    # ── Connector Accounts ───────────────────────────────────────────────────

    def save_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
        credentials_json: str,
        account_label: str = "",
        touch_refreshed_at: bool = True,
    ) -> ConnectorAccountTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            account = session.exec(stmt).first()
            now = utcnow()
            if account is None:
                account = ConnectorAccountTable(
                    tenant_id=tenant_id,
                    platform=platform,
                    account_label=account_label,
                    credentials_json=credentials_json,
                    last_refreshed_at=now if touch_refreshed_at else None,
                    updated_at=now,
                )
            else:
                account.account_label = account_label
                account.credentials_json = credentials_json
                account.updated_at = now
                if touch_refreshed_at:
                    account.last_refreshed_at = now
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_meta_connector_by_page_id(self, fb_page_id: str) -> Optional[ConnectorAccountTable]:
        target = str(fb_page_id or "").strip()
        if not target:
            return None
        with Session(self._engine) as session:
            stmt = select(ConnectorAccountTable).where(
                ConnectorAccountTable.platform == "meta",
                ConnectorAccountTable.is_active == True,  # noqa: E712
            )
            for account in session.exec(stmt).all():
                try:
                    payload = json.loads(account.credentials_json or "{}")
                except (TypeError, JSONDecodeError):
                    continue
                if str(payload.get("fb_page_id", "") or "").strip() == target:
                    return account
            return None

    def update_connector_account(
        self,
        *,
        tenant_id: str,
        platform: str,
        credentials_json: str | None = None,
        account_label: str | None = None,
        touch_refreshed_at: bool = False,
    ) -> Optional[ConnectorAccountTable]:
        from kachu_plus.persistence.tables import utcnow

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
            now = utcnow()
            if credentials_json is not None:
                account.credentials_json = credentials_json
            if account_label is not None:
                account.account_label = account_label
            if touch_refreshed_at:
                account.last_refreshed_at = now
            account.updated_at = now
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    def get_connector_account(self, tenant_id: str, platform: str) -> Optional[ConnectorAccountTable]:
        with Session(self._engine) as session:
            stmt = (
                select(ConnectorAccountTable)
                .where(ConnectorAccountTable.tenant_id == tenant_id)
                .where(ConnectorAccountTable.platform == platform)
                .where(ConnectorAccountTable.is_active == True)  # noqa: E712
            )
            return session.exec(stmt).first()

    def deactivate_connector_account(self, tenant_id: str, platform: str) -> Optional[ConnectorAccountTable]:
        from kachu_plus.persistence.tables import utcnow

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
            account.updated_at = utcnow()
            session.add(account)
            session.commit()
            session.refresh(account)
            return account

    # ── Meta OAuth Sessions ────────────────────────────────────────────────

    def create_meta_oauth_session(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        state: str,
        requested_platform: str = "meta",
        expires_at: datetime | None = None,
    ) -> MetaOAuthSessionTable:
        from kachu_plus.persistence.tables import utcnow

        normalized_state = str(state or "").strip()
        if not normalized_state:
            raise ValueError("state is required")
        with Session(self._engine) as session:
            now = utcnow()
            oauth_session = MetaOAuthSessionTable(
                tenant_id=tenant_id,
                line_user_id=str(line_user_id or "").strip(),
                state=normalized_state,
                requested_platform=str(requested_platform or "meta").strip() or "meta",
                expires_at=self._as_utc(expires_at),
                updated_at=now,
            )
            session.add(oauth_session)
            session.commit()
            session.refresh(oauth_session)
            return oauth_session

    def get_meta_oauth_session(self, session_id: str) -> Optional[MetaOAuthSessionTable]:
        with Session(self._engine) as session:
            return session.get(MetaOAuthSessionTable, session_id)

    def get_meta_oauth_session_by_state(self, state: str) -> Optional[MetaOAuthSessionTable]:
        normalized_state = str(state or "").strip()
        if not normalized_state:
            return None
        with Session(self._engine) as session:
            stmt = select(MetaOAuthSessionTable).where(MetaOAuthSessionTable.state == normalized_state)
            return session.exec(stmt).first()

    def update_meta_oauth_session(
        self,
        *,
        session_id: str,
        status: str | None = None,
        page_candidates: list[dict[str, object]] | None = None,
        selected_page_id: str | None = None,
        selected_page_name: str | None = None,
        selected_ig_user_id: str | None = None,
        user_access_token: str | None = None,
        fb_page_access_token: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        expires_at: datetime | None = None,
        completed_at: datetime | None = None,
    ) -> Optional[MetaOAuthSessionTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            oauth_session = session.get(MetaOAuthSessionTable, session_id)
            if oauth_session is None:
                return None
            if status is not None:
                oauth_session.status = str(status or "").strip() or oauth_session.status
            if page_candidates is not None:
                oauth_session.page_candidates_json = json.dumps(page_candidates, ensure_ascii=False)
            if selected_page_id is not None:
                oauth_session.selected_page_id = str(selected_page_id or "").strip()
            if selected_page_name is not None:
                oauth_session.selected_page_name = str(selected_page_name or "").strip()
            if selected_ig_user_id is not None:
                oauth_session.selected_ig_user_id = str(selected_ig_user_id or "").strip()
            if user_access_token is not None:
                oauth_session.user_access_token = str(user_access_token or "")
            if fb_page_access_token is not None:
                oauth_session.fb_page_access_token = str(fb_page_access_token or "")
            if error_code is not None:
                oauth_session.error_code = str(error_code or "").strip()
            if error_message is not None:
                oauth_session.error_message = str(error_message or "").strip()
            if expires_at is not None:
                oauth_session.expires_at = self._as_utc(expires_at)
            if completed_at is not None:
                oauth_session.completed_at = self._as_utc(completed_at)
            oauth_session.updated_at = utcnow()
            session.add(oauth_session)
            session.commit()
            session.refresh(oauth_session)
            return oauth_session

    def list_active_meta_oauth_sessions(self, tenant_id: str) -> list[MetaOAuthSessionTable]:
        now = datetime.now(timezone.utc)
        with Session(self._engine) as session:
            stmt = (
                select(MetaOAuthSessionTable)
                .where(MetaOAuthSessionTable.tenant_id == tenant_id)
                .where(MetaOAuthSessionTable.status.in_(["pending", "authorized", "selecting_page", "ready_to_connect", "awaiting_overwrite_confirmation"]))
                .where((MetaOAuthSessionTable.expires_at.is_(None)) | (MetaOAuthSessionTable.expires_at > now))
            )
            return list(session.exec(stmt).all())

    # ── Execute Task Tracking ───────────────────────────────────────────────

    def save_execute_task_record(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        intent_label: str,
        source_text: str,
        objective: str,
        task_id: str,
        run_id: str = "",
        related_conversation_id: str = "",
        status: str = "created",
    ) -> ExecuteTaskRecordTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(ExecuteTaskRecordTable).where(ExecuteTaskRecordTable.task_id == task_id)
            record = session.exec(stmt).first()
            now = utcnow()
            if record is None:
                record = ExecuteTaskRecordTable(
                    tenant_id=tenant_id,
                    line_user_id=line_user_id,
                    intent_label=intent_label,
                    source_text=source_text,
                    objective=objective,
                    task_id=task_id,
                    run_id=run_id,
                    related_conversation_id=related_conversation_id,
                    status=status,
                    updated_at=now,
                )
            else:
                record.tenant_id = tenant_id
                record.line_user_id = line_user_id
                record.intent_label = intent_label
                record.source_text = source_text
                record.objective = objective
                record.run_id = run_id
                if related_conversation_id:
                    record.related_conversation_id = related_conversation_id
                record.status = status or record.status
                record.updated_at = now
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    def get_latest_execute_task_record(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        intent_label: str | None = None,
    ) -> Optional[ExecuteTaskRecordTable]:
        with Session(self._engine) as session:
            stmt = select(ExecuteTaskRecordTable).where(
                ExecuteTaskRecordTable.tenant_id == tenant_id,
                ExecuteTaskRecordTable.line_user_id == line_user_id,
            )
            if intent_label:
                stmt = stmt.where(ExecuteTaskRecordTable.intent_label == intent_label)
            stmt = stmt.order_by(ExecuteTaskRecordTable.created_at.desc())
            return session.exec(stmt).first()

    def get_latest_execute_task_for_tenant(self, tenant_id: str) -> Optional[ExecuteTaskRecordTable]:
        with Session(self._engine) as session:
            stmt = (
                select(ExecuteTaskRecordTable)
                .where(ExecuteTaskRecordTable.tenant_id == tenant_id)
                .order_by(ExecuteTaskRecordTable.created_at.desc())
            )
            return session.exec(stmt).first()

    def get_execute_task_record_by_task_id(self, task_id: str) -> Optional[ExecuteTaskRecordTable]:
        with Session(self._engine) as session:
            stmt = select(ExecuteTaskRecordTable).where(ExecuteTaskRecordTable.task_id == task_id)
            return session.exec(stmt).first()

    def update_execute_task_record(
        self,
        *,
        task_id: str,
        run_id: str | None = None,
        status: str | None = None,
    ) -> Optional[ExecuteTaskRecordTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(ExecuteTaskRecordTable).where(ExecuteTaskRecordTable.task_id == task_id)
            record = session.exec(stmt).first()
            if record is None:
                return None
            if run_id is not None:
                record.run_id = run_id
            if status is not None:
                record.status = status
            record.updated_at = utcnow()
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    # ── Pending Approvals / Publishing ──────────────────────────────────────

    def save_pending_approval(
        self,
        *,
        tenant_id: str,
        agentos_task_id: str,
        agentos_run_id: str,
        workflow_type: str,
        draft_content: str,
        review_id: str = "",
    ) -> PendingApprovalTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == agentos_run_id)
            pending = session.exec(stmt).first()
            now = utcnow()
            if pending is None:
                pending = PendingApprovalTable(
                    tenant_id=tenant_id,
                    agentos_task_id=agentos_task_id,
                    agentos_run_id=agentos_run_id,
                    workflow_type=workflow_type,
                    draft_content=draft_content,
                    review_id=review_id,
                    updated_at=now,
                )
            else:
                pending.tenant_id = tenant_id
                pending.agentos_task_id = agentos_task_id
                pending.workflow_type = workflow_type
                pending.draft_content = draft_content
                pending.review_id = review_id
                pending.status = "pending"
                pending.updated_at = now
                pending.decided_at = None
            session.add(pending)
            session.commit()
            session.refresh(pending)
            return pending

    def get_pending_approval_by_run_id(self, run_id: str) -> Optional[PendingApprovalTable]:
        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == run_id)
            return session.exec(stmt).first()

    def list_pending_approvals(
        self,
        *,
        statuses: list[str] | None = None,
        exclude_workflow_types: list[str] | None = None,
        limit: int = 50,
    ) -> list[PendingApprovalTable]:
        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable)
            if statuses:
                stmt = stmt.where(PendingApprovalTable.status.in_(statuses))
            if exclude_workflow_types:
                stmt = stmt.where(PendingApprovalTable.workflow_type.notin_(exclude_workflow_types))
            stmt = stmt.order_by(PendingApprovalTable.updated_at.asc(), PendingApprovalTable.created_at.asc()).limit(max(int(limit), 1))
            return list(session.exec(stmt).all())

    def get_latest_editing_approval(
        self,
        *,
        tenant_id: str,
        actor_line_id: str,
    ) -> Optional[PendingApprovalTable]:
        with Session(self._engine) as session:
            stmt = (
                select(PendingApprovalTable)
                .where(
                    PendingApprovalTable.tenant_id == tenant_id,
                    PendingApprovalTable.status == "editing",
                    PendingApprovalTable.actor_line_id == actor_line_id,
                )
                .order_by(PendingApprovalTable.updated_at.desc(), PendingApprovalTable.created_at.desc())
            )
            return session.exec(stmt).first()

    def decide_pending_approval(
        self,
        *,
        agentos_run_id: str,
        decision: str,
        actor_line_id: str,
        decision_payload: dict[str, object] | None = None,
    ) -> Optional[PendingApprovalTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == agentos_run_id)
            pending = session.exec(stmt).first()
            if pending is None:
                return None
            now = utcnow()
            pending.status = decision
            pending.actor_line_id = actor_line_id
            pending.decision_payload_json = json.dumps(decision_payload or {}, ensure_ascii=False)
            pending.updated_at = now
            pending.decided_at = now
            session.add(pending)
            session.commit()
            session.refresh(pending)
            return pending

    def update_pending_approval_draft(
        self,
        *,
        agentos_run_id: str,
        draft_content: str,
        actor_line_id: str,
        status: str = "pending",
    ) -> Optional[PendingApprovalTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(PendingApprovalTable.agentos_run_id == agentos_run_id)
            pending = session.exec(stmt).first()
            if pending is None:
                return None
            pending.draft_content = draft_content
            pending.status = status
            pending.actor_line_id = actor_line_id
            pending.updated_at = utcnow()
            pending.decided_at = None
            session.add(pending)
            session.commit()
            session.refresh(pending)
            return pending

    def list_due_scheduled_approvals(
        self,
        *,
        limit: int = 20,
        now: datetime | None = None,
    ) -> list[PendingApprovalTable]:
        from kachu_plus.persistence.tables import utcnow

        current = self._as_utc(now) or utcnow()
        with Session(self._engine) as session:
            stmt = (
                select(PendingApprovalTable)
                .where(PendingApprovalTable.status == "scheduled")
                .order_by(PendingApprovalTable.decided_at.asc(), PendingApprovalTable.created_at.asc())
            )
            due_items: list[PendingApprovalTable] = []
            for pending in session.exec(stmt).all():
                try:
                    payload = json.loads(pending.decision_payload_json or "{}")
                except (TypeError, JSONDecodeError):
                    continue
                scheduled_raw = str(payload.get("scheduled_for", "") or "").strip()
                if not scheduled_raw:
                    continue
                try:
                    scheduled_at = datetime.fromisoformat(scheduled_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                scheduled_at = self._as_utc(scheduled_at)
                if scheduled_at is None or scheduled_at > current:
                    continue
                due_items.append(pending)
                if len(due_items) >= max(int(limit), 1):
                    break
            return due_items

    def record_published_content(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        channel: str,
        source_id: str,
        source_ref: str,
        content_text: str,
        payload: dict[str, object] | None = None,
    ) -> PublishedContentRecordTable:
        record = PublishedContentRecordTable(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            channel=channel,
            source_id=source_id,
            source_ref=source_ref,
            content_text=content_text,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
        )
        with Session(self._engine) as session:
            session.add(record)
            session.commit()
            session.refresh(record)
            return record

    # ── Content Plans ─────────────────────────────────────────────────────

    def create_content_plan(
        self,
        *,
        tenant_id: str,
        objective: str,
        plan_payload: dict[str, object],
        source_conversation_id: str = "",
        created_by_line_user_id: str = "",
        status: str = "active",
    ) -> ContentPlanTable:
        from kachu_plus.persistence.tables import utcnow

        plan = ContentPlanTable(
            tenant_id=tenant_id,
            source_conversation_id=source_conversation_id,
            created_by_line_user_id=created_by_line_user_id,
            objective=objective,
            status=status,
            plan_payload_json=json.dumps(plan_payload, ensure_ascii=False),
            updated_at=utcnow(),
        )
        with Session(self._engine) as session:
            session.add(plan)
            session.commit()
            session.refresh(plan)
            return plan

    def get_content_plan(self, content_plan_id: str) -> Optional[ContentPlanTable]:
        with Session(self._engine) as session:
            return session.get(ContentPlanTable, content_plan_id)

    def create_content_plan_item(
        self,
        *,
        content_plan_id: str,
        tenant_id: str,
        title: str,
        selected_platforms: list[str],
        scheduled_for: datetime | None,
        draft_payload: dict[str, object] | None = None,
        workflow_type: str = "kachu_planned_content",
        status: str = "planned",
        pending_run_id: str = "",
    ) -> ContentPlanItemTable:
        from kachu_plus.persistence.tables import utcnow

        item = ContentPlanItemTable(
            content_plan_id=content_plan_id,
            tenant_id=tenant_id,
            title=title,
            workflow_type=workflow_type,
            selected_platforms_json=json.dumps(selected_platforms, ensure_ascii=False),
            draft_payload_json=json.dumps(draft_payload or {}, ensure_ascii=False),
            status=status,
            scheduled_for=scheduled_for,
            pending_run_id=pending_run_id,
            updated_at=utcnow(),
        )
        with Session(self._engine) as session:
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    def get_content_plan_item(self, item_id: str) -> Optional[ContentPlanItemTable]:
        with Session(self._engine) as session:
            return session.get(ContentPlanItemTable, item_id)

    def list_content_plan_items(self, content_plan_id: str, *, limit: int = 50) -> list[ContentPlanItemTable]:
        with Session(self._engine) as session:
            stmt = (
                select(ContentPlanItemTable)
                .where(ContentPlanItemTable.content_plan_id == content_plan_id)
                .order_by(ContentPlanItemTable.scheduled_for.asc(), ContentPlanItemTable.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def list_due_content_plan_items(
        self,
        *,
        now: datetime | None = None,
        limit: int = 20,
    ) -> list[ContentPlanItemTable]:
        current = self._as_utc(now) or datetime.now(timezone.utc)
        with Session(self._engine) as session:
            stmt = (
                select(ContentPlanItemTable)
                .where(ContentPlanItemTable.status == "planned")
                .where(ContentPlanItemTable.scheduled_for != None)  # noqa: E711
                .where(ContentPlanItemTable.scheduled_for <= current)
                .order_by(ContentPlanItemTable.scheduled_for.asc(), ContentPlanItemTable.created_at.asc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def update_content_plan_item(
        self,
        *,
        item_id: str,
        status: str | None = None,
        draft_payload: dict[str, object] | None = None,
        pending_run_id: str | None = None,
        scheduled_for: datetime | None = None,
    ) -> Optional[ContentPlanItemTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            item = session.get(ContentPlanItemTable, item_id)
            if item is None:
                return None
            if status is not None:
                item.status = status
            if draft_payload is not None:
                item.draft_payload_json = json.dumps(draft_payload, ensure_ascii=False)
            if pending_run_id is not None:
                item.pending_run_id = pending_run_id
            if scheduled_for is not None:
                item.scheduled_for = scheduled_for
            item.updated_at = utcnow()
            session.add(item)
            session.commit()
            session.refresh(item)
            return item

    # ── External Engagements ─────────────────────────────────────────────

    def create_external_engagement(
        self,
        *,
        tenant_id: str,
        platform: str,
        engagement_type: str,
        external_thread_id: str,
        external_message_id: str,
        author_name: str,
        author_id: str,
        content_text: str,
        source_payload: dict[str, object] | None = None,
        status: str = "received",
        reply_draft: str = "",
        related_run_id: str = "",
    ) -> ExternalEngagementTable:
        from kachu_plus.persistence.tables import utcnow

        entry = ExternalEngagementTable(
            tenant_id=tenant_id,
            platform=platform,
            engagement_type=engagement_type,
            external_thread_id=external_thread_id,
            external_message_id=external_message_id,
            author_name=author_name,
            author_id=author_id,
            content_text=content_text,
            status=status,
            reply_draft=reply_draft,
            related_run_id=related_run_id,
            source_payload_json=json.dumps(source_payload or {}, ensure_ascii=False),
            updated_at=utcnow(),
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_external_engagement(self, engagement_id: str) -> Optional[ExternalEngagementTable]:
        with Session(self._engine) as session:
            return session.get(ExternalEngagementTable, engagement_id)

    def get_external_engagement_by_message_id(self, external_message_id: str) -> Optional[ExternalEngagementTable]:
        with Session(self._engine) as session:
            stmt = select(ExternalEngagementTable).where(ExternalEngagementTable.external_message_id == external_message_id)
            return session.exec(stmt).first()

    def update_external_engagement(
        self,
        *,
        engagement_id: str,
        status: str | None = None,
        reply_draft: str | None = None,
        related_run_id: str | None = None,
    ) -> Optional[ExternalEngagementTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            entry = session.get(ExternalEngagementTable, engagement_id)
            if entry is None:
                return None
            if status is not None:
                entry.status = status
            if reply_draft is not None:
                entry.reply_draft = reply_draft
            if related_run_id is not None:
                entry.related_run_id = related_run_id
            entry.updated_at = utcnow()
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def list_pending_external_engagements(
        self,
        tenant_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int = 20,
    ) -> list[ExternalEngagementTable]:
        with Session(self._engine) as session:
            stmt = select(ExternalEngagementTable).where(ExternalEngagementTable.tenant_id == tenant_id)
            if statuses:
                stmt = stmt.where(ExternalEngagementTable.status.in_(statuses))
            stmt = stmt.order_by(ExternalEngagementTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def get_last_published_at(self, tenant_id: str):
        with Session(self._engine) as session:
            stmt = (
                select(PublishedContentRecordTable)
                .where(PublishedContentRecordTable.tenant_id == tenant_id)
                .order_by(PublishedContentRecordTable.created_at.desc())
            )
            record = session.exec(stmt).first()
            return record.created_at if record is not None else None

    def get_pending_negative_reviews(self, tenant_id: str) -> int:
        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(
                PendingApprovalTable.tenant_id == tenant_id,
                PendingApprovalTable.workflow_type == "kachu_review_reply",
                PendingApprovalTable.status == "pending",
            )
            rows = list(session.exec(stmt).all())
            total = 0
            for row in rows:
                try:
                    payload = json.loads(row.draft_content or "{}")
                except (TypeError, JSONDecodeError):
                    continue
                sentiment = str(payload.get("sentiment", {}).get("sentiment", "")).lower()
                if sentiment == "negative":
                    total += 1
            return total

    # ── Suggestions ─────────────────────────────────────────────────────────

    def create_suggestion(
        self,
        *,
        tenant_id: str,
        suggestion_type: str,
        category: str,
        title: str,
        reason: str,
        body: str,
        affected_profile_ids: list[str] | None = None,
        profile_count: int = 0,
        suggested_action: str = "",
        draft_message: str = "",
        related_run_id: str = "",
        payload: dict[str, object] | None = None,
        expires_at=None,
    ) -> SuggestionTable:
        from kachu_plus.persistence.tables import utcnow

        suggestion = SuggestionTable(
            tenant_id=tenant_id,
            suggestion_type=suggestion_type,
            category=category,
            title=title,
            reason=reason,
            body=body,
            affected_profile_ids_json=json.dumps(affected_profile_ids or [], ensure_ascii=False),
            profile_count=profile_count,
            suggested_action=suggested_action,
            draft_message=draft_message,
            related_run_id=related_run_id,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            expires_at=expires_at,
            updated_at=utcnow(),
        )
        with Session(self._engine) as session:
            session.add(suggestion)
            session.commit()
            session.refresh(suggestion)
            return suggestion

    def get_suggestion(self, suggestion_id: str) -> Optional[SuggestionTable]:
        with Session(self._engine) as session:
            return session.get(SuggestionTable, suggestion_id)

    def save_suggestion(self, suggestion: SuggestionTable) -> SuggestionTable:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            suggestion.updated_at = utcnow()
            merged = session.merge(suggestion)
            session.commit()
            session.refresh(merged)
            return merged

    def get_latest_active_suggestion(self, tenant_id: str, suggestion_type: str) -> Optional[SuggestionTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            now = utcnow()
            stmt = (
                select(SuggestionTable)
                .where(SuggestionTable.tenant_id == tenant_id)
                .where(SuggestionTable.suggestion_type == suggestion_type)
                .where(SuggestionTable.status.in_(["pending", "accepted"]))
                .order_by(SuggestionTable.created_at.desc())
            )
            for suggestion in session.exec(stmt).all():
                expires_at = self._as_utc(suggestion.expires_at)
                if expires_at is None or expires_at >= now:
                    return suggestion
            return None

    def expire_due_suggestions(self, tenant_id: str | None = None) -> int:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            now = utcnow()
            stmt = select(SuggestionTable).where(
                SuggestionTable.status.in_(["pending", "accepted"]),
                SuggestionTable.expires_at != None,  # noqa: E711
                SuggestionTable.expires_at < now,
            )
            if tenant_id is not None:
                stmt = stmt.where(SuggestionTable.tenant_id == tenant_id)
            suggestions = list(session.exec(stmt).all())
            for suggestion in suggestions:
                expires_at = self._as_utc(suggestion.expires_at)
                if expires_at is None or expires_at >= now:
                    continue
                suggestion.status = "expired"
                suggestion.updated_at = now
                session.add(suggestion)
            session.commit()
            return len(suggestions)

    def update_suggestion_status(
        self,
        *,
        suggestion_id: str,
        status: str,
        result_snapshot: dict[str, object] | None = None,
        allowed_current_statuses: list[str] | None = None,
    ) -> Optional[SuggestionTable]:
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            suggestion = session.get(SuggestionTable, suggestion_id)
            if suggestion is None:
                return None
            if allowed_current_statuses is not None and suggestion.status not in allowed_current_statuses:
                raise ValueError("invalid suggestion status transition")
            now = utcnow()
            suggestion.status = status
            suggestion.updated_at = now
            if status == "sent":
                suggestion.sent_at = now
            if result_snapshot is not None:
                suggestion.result_snapshot_json = json.dumps(result_snapshot, ensure_ascii=False)
            session.add(suggestion)
            session.commit()
            session.refresh(suggestion)
            return suggestion

    def list_pending_suggestions(self, tenant_id: str) -> list[SuggestionTable]:
        with Session(self._engine) as session:
            stmt = (
                select(SuggestionTable)
                .where(SuggestionTable.tenant_id == tenant_id, SuggestionTable.status.in_(["pending", "accepted"]))
                .order_by(SuggestionTable.created_at.desc())
            )
            return list(session.exec(stmt).all())

    def list_suggestions(
        self,
        tenant_id: str,
        *,
        statuses: list[str] | None = None,
        limit: int = 200,
    ) -> list[SuggestionTable]:
        with Session(self._engine) as session:
            stmt = select(SuggestionTable).where(SuggestionTable.tenant_id == tenant_id)
            if statuses:
                stmt = stmt.where(SuggestionTable.status.in_(statuses))
            stmt = stmt.order_by(SuggestionTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    # ── Onboarding State ──────────────────────────────────────────────────────

    def get_onboarding_state(self, tenant_id: str) -> Optional[OnboardingStateTable]:
        with Session(self._engine) as session:
            stmt = select(OnboardingStateTable).where(
                OnboardingStateTable.tenant_id == tenant_id
            )
            return session.exec(stmt).first()

    def get_or_create_onboarding_state(self, tenant_id: str) -> OnboardingStateTable:
        state = self.get_onboarding_state(tenant_id)
        if state is not None:
            return state
        state = OnboardingStateTable(tenant_id=tenant_id, step="new")
        with Session(self._engine) as session:
            session.add(state)
            session.commit()
            session.refresh(state)
        return state

    def update_onboarding_step(self, tenant_id: str, step: str) -> None:
        from kachu_plus.persistence.tables import utcnow
        with Session(self._engine) as session:
            stmt = select(OnboardingStateTable).where(
                OnboardingStateTable.tenant_id == tenant_id
            )
            state = session.exec(stmt).first()
            if state is None:
                state = OnboardingStateTable(tenant_id=tenant_id)
                session.add(state)
            state.step = step
            state.updated_at = utcnow()
            session.commit()

    def is_onboarding_complete(self, tenant_id: str) -> bool:
        state = self.get_onboarding_state(tenant_id)
        if state is None:
            return False
        return state.step == "completed"

    # ── Knowledge Entries ─────────────────────────────────────────────────────

    def save_knowledge_entry(
        self,
        *,
        tenant_id: str,
        category: str,
        content: str,
        source_type: str = "conversation",
        source_conversation_id: str = "",
        status: str = "active",
        confidence_score: float = 1.0,
        supersedes_entry_id: str = "",
    ) -> None:
        entry = KnowledgeEntryTable(
            tenant_id=tenant_id,
            category=category,
            content=content,
            source_type=source_type,
            source_conversation_id=source_conversation_id,
            status=status,
            confidence_score=confidence_score,
            supersedes_entry_id=supersedes_entry_id,
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()

    def list_knowledge_entries(self, tenant_id: str, *, limit: int = 20) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .order_by(KnowledgeEntryTable.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def get_knowledge_last_updated_at(self, tenant_id: str):
        with Session(self._engine) as session:
            stmt = (
                select(KnowledgeEntryTable)
                .where(KnowledgeEntryTable.tenant_id == tenant_id)
                .order_by(KnowledgeEntryTable.created_at.desc())
            )
            entry = session.exec(stmt).first()
            return entry.created_at if entry is not None else None

    # ── Learning / Briefs ───────────────────────────────────────────────────

    def save_preference_memory(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        diff_notes: str,
        run_id: str = "",
    ) -> PreferenceMemoryTable:
        entry = PreferenceMemoryTable(
            tenant_id=tenant_id,
            platform=platform,
            original_draft=original_draft,
            edited_draft=edited_draft,
            diff_notes=diff_notes,
            run_id=run_id,
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_preference_memories(
        self,
        tenant_id: str,
        *,
        platform: str,
        limit: int = 3,
    ) -> list[PreferenceMemoryTable]:
        with Session(self._engine) as session:
            stmt = (
                select(PreferenceMemoryTable)
                .where(PreferenceMemoryTable.tenant_id == tenant_id, PreferenceMemoryTable.platform == platform)
                .order_by(PreferenceMemoryTable.created_at.desc())
                .limit(limit)
            )
            return list(session.exec(stmt).all())

    def save_episodic_memory(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: str,
    ) -> EpisodicMemoryTable:
        entry = EpisodicMemoryTable(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome=outcome,
            context_summary_json=context_summary,
        )
        with Session(self._engine) as session:
            session.add(entry)
            session.commit()
            session.refresh(entry)
            return entry

    def get_episodic_memories(
        self,
        tenant_id: str,
        *,
        workflow_type: str | None = None,
        limit: int = 5,
    ) -> list[EpisodicMemoryTable]:
        with Session(self._engine) as session:
            stmt = select(EpisodicMemoryTable).where(EpisodicMemoryTable.tenant_id == tenant_id)
            if workflow_type:
                stmt = stmt.where(EpisodicMemoryTable.workflow_type == workflow_type)
            stmt = stmt.order_by(EpisodicMemoryTable.created_at.desc()).limit(limit)
            return list(session.exec(stmt).all())

    def save_context_brief(
        self,
        *,
        tenant_id: str,
        brief_type: str,
        content: dict[str, object],
        ttl_hours: int,
    ) -> ContextBriefTable:
        from datetime import timedelta
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(ContextBriefTable).where(
                ContextBriefTable.tenant_id == tenant_id,
                ContextBriefTable.brief_type == brief_type,
            )
            brief = session.exec(stmt).first()
            now = utcnow()
            expires_at = now + timedelta(hours=ttl_hours)
            if brief is None:
                brief = ContextBriefTable(
                    tenant_id=tenant_id,
                    brief_type=brief_type,
                    content_json=json.dumps(content, ensure_ascii=False),
                    expires_at=expires_at,
                    updated_at=now,
                )
            else:
                brief.content_json = json.dumps(content, ensure_ascii=False)
                brief.expires_at = expires_at
                brief.updated_at = now
            session.add(brief)
            session.commit()
            session.refresh(brief)
            return brief

    def get_context_brief(self, tenant_id: str, brief_type: str) -> Optional[ContextBriefTable]:
        with Session(self._engine) as session:
            stmt = select(ContextBriefTable).where(
                ContextBriefTable.tenant_id == tenant_id,
                ContextBriefTable.brief_type == brief_type,
            )
            return session.exec(stmt).first()

    def compute_and_save_approval_profile(self, tenant_id: str) -> ApprovalProfileTable:
        from statistics import median
        from kachu_plus.persistence.tables import utcnow

        with Session(self._engine) as session:
            stmt = select(PendingApprovalTable).where(
                PendingApprovalTable.tenant_id == tenant_id,
                PendingApprovalTable.status.in_(["approved", "rejected", "published"]),
            )
            decisions = list(session.exec(stmt).all())
            total = len(decisions)
            approved = sum(1 for item in decisions if item.status in {"approved", "published"})
            deltas: list[float] = []
            for item in decisions:
                try:
                    draft = json.loads(item.draft_content or "{}")
                    decision_payload = json.loads(item.decision_payload_json or "{}")
                except (TypeError, JSONDecodeError):
                    continue
                original = str(draft.get("post_text") or draft.get("reply_draft") or "")
                edited = str(decision_payload.get("post_text") or decision_payload.get("reply_draft") or original)
                if not original:
                    continue
                deltas.append(abs(len(edited) - len(original)) / max(len(original), 1))

            stmt_profile = select(ApprovalProfileTable).where(ApprovalProfileTable.tenant_id == tenant_id)
            profile = session.exec(stmt_profile).first()
            if profile is None:
                profile = ApprovalProfileTable(tenant_id=tenant_id)
            profile.total_decisions = total
            profile.recent_acceptance_rate = approved / total if total else 0.0
            profile.median_edit_delta = float(median(deltas)) if deltas else 0.0
            profile.updated_at = utcnow()
            session.add(profile)
            session.commit()
            session.refresh(profile)
            return profile

    def get_approval_profile(self, tenant_id: str) -> Optional[ApprovalProfileTable]:
        with Session(self._engine) as session:
            stmt = select(ApprovalProfileTable).where(ApprovalProfileTable.tenant_id == tenant_id)
            return session.exec(stmt).first()

    def get_knowledge_entries(
        self, tenant_id: str, category: str
    ) -> list[KnowledgeEntryTable]:
        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(
                KnowledgeEntryTable.tenant_id == tenant_id,
                KnowledgeEntryTable.category == category,
            )
            return list(session.exec(stmt).all())

    def delete_knowledge_entries_by_category(
        self, tenant_id: str, category: str
    ) -> None:
        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(
                KnowledgeEntryTable.tenant_id == tenant_id,
                KnowledgeEntryTable.category == category,
            )
            for entry in session.exec(stmt).all():
                session.delete(entry)
            session.commit()

    def delete_knowledge_entries_by_source_type(
        self, tenant_id: str, source_type: str
    ) -> None:
        with Session(self._engine) as session:
            stmt = select(KnowledgeEntryTable).where(
                KnowledgeEntryTable.tenant_id == tenant_id,
                KnowledgeEntryTable.source_type == source_type,
            )
            for entry in session.exec(stmt).all():
                session.delete(entry)
            session.commit()
