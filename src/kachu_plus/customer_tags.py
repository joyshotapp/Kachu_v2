from __future__ import annotations
import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from sqlmodel import SQLModel

from kachu_plus.persistence.tables import (
    ChannelEntityTable,
    ConversationHandoffLockTable,
    CustomerProfileTable,
    CustomerTagDefinitionTable,
    CustomerTimelineEventTable,
    ProfileLinkTable,
    ProfileMergeAuditTable,
)

router = APIRouter(prefix="/tenants", tags=["customer-tags"])


class TagCreateRequest(SQLModel):
    name: str
    color: Optional[str] = None


class TagUpdateRequest(SQLModel):
    name: Optional[str] = None
    color: Optional[str] = None


class TagAssignRequest(SQLModel):
    tag_id: str


class TagResponse(SQLModel):
    id: str
    name: str
    color: str
    source: str
    is_active: bool
    deleted_at: Optional[str] = None


class ProfileSummaryResponse(SQLModel):
    id: str
    display_name: str
    custom_name: str
    status: str
    opt_out: bool
    interaction_count: int
    sleep_since_days: int
    merged_into_profile_id: str
    created_at: str
    updated_at: str


class ProfileLinkResponse(SQLModel):
    id: str
    channel_entity_id: str
    channel_type: str
    external_user_id: str
    reachability_status: str
    confidence_score: float
    resolution_source: str
    resolution_note: str
    created_at: str
    updated_at: str


class TimelineEventResponse(SQLModel):
    id: str
    activity_type: str
    title: str
    payload_json: str
    created_at: str


class ProfileMergeRequest(SQLModel):
    source_profile_id: str
    target_profile_id: str
    actor_line_id: str = ""
    reason: Optional[str] = None


class ProfileMergeAuditResponse(SQLModel):
    id: str
    source_profile_id: str
    target_profile_id: str
    actor_line_id: str
    reason: str
    summary_json: str
    created_at: str


class HandoffLockRequest(SQLModel):
    actor_line_id: str = ""
    reason: Optional[str] = None


class ProfileRelinkRequest(SQLModel):
    channel_type: str = "line"
    external_user_id: str
    actor_line_id: str = ""
    reason: Optional[str] = None


class HandoffLockResponse(SQLModel):
    id: str
    channel_type: str
    external_user_id: str
    reason: str
    is_active: bool
    locked_by_line_user_id: str
    released_by_line_user_id: str
    locked_at: str
    released_at: Optional[str] = None
    updated_at: str


class ProfileDetailResponse(SQLModel):
    profile: ProfileSummaryResponse
    channel_links: list[ProfileLinkResponse]
    tags: list[TagResponse]
    merge_audits: list[ProfileMergeAuditResponse]
    active_handoff_locks: list[HandoffLockResponse]


class ProfileListItemResponse(SQLModel):
    profile: ProfileSummaryResponse
    link_count: int
    inferred_link_count: int
    low_confidence_link_count: int
    pending_resolution: bool


class ResolutionHistoryEntryResponse(SQLModel):
    entry_type: str
    activity_type: str
    title: str
    payload_json: str
    source_profile_id: str = ""
    target_profile_id: str = ""
    actor_line_id: str = ""
    reason: str = ""
    summary_json: str = "{}"
    created_at: str


def _repo(request: Request) -> Any:
    repository = getattr(request.app.state, "repository", None)
    if repository is None:
        raise HTTPException(status_code=500, detail="repository not initialized")
    return repository


def _tag_response(tag: CustomerTagDefinitionTable) -> TagResponse:
    return TagResponse(
        id=tag.id,
        name=tag.name,
        color=tag.color,
        source=tag.source,
        is_active=tag.is_active,
        deleted_at=tag.deleted_at.isoformat() if tag.deleted_at is not None else None,
    )


def _profile_response(profile: CustomerProfileTable) -> ProfileSummaryResponse:
    return ProfileSummaryResponse(
        id=profile.id,
        display_name=profile.display_name,
        custom_name=profile.custom_name,
        status=profile.status,
        opt_out=profile.opt_out,
        interaction_count=profile.interaction_count,
        sleep_since_days=profile.sleep_since_days,
        merged_into_profile_id=profile.merged_into_profile_id,
        created_at=profile.created_at.isoformat(),
        updated_at=profile.updated_at.isoformat(),
    )


def _profile_link_response(link: ProfileLinkTable, entity: ChannelEntityTable) -> ProfileLinkResponse:
    return ProfileLinkResponse(
        id=link.id,
        channel_entity_id=link.channel_entity_id,
        channel_type=entity.channel_type,
        external_user_id=entity.external_user_id,
        reachability_status=entity.reachability_status,
        confidence_score=link.confidence_score,
        resolution_source=link.resolution_source,
        resolution_note=link.resolution_note,
        created_at=link.created_at.isoformat(),
        updated_at=link.updated_at.isoformat(),
    )


def _timeline_response(event: CustomerTimelineEventTable) -> TimelineEventResponse:
    return TimelineEventResponse(
        id=event.id,
        activity_type=event.activity_type,
        title=event.title,
        payload_json=event.payload_json,
        created_at=event.created_at.isoformat(),
    )


def _merge_audit_response(audit: ProfileMergeAuditTable) -> ProfileMergeAuditResponse:
    return ProfileMergeAuditResponse(
        id=audit.id,
        source_profile_id=audit.source_profile_id,
        target_profile_id=audit.target_profile_id,
        actor_line_id=audit.actor_line_id,
        reason=audit.reason,
        summary_json=audit.summary_json,
        created_at=audit.created_at.isoformat(),
    )


def _handoff_lock_response(lock: ConversationHandoffLockTable) -> HandoffLockResponse:
    return HandoffLockResponse(
        id=lock.id,
        channel_type=lock.channel_type,
        external_user_id=lock.external_user_id,
        reason=lock.reason,
        is_active=lock.is_active,
        locked_by_line_user_id=lock.locked_by_line_user_id,
        released_by_line_user_id=lock.released_by_line_user_id,
        locked_at=lock.locked_at.isoformat(),
        released_at=lock.released_at.isoformat() if lock.released_at is not None else None,
        updated_at=lock.updated_at.isoformat(),
    )


def _resolution_history_from_timeline(event: CustomerTimelineEventTable) -> ResolutionHistoryEntryResponse:
    try:
        payload = json.loads(event.payload_json or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return ResolutionHistoryEntryResponse(
        entry_type="timeline",
        activity_type=event.activity_type,
        title=event.title,
        payload_json=event.payload_json,
        source_profile_id=str(payload.get("source_profile_id", "") or ""),
        target_profile_id=str(payload.get("target_profile_id", "") or ""),
        actor_line_id=str(payload.get("actor_line_id", "") or ""),
        reason=str(payload.get("reason", "") or ""),
        created_at=event.created_at.isoformat(),
    )


def _resolution_history_from_merge_audit(audit: ProfileMergeAuditTable) -> ResolutionHistoryEntryResponse:
    return ResolutionHistoryEntryResponse(
        entry_type="merge_audit",
        activity_type="profile_merge_audit",
        title="合併顧客檔案",
        payload_json="{}",
        source_profile_id=audit.source_profile_id,
        target_profile_id=audit.target_profile_id,
        actor_line_id=audit.actor_line_id,
        reason=audit.reason,
        summary_json=audit.summary_json,
        created_at=audit.created_at.isoformat(),
    )


@router.get("/{tenant_id}/tags", response_model=list[TagResponse])
def list_tags(
    tenant_id: str,
    request: Request,
    include_inactive: bool = Query(default=False),
) -> list[TagResponse]:
    repo = _repo(request)
    tags = repo.list_tags(tenant_id, include_inactive=include_inactive)
    return [_tag_response(tag) for tag in tags]


@router.post("/{tenant_id}/tags", response_model=TagResponse, status_code=status.HTTP_201_CREATED)
def create_tag(tenant_id: str, payload: TagCreateRequest, request: Request) -> TagResponse:
    repo = _repo(request)
    try:
        tag = repo.create_tag(tenant_id, name=payload.name, color=payload.color)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _tag_response(tag)


@router.patch("/{tenant_id}/tags/{tag_id}", response_model=TagResponse)
def update_tag(
    tenant_id: str,
    tag_id: str,
    payload: TagUpdateRequest,
    request: Request,
) -> TagResponse:
    repo = _repo(request)
    try:
        tag = repo.update_tag(tenant_id, tag_id, name=payload.name, color=payload.color)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _tag_response(tag)


@router.delete("/{tenant_id}/tags/{tag_id}", response_model=TagResponse)
def delete_tag(tenant_id: str, tag_id: str, request: Request) -> TagResponse:
    repo = _repo(request)
    try:
        tag = repo.deactivate_tag(tenant_id, tag_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return _tag_response(tag)


@router.get("/{tenant_id}/profiles", response_model=list[ProfileListItemResponse])
def list_profiles(
    tenant_id: str,
    request: Request,
    include_merged: bool = Query(default=False),
    pending_resolution_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ProfileListItemResponse]:
    repo = _repo(request)
    profiles = sorted(
        repo.list_customer_profiles_for_tenant(tenant_id),
        key=lambda profile: profile.updated_at,
        reverse=True,
    )
    items: list[ProfileListItemResponse] = []
    for profile in profiles:
        is_merged = bool(str(profile.merged_into_profile_id or "").strip()) or str(profile.status or "") == "merged"
        if is_merged and not include_merged:
            continue

        channel_links = repo.list_profile_channel_links(tenant_id, profile.id)
        inferred_link_count = sum(1 for link, _ in channel_links if str(link.resolution_source or "") == "inferred")
        low_confidence_link_count = sum(1 for link, _ in channel_links if float(link.confidence_score or 0.0) < 1.0)
        # 僅有單一 inferred link 是正常的自動建立狀態，不需要人工覆核。
        # 只有在「多個 channel identity 共存，且其中有 inferred」或「low confidence」
        # 時，才代表身份解析可能需要注意。
        pending_resolution = (inferred_link_count > 0 and len(channel_links) > 1) or low_confidence_link_count > 0
        if pending_resolution_only and not pending_resolution:
            continue

        items.append(
            ProfileListItemResponse(
                profile=_profile_response(profile),
                link_count=len(channel_links),
                inferred_link_count=inferred_link_count,
                low_confidence_link_count=low_confidence_link_count,
                pending_resolution=pending_resolution,
            )
        )
        if len(items) >= limit:
            break
    return items


@router.get("/{tenant_id}/profiles/{profile_id}/tags", response_model=list[TagResponse])
def list_profile_tags(tenant_id: str, profile_id: str, request: Request) -> list[TagResponse]:
    repo = _repo(request)
    tags = repo.list_profile_active_tags(tenant_id, profile_id)
    return [_tag_response(tag) for tag in tags]


@router.get("/{tenant_id}/profiles/{profile_id}", response_model=ProfileDetailResponse)
def get_profile_detail(tenant_id: str, profile_id: str, request: Request) -> ProfileDetailResponse:
    repo = _repo(request)
    profile = repo.get_customer_profile(profile_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="profile not found")

    channel_links = repo.list_profile_channel_links(tenant_id, profile_id)
    active_handoff_locks: list[HandoffLockResponse] = []
    for _, entity in channel_links:
        if str(entity.channel_type or "") != "line":
            continue
        lock = repo.get_active_conversation_handoff_lock(
            tenant_id=tenant_id,
            channel_type=entity.channel_type,
            external_user_id=entity.external_user_id,
        )
        if lock is not None:
            active_handoff_locks.append(_handoff_lock_response(lock))

    return ProfileDetailResponse(
        profile=_profile_response(profile),
        channel_links=[_profile_link_response(link, entity) for link, entity in channel_links],
        tags=[_tag_response(tag) for tag in repo.list_profile_active_tags(tenant_id, profile_id)],
        merge_audits=[_merge_audit_response(audit) for audit in repo.list_profile_merge_audits(tenant_id, profile_id)],
        active_handoff_locks=active_handoff_locks,
    )


@router.post("/{tenant_id}/profiles/{profile_id}/relink", response_model=ProfileLinkResponse)
def relink_profile_channel_entity(
    tenant_id: str,
    profile_id: str,
    payload: ProfileRelinkRequest,
    request: Request,
) -> ProfileLinkResponse:
    repo = _repo(request)
    try:
        link, entity = repo.relink_profile_channel_entity(
            tenant_id=tenant_id,
            target_profile_id=profile_id,
            channel_type=payload.channel_type,
            external_user_id=payload.external_user_id,
            actor_line_id=payload.actor_line_id,
            reason=payload.reason or "",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _profile_link_response(link, entity)


@router.post("/{tenant_id}/profiles/{profile_id}/tags", status_code=status.HTTP_204_NO_CONTENT)
def assign_profile_tag(
    tenant_id: str,
    profile_id: str,
    payload: TagAssignRequest,
    request: Request,
) -> Response:
    repo = _repo(request)
    try:
        repo.assign_tag_to_profile(tenant_id, profile_id, payload.tag_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{tenant_id}/profiles/{profile_id}/tags/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_profile_tag(
    tenant_id: str,
    profile_id: str,
    tag_id: str,
    request: Request,
) -> Response:
    repo = _repo(request)
    try:
        repo.remove_tag_from_profile(tenant_id, profile_id, tag_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{tenant_id}/profiles/{profile_id}/timeline", response_model=list[TimelineEventResponse])
def list_profile_timeline(tenant_id: str, profile_id: str, request: Request) -> list[TimelineEventResponse]:
    repo = _repo(request)
    events = repo.list_profile_timeline_events(tenant_id, profile_id)
    return [_timeline_response(event) for event in events]


@router.get("/{tenant_id}/profiles/{profile_id}/resolution-history", response_model=list[ResolutionHistoryEntryResponse])
def list_profile_resolution_history(tenant_id: str, profile_id: str, request: Request) -> list[ResolutionHistoryEntryResponse]:
    repo = _repo(request)
    profile = repo.get_customer_profile(profile_id)
    if profile is None or profile.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="profile not found")

    timeline_entries = [
        _resolution_history_from_timeline(event)
        for event in repo.list_profile_timeline_events(tenant_id, profile_id)
        if event.activity_type in {"profile_merged", "profile_link_relinked", "profile_link_moved_out"}
    ]
    merge_entries = [
        _resolution_history_from_merge_audit(audit)
        for audit in repo.list_profile_merge_audits(tenant_id, profile_id)
    ]
    combined = timeline_entries + merge_entries
    combined.sort(key=lambda item: item.created_at, reverse=True)
    return combined


@router.post("/{tenant_id}/profiles/merge", response_model=ProfileMergeAuditResponse, status_code=status.HTTP_201_CREATED)
def merge_profiles(tenant_id: str, payload: ProfileMergeRequest, request: Request) -> ProfileMergeAuditResponse:
    repo = _repo(request)
    try:
        audit = repo.merge_customer_profiles(
            tenant_id=tenant_id,
            source_profile_id=payload.source_profile_id,
            target_profile_id=payload.target_profile_id,
            actor_line_id=payload.actor_line_id,
            reason=payload.reason or "",
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _merge_audit_response(audit)


@router.get("/{tenant_id}/profiles/{profile_id}/merge-audits", response_model=list[ProfileMergeAuditResponse])
def list_profile_merge_audits(tenant_id: str, profile_id: str, request: Request) -> list[ProfileMergeAuditResponse]:
    repo = _repo(request)
    audits = repo.list_profile_merge_audits(tenant_id, profile_id)
    return [_merge_audit_response(audit) for audit in audits]


@router.get("/{tenant_id}/customer-handoff/line/{line_user_id}/lock", response_model=HandoffLockResponse)
def get_line_handoff_lock(tenant_id: str, line_user_id: str, request: Request) -> HandoffLockResponse:
    repo = _repo(request)
    lock = repo.get_active_conversation_handoff_lock(
        tenant_id=tenant_id,
        channel_type="line",
        external_user_id=line_user_id,
    )
    if lock is None:
        raise HTTPException(status_code=404, detail="handoff lock not found")
    return _handoff_lock_response(lock)


@router.post("/{tenant_id}/customer-handoff/line/{line_user_id}/lock", response_model=HandoffLockResponse)
def lock_line_handoff(tenant_id: str, line_user_id: str, payload: HandoffLockRequest, request: Request) -> HandoffLockResponse:
    repo = _repo(request)
    try:
        lock = repo.upsert_conversation_handoff_lock(
            tenant_id=tenant_id,
            channel_type="line",
            external_user_id=line_user_id,
            locked_by_line_user_id=payload.actor_line_id,
            reason=payload.reason or "human_handoff",
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _handoff_lock_response(lock)


@router.delete("/{tenant_id}/customer-handoff/line/{line_user_id}/lock", response_model=HandoffLockResponse)
def unlock_line_handoff(
    tenant_id: str,
    line_user_id: str,
    request: Request,
    actor_line_id: str = Query(default=""),
) -> HandoffLockResponse:
    repo = _repo(request)
    lock = repo.release_conversation_handoff_lock(
        tenant_id=tenant_id,
        channel_type="line",
        external_user_id=line_user_id,
        released_by_line_user_id=actor_line_id,
    )
    if lock is None:
        raise HTTPException(status_code=404, detail="handoff lock not found")
    return _handoff_lock_response(lock)