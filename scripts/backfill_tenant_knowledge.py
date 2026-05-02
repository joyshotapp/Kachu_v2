#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections import Counter
from pathlib import Path

from sqlmodel import Session, select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from kachu.config import Settings  # noqa: E402
from kachu.context_brief_manager import ContextBriefManager  # noqa: E402
from kachu.knowledge_capture import KnowledgeCaptureService  # noqa: E402
from kachu.memory import MemoryManager  # noqa: E402
from kachu.persistence import KachuRepository, create_db_engine  # noqa: E402
from kachu.persistence.tables import KnowledgeEntryTable  # noqa: E402

DERIVED_CATEGORIES = ("product", "contact", "style", "offer", "restriction")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backfill structured knowledge facts for one tenant.")
    parser.add_argument("--tenant-id", required=True, help="Target tenant id")
    parser.add_argument(
        "--env-file",
        default="",
        help="Optional env file to load before reading Settings, e.g. .env.prod",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview counts only; do not modify the database",
    )
    return parser.parse_args()


def load_env_file(env_file: str) -> None:
    if not env_file:
        return
    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = ROOT / env_path
    if not env_path.exists():
        raise FileNotFoundError(f"Env file not found: {env_path}")
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def summarize_active_entries(repo: KachuRepository, tenant_id: str) -> dict[str, int]:
    entries = repo.get_active_knowledge_entries(tenant_id)
    return dict(sorted(Counter(entry.category for entry in entries).items()))


def supersede_active_derived_entries(repo: KachuRepository, tenant_id: str) -> dict[str, int]:
    counts = Counter()
    with Session(repo._engine) as session:  # noqa: SLF001
        stmt = (
            select(KnowledgeEntryTable)
            .where(KnowledgeEntryTable.tenant_id == tenant_id)
            .where(KnowledgeEntryTable.status == "active")
        )
        rows = list(session.exec(stmt).all())
        for row in rows:
            if row.category not in DERIVED_CATEGORIES:
                continue
            if not row.source_type.endswith("_derived"):
                continue
            row.status = "superseded"
            session.add(row)
            counts[row.category] += 1
        session.commit()
    return dict(sorted(counts.items()))


async def backfill_tenant_knowledge(tenant_id: str, *, dry_run: bool) -> dict[str, object]:
    settings = Settings()
    engine = create_db_engine(settings.DATABASE_URL)
    repo = KachuRepository(engine)
    memory = MemoryManager(repo, settings)
    brief_manager = ContextBriefManager(repo, memory)
    knowledge = KnowledgeCaptureService(
        repo,
        settings,
        memory_manager=memory,
        context_brief_manager=brief_manager,
    )

    document_entries = [
        entry
        for entry in repo.get_active_knowledge_entries(tenant_id, categories=["document"])
        if entry.content.strip()
    ]
    before_counts = summarize_active_entries(repo, tenant_id)

    if dry_run:
        return {
            "tenant_id": tenant_id,
            "dry_run": True,
            "documents": len(document_entries),
            "before": before_counts,
        }

    superseded_counts = supersede_active_derived_entries(repo, tenant_id)
    for entry in document_entries:
        knowledge._store_derived_document_facts(  # noqa: SLF001
            tenant_id=tenant_id,
            content=entry.content,
            source_type=entry.source_type,
            source_id=entry.id,
        )
    refreshed = await brief_manager.refresh_briefs(tenant_id, reason="knowledge_backfill")
    after_counts = summarize_active_entries(repo, tenant_id)

    return {
        "tenant_id": tenant_id,
        "dry_run": False,
        "documents": len(document_entries),
        "superseded": superseded_counts,
        "before": before_counts,
        "after": after_counts,
        "brand_brief": {
            "brand_name": refreshed.get("brand_brief", {}).get("brand_name", ""),
            "products": refreshed.get("brand_brief", {}).get("products", [])[:5],
            "contact_points": refreshed.get("brand_brief", {}).get("contact_points", [])[:5],
            "offers": refreshed.get("brand_brief", {}).get("offers", [])[:5],
            "restrictions": refreshed.get("brand_brief", {}).get("restrictions", [])[:5],
        },
    }


def main() -> int:
    args = parse_args()
    load_env_file(args.env_file)
    result = asyncio.run(backfill_tenant_knowledge(args.tenant_id, dry_run=args.dry_run))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())