from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.customer_tags import router
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import CustomerProfileTable, TenantTable


def _make_app(repo: KachuPlusRepository) -> FastAPI:
    app = FastAPI()
    app.state.repository = repo
    app.include_router(router)
    return app


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_profile(repo: KachuPlusRepository, tenant_id: str = "tenant-1") -> str:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id=tenant_id, name="店家"))
        profile = CustomerProfileTable(id="profile-1", tenant_id=tenant_id, display_name="王小美")
        session.add(profile)
        session.commit()
    return "profile-1"


def test_tag_crud_and_soft_delete() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_profile(repo)
    client = TestClient(_make_app(repo))

    created = client.post("/tenants/tenant-1/tags", json={"name": "VIP", "color": "#ff9900"})
    assert created.status_code == 201
    tag_id = created.json()["id"]

    listed = client.get("/tenants/tenant-1/tags")
    assert listed.status_code == 200
    assert [tag["name"] for tag in listed.json()] == ["VIP"]

    updated = client.patch(f"/tenants/tenant-1/tags/{tag_id}", json={"name": "高價值客", "color": "#cc6600"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "高價值客"

    deleted = client.delete(f"/tenants/tenant-1/tags/{tag_id}")
    assert deleted.status_code == 200
    assert deleted.json()["is_active"] is False

    active_tags = client.get("/tenants/tenant-1/tags")
    assert active_tags.status_code == 200
    assert active_tags.json() == []

    all_tags = client.get("/tenants/tenant-1/tags", params={"include_inactive": True})
    assert all_tags.status_code == 200
    assert all_tags.json()[0]["name"] == "高價值客"
    assert all_tags.json()[0]["is_active"] is False


def test_profile_tag_assignment_and_timeline_survive_tag_deletion() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    profile_id = _seed_profile(repo)
    client = TestClient(_make_app(repo))

    created = client.post("/tenants/tenant-1/tags", json={"name": "VIP", "color": "#ff9900"})
    tag_id = created.json()["id"]

    assigned = client.post(
        f"/tenants/tenant-1/profiles/{profile_id}/tags",
        json={"tag_id": tag_id},
    )
    assert assigned.status_code == 204

    profile_tags = client.get(f"/tenants/tenant-1/profiles/{profile_id}/tags")
    assert profile_tags.status_code == 200
    assert [tag["name"] for tag in profile_tags.json()] == ["VIP"]

    deleted = client.delete(f"/tenants/tenant-1/tags/{tag_id}")
    assert deleted.status_code == 200

    profile_tags_after_delete = client.get(f"/tenants/tenant-1/profiles/{profile_id}/tags")
    assert profile_tags_after_delete.status_code == 200
    assert profile_tags_after_delete.json() == []

    timeline = client.get(f"/tenants/tenant-1/profiles/{profile_id}/timeline")
    assert timeline.status_code == 200
    assert len(timeline.json()) == 1
    assert timeline.json()[0]["activity_type"] == "tag_assigned"
    assert "VIP" in timeline.json()[0]["title"]
    assert "VIP" in timeline.json()[0]["payload_json"]

    assign_inactive = client.post(
        f"/tenants/tenant-1/profiles/{profile_id}/tags",
        json={"tag_id": tag_id},
    )
    assert assign_inactive.status_code == 409


def test_remove_profile_tag_adds_timeline_event() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    profile_id = _seed_profile(repo)
    client = TestClient(_make_app(repo))

    created = client.post("/tenants/tenant-1/tags", json={"name": "回購提醒"})
    tag_id = created.json()["id"]

    assert client.post(
        f"/tenants/tenant-1/profiles/{profile_id}/tags",
        json={"tag_id": tag_id},
    ).status_code == 204

    removed = client.delete(f"/tenants/tenant-1/profiles/{profile_id}/tags/{tag_id}")
    assert removed.status_code == 204

    profile_tags = client.get(f"/tenants/tenant-1/profiles/{profile_id}/tags")
    assert profile_tags.status_code == 200
    assert profile_tags.json() == []

    timeline = client.get(f"/tenants/tenant-1/profiles/{profile_id}/timeline")
    assert timeline.status_code == 200
    assert [event["activity_type"] for event in timeline.json()] == ["tag_removed", "tag_assigned"]
    assert "回購提醒" in timeline.json()[0]["payload_json"]


def test_profile_merge_moves_timeline_tags_and_identity_with_audit() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    source = repo.resolve_or_create_line_profile("tenant-1", "U-source")
    target = repo.resolve_or_create_line_profile("tenant-1", "U-target")
    repo.create_tag("tenant-1", name="VIP")
    tag = repo.list_tags("tenant-1")[0]
    repo.assign_tag_to_profile("tenant-1", source.id, tag.id)
    client = TestClient(_make_app(repo))

    merged = client.post(
        "/tenants/tenant-1/profiles/merge",
        json={
            "source_profile_id": source.id,
            "target_profile_id": target.id,
            "actor_line_id": "U-owner-1",
            "reason": "duplicate import",
        },
    )

    assert merged.status_code == 201
    summary = json.loads(merged.json()["summary_json"])
    assert summary["moved_links"] == 1
    assert summary["attached_active_tags"] == 1
    merged_source = repo.get_customer_profile(source.id)
    assert merged_source is not None
    assert merged_source.status == "merged"
    assert merged_source.merged_into_profile_id == target.id
    assert sorted(repo.get_profile_line_user_ids("tenant-1", [target.id])) == ["U-source", "U-target"]
    assert repo.get_profile_line_user_ids("tenant-1", [source.id]) == []

    target_tags = client.get(f"/tenants/tenant-1/profiles/{target.id}/tags")
    assert target_tags.status_code == 200
    assert [tag["name"] for tag in target_tags.json()] == ["VIP"]

    timeline = client.get(f"/tenants/tenant-1/profiles/{target.id}/timeline")
    assert timeline.status_code == 200
    assert [event["activity_type"] for event in timeline.json()][:2] == ["profile_merged", "tag_assigned"]

    audits = client.get(f"/tenants/tenant-1/profiles/{target.id}/merge-audits")
    assert audits.status_code == 200
    assert audits.json()[0]["source_profile_id"] == source.id


def test_profile_relink_moves_channel_identity_with_timeline_audit() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    source = repo.resolve_or_create_line_profile("tenant-1", "U-source")
    target = repo.resolve_or_create_line_profile("tenant-1", "U-target")
    client = TestClient(_make_app(repo))

    relinked = client.post(
        f"/tenants/tenant-1/profiles/{target.id}/relink",
        json={
            "channel_type": "line",
            "external_user_id": "U-source",
            "actor_line_id": "U-owner-1",
            "reason": "manual dedupe",
        },
    )

    assert relinked.status_code == 200
    assert relinked.json()["external_user_id"] == "U-source"
    assert relinked.json()["resolution_source"] == "manual_relink"
    assert sorted(repo.get_profile_line_user_ids("tenant-1", [target.id])) == ["U-source", "U-target"]
    assert repo.get_profile_line_user_ids("tenant-1", [source.id]) == []

    source_timeline = client.get(f"/tenants/tenant-1/profiles/{source.id}/timeline")
    target_timeline = client.get(f"/tenants/tenant-1/profiles/{target.id}/timeline")
    assert source_timeline.status_code == 200
    assert target_timeline.status_code == 200
    assert source_timeline.json()[0]["activity_type"] == "profile_link_moved_out"
    assert target_timeline.json()[0]["activity_type"] == "profile_link_relinked"


def test_profile_resolution_history_aggregates_merge_and_relink_entries() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    source = repo.resolve_or_create_line_profile("tenant-1", "U-source")
    target = repo.resolve_or_create_line_profile("tenant-1", "U-target")
    third = repo.resolve_or_create_line_profile("tenant-1", "U-third")
    client = TestClient(_make_app(repo))

    merge = client.post(
        "/tenants/tenant-1/profiles/merge",
        json={
            "source_profile_id": source.id,
            "target_profile_id": target.id,
            "actor_line_id": "U-owner-1",
            "reason": "duplicate import",
        },
    )
    assert merge.status_code == 201

    relink = client.post(
        f"/tenants/tenant-1/profiles/{target.id}/relink",
        json={
            "channel_type": "line",
            "external_user_id": "U-third",
            "actor_line_id": "U-owner-2",
            "reason": "manual dedupe",
        },
    )
    assert relink.status_code == 200

    history = client.get(f"/tenants/tenant-1/profiles/{target.id}/resolution-history")
    assert history.status_code == 200
    entries = history.json()
    assert [entry["entry_type"] for entry in entries[:2]] == ["timeline", "merge_audit"]
    assert entries[0]["activity_type"] == "profile_link_relinked"
    assert entries[0]["actor_line_id"] == "U-owner-2"
    assert entries[1]["activity_type"] == "profile_merge_audit"
    assert entries[1]["source_profile_id"] == source.id


def test_profile_detail_aggregates_links_tags_audits_and_handoff_lock() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    source = repo.resolve_or_create_line_profile("tenant-1", "U-source")
    target = repo.resolve_or_create_line_profile("tenant-1", "U-target")
    repo.create_tag("tenant-1", name="VIP")
    tag = repo.list_tags("tenant-1")[0]
    repo.assign_tag_to_profile("tenant-1", source.id, tag.id)
    repo.upsert_conversation_handoff_lock(
        tenant_id="tenant-1",
        channel_type="line",
        external_user_id="U-target",
        locked_by_line_user_id="U-owner-1",
        reason="人工接手中",
    )
    repo.merge_customer_profiles(
        tenant_id="tenant-1",
        source_profile_id=source.id,
        target_profile_id=target.id,
        actor_line_id="U-owner-1",
        reason="duplicate import",
    )

    client = TestClient(_make_app(repo))
    detail = client.get(f"/tenants/tenant-1/profiles/{target.id}")

    assert detail.status_code == 200
    payload = detail.json()
    assert payload["profile"]["id"] == target.id
    assert payload["profile"]["status"] == "active"
    assert sorted(link["external_user_id"] for link in payload["channel_links"]) == ["U-source", "U-target"]
    assert [tag["name"] for tag in payload["tags"]] == ["VIP"]
    assert payload["merge_audits"][0]["source_profile_id"] == source.id
    assert payload["active_handoff_locks"][0]["external_user_id"] == "U-target"


def test_profile_list_can_filter_pending_resolution_and_hide_merged_by_default() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    inferred_profile = repo.resolve_or_create_line_profile("tenant-1", "U-inferred")
    plain_profile = _seed_profile(repo, tenant_id="tenant-2")
    with Session(repo._engine) as session:  # noqa: SLF001
        profile = session.get(CustomerProfileTable, plain_profile)
        assert profile is not None
        profile.tenant_id = "tenant-1"
        session.add(profile)
        session.commit()

    merge_source = repo.resolve_or_create_line_profile("tenant-1", "U-merge-source")
    merge_target = repo.resolve_or_create_line_profile("tenant-1", "U-merge-target")
    repo.merge_customer_profiles(
        tenant_id="tenant-1",
        source_profile_id=merge_source.id,
        target_profile_id=merge_target.id,
        actor_line_id="U-owner-1",
        reason="duplicate import",
    )
    client = TestClient(_make_app(repo))

    listed = client.get("/tenants/tenant-1/profiles")
    assert listed.status_code == 200
    payload = listed.json()
    item_by_profile_id = {item["profile"]["id"]: item for item in payload}
    assert set(item_by_profile_id) == {merge_target.id, inferred_profile.id, plain_profile}
    # merge_target 合併後持有 2 個 inferred link（自己原本的 + 被合入的），需人工確認
    assert item_by_profile_id[merge_target.id]["pending_resolution"] is True
    assert item_by_profile_id[merge_target.id]["inferred_link_count"] == 2
    # inferred_profile 只有 1 個 inferred link，是正常自動建立狀態，不應觸發 pending
    assert item_by_profile_id[inferred_profile.id]["pending_resolution"] is False
    assert item_by_profile_id[inferred_profile.id]["inferred_link_count"] == 1
    assert item_by_profile_id[plain_profile]["pending_resolution"] is False

    pending_only = client.get("/tenants/tenant-1/profiles", params={"pending_resolution_only": True})
    assert pending_only.status_code == 200
    assert {item["profile"]["id"] for item in pending_only.json()} == {merge_target.id}

    include_merged = client.get("/tenants/tenant-1/profiles", params={"include_merged": True})
    assert include_merged.status_code == 200
    assert merge_source.id in [item["profile"]["id"] for item in include_merged.json()]


def test_line_handoff_lock_crud() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_profile(repo)
    client = TestClient(_make_app(repo))

    locked = client.post(
        "/tenants/tenant-1/customer-handoff/line/U-customer-1/lock",
        json={"actor_line_id": "U-owner-1", "reason": "人工接手中"},
    )
    assert locked.status_code == 200
    assert locked.json()["is_active"] is True
    assert locked.json()["reason"] == "人工接手中"

    fetched = client.get("/tenants/tenant-1/customer-handoff/line/U-customer-1/lock")
    assert fetched.status_code == 200
    assert fetched.json()["locked_by_line_user_id"] == "U-owner-1"

    unlocked = client.delete(
        "/tenants/tenant-1/customer-handoff/line/U-customer-1/lock",
        params={"actor_line_id": "U-owner-2"},
    )
    assert unlocked.status_code == 200
    assert unlocked.json()["is_active"] is False
    assert unlocked.json()["released_by_line_user_id"] == "U-owner-2"

    missing = client.get("/tenants/tenant-1/customer-handoff/line/U-customer-1/lock")
    assert missing.status_code == 404