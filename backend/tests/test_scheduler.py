import asyncio
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db.models import Base
from app.db.models import Match as MatchRow
from app.db.models import Message as MessageRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.notify.dispatch import ChannelDigestOutcome, SendOutcome
from app.scheduler import (
    _record_channel_digest_outcomes,
    _record_instant_outcomes,
    build_scheduler,
    reliable_tier_sources,
)
from app.sources.registry import registry


@pytest.fixture
def db(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    import app.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "SessionLocal", session_factory)

    asyncio.run(_create_all(engine))
    return session_factory


async def _create_all(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


async def _seed(session_factory):
    async with session_factory() as session:
        user = User(
            name="Demo",
            phone="+15555550100",
            email="demo@example.com",
            sms_provider="signalwire",
            opted_out=False,
            consent_at=datetime.now(UTC),
            consent_method="verbal-friend-onboarding",
            created_at=datetime.now(UTC),
        )
        session.add(user)
        posting = PostingRow(
            posting_key="acme|swe|remote",
            source="test",
            company="Acme",
            title="SWE",
            url="https://example.com/1",
            status="open",
            first_seen_at=datetime.now(UTC),
            last_seen_at=datetime.now(UTC),
        )
        session.add(posting)
        await session.flush()
        match = MatchRow(
            user_id=user.id,
            posting_id=posting.id,
            score=0.95,
            blurb="great fit",
            priority="high",
            lane="fast",
            match_reason="new_posting",
            notified_channels=[],
            notified_at=None,
            created_at=datetime.now(UTC),
        )
        session.add(match)
        await session.commit()
        return user, match, posting


@pytest.mark.asyncio
async def test_record_instant_outcomes_marks_notified_only_on_success(db):
    user, match, posting = await _seed(db)
    outcomes = [
        (user, match, posting, SendOutcome(success=True, provider="signalwire")),
    ]

    await _record_instant_outcomes(outcomes)

    async with db() as session:
        refreshed = (
            await session.execute(select(MatchRow).where(MatchRow.id == match.id))
        ).scalar_one()
        assert refreshed.notified_at is not None
        assert refreshed.notified_channels == ["sms"]
        messages = (await session.execute(select(MessageRow))).scalars().all()
        assert len(messages) == 1
        assert messages[0].status == "sent"


@pytest.mark.asyncio
async def test_record_instant_outcomes_leaves_failed_send_unmarked(db):
    user, match, posting = await _seed(db)
    outcomes = [
        (
            user,
            match,
            posting,
            SendOutcome(success=False, provider="signalwire", error="http_500"),
        ),
    ]

    await _record_instant_outcomes(outcomes)

    async with db() as session:
        refreshed = (
            await session.execute(select(MatchRow).where(MatchRow.id == match.id))
        ).scalar_one()
        assert refreshed.notified_at is None  # must stay pending so it can be retried
        messages = (await session.execute(select(MessageRow))).scalars().all()
        assert len(messages) == 1
        assert messages[0].status == "failed"


@pytest.mark.asyncio
async def test_record_instant_outcomes_skips_opted_out_without_logging(db):
    user, match, posting = await _seed(db)
    outcomes = [
        (
            user,
            match,
            posting,
            SendOutcome(success=False, provider="signalwire", skipped=True),
        ),
    ]

    await _record_instant_outcomes(outcomes)

    async with db() as session:
        refreshed = (
            await session.execute(select(MatchRow).where(MatchRow.id == match.id))
        ).scalar_one()
        assert refreshed.notified_at is None
        messages = (await session.execute(select(MessageRow))).scalars().all()
        assert messages == []


@pytest.mark.asyncio
async def test_reliable_tier_sources_reuses_same_instances_across_calls(db):
    """End-to-end regression for the fast-lane source lifecycle bug: two
    calls to reliable_tier_sources() a "cycle" apart (as fast_lane_cycle
    makes) must return the same Source objects, constructed from the real
    companies.yaml, so check_for_changes() state survives — see
    app.sources.registry."""
    async with db() as session:
        first = await reliable_tier_sources(session)
    async with db() as session:
        second = await reliable_tier_sources(session)

    assert first  # sanity: companies.yaml actually has entries
    assert [id(s) for s in first] == [id(s) for s in second]


def test_scheduler_keeps_sms_digest_and_adds_daily_email_job():
    scheduler = build_scheduler()

    assert {job.id for job in scheduler.get_jobs()} == {
        "fast_lane_cycle",
        "slow_lane_cycle",
        "profile_backfill_cycle",
        "digest_cycle",
        "daily_email_cycle",
    }
    email_job = next(
        job for job in scheduler.get_jobs() if job.id == "daily_email_cycle"
    )
    assert str(email_job.trigger) == "cron[minute='*']"


@pytest.mark.asyncio
async def test_email_recording_merges_with_existing_sms_channel(db):
    user, match, posting = await _seed(db)
    async with db() as session:
        stored = (
            await session.execute(select(MatchRow).where(MatchRow.id == match.id))
        ).scalar_one()
        stored.notified_channels = ["sms"]
        stored.notified_at = datetime.now(UTC)
        await session.commit()

    sent_on = date(2026, 8, 8)
    await _record_channel_digest_outcomes(
        [
            ChannelDigestOutcome(
                user=user,
                matches=[(match, posting)],
                channel="email",
                send=SendOutcome(success=True, provider="resend"),
            )
        ],
        email_digest_sent_on=sent_on,
    )

    async with db() as session:
        refreshed = (
            await session.execute(select(MatchRow).where(MatchRow.id == match.id))
        ).scalar_one()
        assert set(refreshed.notified_channels) == {"sms", "email"}
        refreshed_user = (
            await session.execute(select(User).where(User.id == user.id))
        ).scalar_one()
        assert refreshed_user.last_email_digest_sent_on == sent_on
