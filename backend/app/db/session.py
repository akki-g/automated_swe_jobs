from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base

_pool_kwargs = (
    {"pool_size": 10, "max_overflow": 10}
    if settings.database_url.startswith("postgresql")
    else {}
)
engine = create_async_engine(settings.database_url, **_pool_kwargs)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_ensure_web_profile_columns)


def _ensure_web_profile_columns(connection) -> None:
    """Apply the one additive schema expansion introduced by web onboarding.

    This project intentionally uses `create_all` rather than a migration
    framework. `create_all` is sufficient for new databases but does not add
    columns to the repo's existing Phase-1 database, so keep this narrow,
    idempotent compatibility migration beside initialization. Future
    non-additive schema work should introduce a real migration framework.
    """
    inspector = inspect(connection)
    if "users" not in inspector.get_table_names():
        return

    user_columns = {column["name"] for column in inspector.get_columns("users")}
    user_additions = {
        "password_hash": "VARCHAR(255)",
        "profile_completed_at": "TIMESTAMP",
        "email_digest_enabled": "BOOLEAN NOT NULL DEFAULT true",
    }
    for name, definition in user_additions.items():
        if name not in user_columns:
            connection.exec_driver_sql(f"ALTER TABLE users ADD COLUMN {name} {definition}")

    criteria_columns = {column["name"] for column in inspector.get_columns("criteria")}
    criteria_additions = {
        "target_fields": "JSON NOT NULL DEFAULT '[]'",
        "resume_profile": "JSON NOT NULL DEFAULT '{}'",
        "resume_updated_at": "TIMESTAMP",
    }
    for name, definition in criteria_additions.items():
        if name not in criteria_columns:
            connection.exec_driver_sql(f"ALTER TABLE criteria ADD COLUMN {name} {definition}")

    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
