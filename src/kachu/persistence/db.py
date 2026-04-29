from __future__ import annotations

from sqlalchemy import Engine, create_engine
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


def init_db(engine: Engine) -> None:
    SQLModel.metadata.create_all(engine)
