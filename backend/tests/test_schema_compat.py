from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.db.models import Base
from app.db.session import _ensure_web_profile_columns
from app.ingest.normalize import build_posting_key


def test_web_profile_schema_upgrade_is_additive_and_idempotent():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE users (
                id INTEGER PRIMARY KEY,
                name VARCHAR(120) NOT NULL,
                phone VARCHAR(20) NOT NULL,
                email VARCHAR(255),
                sms_provider VARCHAR(20) NOT NULL,
                opted_out BOOLEAN NOT NULL,
                consent_at DATETIME,
                consent_method VARCHAR(50) NOT NULL,
                created_at DATETIME NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE criteria (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL UNIQUE,
                role_types JSON NOT NULL,
                keywords JSON NOT NULL,
                locations JSON NOT NULL,
                sponsorship_required BOOLEAN,
                min_date DATETIME,
                freeform_notes VARCHAR(2000) NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
        )

        _ensure_web_profile_columns(connection)
        _ensure_web_profile_columns(connection)

        schema = inspect(connection)
        user_columns = {column["name"] for column in schema.get_columns("users")}
        criteria_columns = {column["name"] for column in schema.get_columns("criteria")}
        assert {
            "password_hash",
            "profile_completed_at",
            "initial_matches_generated_at",
            "initial_match_backfill_version",
            "initial_match_backfill_attempts",
            "initial_match_backfill_cursor",
            "initial_match_backfill_recent_seeded",
            "initial_match_backfill_last_attempted_at",
            "email_digest_enabled",
            "email_digest_time",
            "last_email_digest_sent_on",
        } <= user_columns
        assert {
            "target_fields",
            "resume_profile",
            "resume_updated_at",
        } <= criteria_columns


@pytest.mark.asyncio
async def test_backfill_rekeys_postings_stored_before_the_component_cap():
    """A posting written before ingest/normalize.py capped each posting_key
    component at 150 chars carries the uncapped key. Scraped again today it
    hashes shorter, filter_new no longer recognises it, and it is
    re-inserted as a brand-new posting — re-matched and re-notified to
    everyone who already saw it. Startup should re-key it in place.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    long_title = "Software Engineer New Grad 2027 " + (
        "Distributed Systems and Platform Infrastructure " * 4
    )
    legacy_key = f"acme|{' '.join(long_title.split()).lower()}|remote"
    assert len(legacy_key) > 150

    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO postings (posting_key, source, company, title, url, location, raw, "
                "status, first_seen_at, last_seen_at) VALUES (:key, 'test', 'Acme', :title, "
                "'https://example.com/job', 'Remote', '{}', 'open', :now, :now)"
            ),
            {
                "key": legacy_key,
                "title": long_title,
                "now": datetime.now(UTC).isoformat(" "),
            },
        )

    async with engine.begin() as connection:
        await connection.run_sync(_ensure_web_profile_columns)

    async with engine.connect() as connection:
        stored = (
            await connection.execute(text("SELECT posting_key FROM postings"))
        ).scalar_one()

    assert stored == build_posting_key("Acme", long_title, "Remote")
    assert stored != legacy_key
    await engine.dispose()


@pytest.mark.asyncio
async def test_backfill_leaves_a_row_alone_when_the_new_key_is_taken():
    """If the capped key already belongs to another row, re-keying would
    violate the unique constraint. Leave the legacy row as a harmless
    duplicate rather than deleting either one and orphaning matches."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    long_title = "Staff Engineer " + ("Platform Infrastructure and Reliability " * 5)
    capped_key = build_posting_key("Acme", long_title, "Remote")
    legacy_key = f"acme|{' '.join(long_title.split()).lower()}|remote"

    async with engine.begin() as connection:
        for key, title in ((capped_key, "Short Title"), (legacy_key, long_title)):
            await connection.execute(
                text(
                    "INSERT INTO postings (posting_key, source, company, title, url, location, raw, "
                    "status, first_seen_at, last_seen_at) VALUES (:key, 'test', 'Acme', :title, "
                    "'https://example.com/job', 'Remote', '{}', 'open', :now, :now)"
                ),
                {"key": key, "title": title, "now": datetime.now(UTC).isoformat(" ")},
            )

    async with engine.begin() as connection:
        await connection.run_sync(_ensure_web_profile_columns)

    async with engine.connect() as connection:
        keys = {
            row[0]
            for row in (
                await connection.execute(text("SELECT posting_key FROM postings"))
            ).all()
        }

    assert keys == {capped_key, legacy_key}
    await engine.dispose()
