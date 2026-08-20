import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base
from app.ingest.normalize import build_posting_key

logger = logging.getLogger(__name__)

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
        "profile_completed_at": "TIMESTAMP WITH TIME ZONE",
        "initial_matches_generated_at": "TIMESTAMP WITH TIME ZONE",
        "email_digest_enabled": "BOOLEAN NOT NULL DEFAULT true",
        "email_digest_time": "VARCHAR(5) NOT NULL DEFAULT '08:00'",
        "last_email_digest_sent_on": "DATE",
        "matches_last_viewed_at": "TIMESTAMP WITH TIME ZONE",
        "matches_visit_started_at": "TIMESTAMP WITH TIME ZONE",
    }
    for name, definition in user_additions.items():
        if name not in user_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE users ADD COLUMN {name} {definition}"
            )

    criteria_columns = {column["name"] for column in inspector.get_columns("criteria")}
    criteria_additions = {
        "target_fields": "JSON NOT NULL DEFAULT '[]'",
        "resume_profile": "JSON NOT NULL DEFAULT '{}'",
        "resume_updated_at": "TIMESTAMP WITH TIME ZONE",
    }
    for name, definition in criteria_additions.items():
        if name not in criteria_columns:
            connection.exec_driver_sql(
                f"ALTER TABLE criteria ADD COLUMN {name} {definition}"
            )

    if "matches" in inspector.get_table_names():
        match_columns = {column["name"] for column in inspector.get_columns("matches")}
        match_additions = {
            "matched_target_field": "VARCHAR(50)",
            "saved": "BOOLEAN NOT NULL DEFAULT false",
        }
        for name, definition in match_additions.items():
            if name not in match_columns:
                connection.exec_driver_sql(
                    f"ALTER TABLE matches ADD COLUMN {name} {definition}"
                )

    connection.exec_driver_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email ON users (email)"
    )

    if connection.dialect.name == "postgresql":
        # Corrective step for databases where the three TIMESTAMP columns
        # above were already created without a time zone (every deployment
        # before this fix — see the ADD COLUMN definitions' history): a bare
        # Postgres TIMESTAMP round-trips as a *naive* Python datetime via
        # asyncpg, while every SQLAlchemy model here declares
        # DateTime(timezone=True) and every write uses datetime.now(UTC) —
        # comparing one of these against another aware datetime in Python
        # (e.g. app/api/matches.py's is_new computation) raises
        # "can't compare offset-naive and offset-aware datetimes". SQLite
        # never surfaces this because it has no real column-type enforcement
        # (round-tripping there is governed by SQLAlchemy's Python-side type
        # decorator, not the DDL text), which is why this only broke in
        # production. `USING col AT TIME ZONE 'UTC'` reinterprets the
        # existing naive values as UTC (correct, since every writer already
        # used UTC) rather than silently shifting them.
        #
        # The type check below is what makes this safe to run on every boot,
        # and it is load-bearing rather than an optimization. Re-running the
        # ALTER against a column that is *already* TIMESTAMPTZ is not a
        # no-op: `timestamptz AT TIME ZONE 'UTC'` yields a naive timestamp,
        # which the ALTER then re-interprets in the session's TimeZone, so
        # every restart shifts every value by the server's UTC offset
        # (verified against PostgreSQL 16: with timezone='America/New_York'
        # a 12:00Z value walked to 16:00Z, 20:00Z, 00:00Z on successive
        # boots). It also forces a full table rewrite under an ACCESS
        # EXCLUSIVE lock each time, even where the values happen to survive.
        for table, column in (
            ("users", "profile_completed_at"),
            ("users", "initial_matches_generated_at"),
            ("users", "matches_last_viewed_at"),
            ("users", "matches_visit_started_at"),
            ("criteria", "resume_updated_at"),
        ):
            already_aware = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = :table AND column_name = :column "
                    "AND data_type = 'timestamp with time zone'"
                ),
                {"table": table, "column": column},
            ).first()
            if already_aware:
                continue
            connection.exec_driver_sql(
                f"ALTER TABLE {table} ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE "
                f"USING {column} AT TIME ZONE 'UTC'"
            )

        # Corrective widening for an already-deployed postings.posting_key
        # VARCHAR(400): real-world data (e.g. a Workday posting listing a
        # dozen+ office locations) can build a posting_key past 400 chars,
        # and that INSERT failure (an uncaught DataError, not an
        # IntegrityError) took down the *entire* batch insert for that
        # cycle — see ingest/normalize.py's per-component length cap for
        # the actual fix; this just brings existing databases up to the
        # new column width. Guarded on the current width so an already-wide
        # column doesn't take a needless ACCESS EXCLUSIVE lock every boot.
        if "postings" in inspector.get_table_names():
            too_narrow = connection.execute(
                text(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'postings' AND column_name = 'posting_key' "
                    "AND character_maximum_length < 500"
                )
            ).first()
            if too_narrow:
                connection.exec_driver_sql(
                    "ALTER TABLE postings ALTER COLUMN posting_key TYPE VARCHAR(500)"
                )

    _backfill_truncated_posting_keys(connection, inspector)


def _backfill_truncated_posting_keys(connection, inspector) -> None:
    """Re-key postings stored before ingest/normalize.py capped each
    posting_key component at 150 characters.

    Those rows were written with the *uncapped* key, so the same posting
    scraped today hashes to a different (shorter) key, `filter_new` no
    longer recognises it, and it gets re-inserted as a brand-new posting —
    re-matched and re-notified to every user who already saw it. Rewriting
    the stored key to what the current normalizer produces closes that gap
    once, in place.

    Only rows with an over-long component are even considered, so this is a
    no-op scan on a healthy database. A recomputed key that already belongs
    to another row is left alone rather than merged: the duplicate is
    harmless, whereas deleting either row would orphan the matches that
    reference it.
    """
    if "postings" not in inspector.get_table_names():
        return
    # This helper's whole reason for existing is databases whose schema
    # predates the current models, so don't assume the table already has
    # every column the recomputation reads.
    required = {"id", "company", "title", "location", "posting_key"}
    if not required <= {column["name"] for column in inspector.get_columns("postings")}:
        return

    candidates = connection.execute(
        text(
            "SELECT id, company, title, location, posting_key FROM postings "
            "WHERE length(company) > 150 OR length(title) > 150 OR length(location) > 150"
        )
    ).all()
    if not candidates:
        return

    existing_keys = {
        row[0]
        for row in connection.execute(text("SELECT posting_key FROM postings")).all()
    }
    rekeyed = 0
    for posting_id, company, title, location, current_key in candidates:
        new_key = build_posting_key(company, title, location)
        if new_key == current_key or new_key in existing_keys:
            continue
        connection.execute(
            text("UPDATE postings SET posting_key = :key WHERE id = :id"),
            {"key": new_key, "id": posting_id},
        )
        existing_keys.discard(current_key)
        existing_keys.add(new_key)
        rekeyed += 1
    if rekeyed:
        logger.info(
            "posting_key backfill: re-keyed %d posting(s) to the capped-component form",
            rekeyed,
        )


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session
