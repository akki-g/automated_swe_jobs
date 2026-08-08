from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.db.models import Criteria as CriteriaRow
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.notify.dispatch import send_email_digest
from app.pipeline import gather_pending_digests


class FakeEmailProvider:
    def __init__(self, success: bool = True):
        self.success = success
        self.sent = []

    async def send(self, to, subject, body):
        self.sent.append((to, subject, body))
        return self.success


@pytest.mark.asyncio
async def test_email_pending_is_independent_of_prior_sms_delivery(db_session, demo_user):
    now = datetime.now(UTC)
    demo_user.profile_completed_at = now
    demo_user.email_digest_enabled = True
    db_session.add(CriteriaRow(user_id=demo_user.id, role_types=["new_grad"], updated_at=now))
    posting = PostingRow(
        posting_key="acme|analyst|nyc",
        source="test",
        company="Acme",
        title="2027 Analyst",
        url="https://example.com/job",
        status="open",
        first_seen_at=now,
        last_seen_at=now,
    )
    db_session.add(posting)
    await db_session.flush()
    db_session.add(
        MatchRow(
            user_id=demo_user.id,
            posting_id=posting.id,
            score=0.96,
            blurb="Strong fit",
            priority="high",
            lane="fast",
            notified_channels=["sms"],
            notified_at=now,
            created_at=now,
        )
    )
    await db_session.flush()

    email_items = await gather_pending_digests(db_session, channel="email")
    sms_items = await gather_pending_digests(db_session, channel="sms")

    assert len(email_items) == 1
    assert email_items[0].matches[0][1].title == "2027 Analyst"
    assert sms_items == []  # high priority and already sent by SMS


@pytest.mark.asyncio
async def test_email_digest_is_email_only_and_reuses_uncapped_formatter():
    class User:
        email = "alex@example.com"
        email_digest_enabled = True

    class Match:
        pass

    class Posting:
        company = "Acme"
        title = "Graduate Consultant"
        url = "https://example.com/job"

    provider = FakeEmailProvider()
    outcome = await send_email_digest(User(), [(Match(), Posting())], provider)

    assert outcome.channel == "email"
    assert outcome.send.success is True
    assert provider.sent[0][0] == "alex@example.com"
    assert "Acme: Graduate Consultant" in provider.sent[0][2]


@pytest.mark.asyncio
async def test_completed_profile_gets_a_daily_email_even_without_new_matches(
    db_session, demo_user
):
    demo_user.profile_completed_at = datetime.now(UTC)
    demo_user.email_digest_enabled = True
    await db_session.flush()

    items = await gather_pending_digests(db_session, channel="email")
    provider = FakeEmailProvider()
    outcome = await send_email_digest(items[0].user, items[0].matches, provider)

    assert len(items) == 1
    assert items[0].matches == []
    assert outcome.send.success is True
    assert "No new job matches today" in provider.sent[0][2]


@pytest.mark.asyncio
async def test_email_digest_only_becomes_due_at_user_time_and_once_per_day(
    db_session, demo_user
):
    today = date(2026, 8, 8)
    demo_user.profile_completed_at = datetime.now(UTC)
    demo_user.email_digest_enabled = True
    demo_user.email_digest_time = "17:45"
    await db_session.flush()

    early = await gather_pending_digests(
        db_session,
        channel="email",
        email_due_time="17:44",
        email_digest_date=today,
    )
    due = await gather_pending_digests(
        db_session,
        channel="email",
        email_due_time="17:45",
        email_digest_date=today,
    )
    demo_user.last_email_digest_sent_on = today
    await db_session.flush()
    already_sent = await gather_pending_digests(
        db_session,
        channel="email",
        email_due_time="23:59",
        email_digest_date=today,
    )

    assert early == []
    assert len(due) == 1
    assert already_sent == []
