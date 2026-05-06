from __future__ import annotations

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlmodel import SQLModel

from .tables import PendingApprovalTable, TenantTable, WorkflowRecordTable  # noqa: F401 — ensure models registered


def create_db_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        from sqlalchemy.pool import StaticPool
        return create_engine(
            database_url,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
            echo=False,
        )
    return create_engine(database_url, echo=False)


def _project_root() -> Path:
    candidates: list[Path] = [Path.cwd().resolve()]
    candidates.extend(Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "alembic.ini").exists() and (candidate / "alembic").exists():
            return candidate
    raise RuntimeError("Could not locate Alembic configuration; run migrations before startup.")


def _alembic_head_revisions() -> set[str]:
    project_root = _project_root()
    alembic_ini = project_root / "alembic.ini"
    script_location = project_root / "alembic"

    config = Config(str(alembic_ini))
    config.set_main_option("script_location", str(script_location))
    script = ScriptDirectory.from_config(config)
    return {revision for revision in script.get_heads() if revision}


def assert_schema_migrated(engine: Engine) -> None:
    inspector = inspect(engine)
    if "alembic_version" not in inspector.get_table_names():
        raise RuntimeError(
            "Database schema is not ready for production startup; alembic_version is missing. Run migrations before startup."
        )

    with engine.connect() as connection:
        applied_revisions = {
            str(row[0])
            for row in connection.execute(text("select version_num from alembic_version"))
            if row[0]
        }

    if not applied_revisions:
        raise RuntimeError(
            "Database schema is not ready for production startup; no Alembic revisions are recorded. Run migrations before startup."
        )

    head_revisions = _alembic_head_revisions()
    if not head_revisions.issubset(applied_revisions):
        raise RuntimeError(
            "Database schema is not ready for production startup; migrations are behind head. Run migrations before startup."
        )


def init_db(engine: Engine, *, create_schema: bool = True) -> None:
    if create_schema:
        SQLModel.metadata.create_all(engine)
