from datetime import UTC, datetime

import pytest

from app.db.models import Match, Posting, User
from app.notify.dispatch import (
    _format_email_digest,
    _format_sms_digest,
    send_digest,
    send_email_digest,
    send_instant,
    send_instants,
    send_sms_digest,
)
from app.notify.sms.base import SendResult


def _user(**overrides) -> User:
    defaults = dict(
        id=1,
        name="Demo",
        phone="+15555550100",
        email="demo@example.com",
        sms_provider="signalwire",
        opted_out=False,
        consent_method="verbal-friend-onboarding",
        created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return User(**defaults)


def _posting(**overrides) -> Posting:
    defaults = dict(
        id=1,
        posting_key="k1",
        source="test",
        company="Acme",
        title="New Grad SWE",
        url="https://example.com/1",
        status="open",
        first_seen_at=datetime.now(UTC),
        last_seen_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Posting(**defaults)


def _match(**overrides) -> Match:
    defaults = dict(
        id=1, user_id=1, posting_id=1, score=0.5, blurb="", priority="normal",
        lane="slow", match_reason="new_posting", notified_channels=[], created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Match(**defaults)


class FakeSmsProvider:
    name = "fake"

    def __init__(self, result: SendResult | None = None):
        self.sent = []
        self._result = result or SendResult(success=True, provider="fake")

    async def send(self, to, body):
        self.sent.append((to, body))
        return self._result


class FakeEmailProvider:
    def __init__(self, success: bool = True):
        self.sent = []
        self._success = success

    async def send(self, to, subject, body, html=None):
        self.sent.append((to, subject, body, html))
        return self._success


def test_format_sms_digest_caps_and_shows_remaining_count():
    items = [(_match(), _posting(posting_key=f"k{i}", company=f"C{i}")) for i in range(10)]
    text, sent = _format_sms_digest(items)
    assert "+2 more" in text
    assert "reply LIST" in text
    assert len(sent) == 8  # only the named matches may be recorded as delivered


def test_format_email_digest_is_curated_to_the_configured_cap():
    """The email digest is deliberately curated (relevant, diverse top-N),
    not an uncapped dump of every pending match — see notify/curate.py and
    the digest_max_email_matches/digest_max_per_company settings. Each
    posting here is a distinct company, so the per-company cap doesn't come
    into play — this just checks the overall cap and that both a text and
    an html part are produced."""
    from app.config import settings

    items = [(_match(), _posting(posting_key=f"k{i}", company=f"C{i}")) for i in range(30)]
    user = _user()

    text, html, sent = _format_email_digest(user, items)

    shown_companies = [f"C{i}" for i in range(30) if f"- C{i}:" in text]
    assert len(shown_companies) == settings.digest_max_email_matches
    assert "View all your matches" in text
    assert "View all your matches" in html
    assert len(sent) == settings.digest_max_email_matches


@pytest.mark.asyncio
async def test_send_digest_skips_opted_out_user():
    sms = FakeSmsProvider()
    email = FakeEmailProvider()
    user = _user(opted_out=True)

    outcome = await send_digest(user, [(_match(), _posting())], sms, email)

    assert sms.sent == []
    assert email.sent == []
    assert outcome.sms.skipped
    assert outcome.delivered is False


@pytest.mark.asyncio
async def test_send_digest_sends_sms_and_email():
    sms = FakeSmsProvider()
    email = FakeEmailProvider()
    user = _user()

    outcome = await send_digest(user, [(_match(), _posting())], sms, email)

    assert len(sms.sent) == 1
    assert sms.sent[0][0] == user.phone
    assert len(email.sent) == 1
    assert email.sent[0][0] == user.email
    assert outcome.sms.success
    assert outcome.email is not None and outcome.email.success
    assert outcome.delivered is True


@pytest.mark.asyncio
async def test_send_digest_skips_email_when_no_address():
    sms = FakeSmsProvider()
    email = FakeEmailProvider()
    user = _user(email=None)

    outcome = await send_digest(user, [(_match(), _posting())], sms, email)

    assert len(sms.sent) == 1
    assert email.sent == []
    assert outcome.email is not None and outcome.email.skipped
    assert outcome.delivered is True  # SMS alone still counts as delivered


@pytest.mark.asyncio
async def test_send_digest_delivered_if_sms_fails_but_email_succeeds():
    sms = FakeSmsProvider(SendResult(success=False, provider="fake", error="http_500"))
    email = FakeEmailProvider(success=True)
    user = _user()

    outcome = await send_digest(user, [(_match(), _posting())], sms, email)

    assert outcome.sms.success is False
    assert outcome.email is not None and outcome.email.success
    assert outcome.delivered is True


@pytest.mark.asyncio
async def test_send_digest_not_delivered_if_both_channels_fail():
    sms = FakeSmsProvider(SendResult(success=False, provider="fake", error="http_500"))
    email = FakeEmailProvider(success=False)
    user = _user()

    outcome = await send_digest(user, [(_match(), _posting())], sms, email)

    assert outcome.delivered is False


@pytest.mark.asyncio
async def test_send_instant_includes_blurb():
    sms = FakeSmsProvider()
    user = _user()
    match = _match(blurb="excellent fit")
    posting = _posting()

    outcome = await send_instant(user, match, posting, sms)

    assert "excellent fit" in sms.sent[0][1]
    assert outcome.success is True


@pytest.mark.asyncio
async def test_send_instant_skips_opted_out_user():
    sms = FakeSmsProvider()
    user = _user(opted_out=True)

    outcome = await send_instant(user, _match(), _posting(), sms)

    assert sms.sent == []
    assert outcome.skipped is True


@pytest.mark.asyncio
async def test_send_instant_reports_failure_without_raising():
    sms = FakeSmsProvider(SendResult(success=False, provider="fake", error="timeout", ambiguous=True))
    user = _user()

    outcome = await send_instant(user, _match(), _posting(), sms)

    assert outcome.success is False
    assert outcome.error == "timeout"


@pytest.mark.asyncio
async def test_send_instants_pairs_each_item_with_its_own_outcome():
    sms = FakeSmsProvider()
    items = [
        (_user(id=1, phone="+15555550101"), _match(id=1), _posting(id=1)),
        (_user(id=2, phone="+15555550102"), _match(id=2), _posting(id=2)),
    ]

    results = await send_instants(items, sms)

    assert [user.id for user, _m, _p, _o in results] == [1, 2]
    assert all(outcome.success for *_ , outcome in results)


@pytest.mark.asyncio
async def test_email_digest_outcome_reports_only_the_matches_it_sent():
    """Curation caps the email, and the scheduler marks everything on the
    outcome as notified — so the outcome must carry the curated subset, not
    the full backlog. Reporting the backlog silently consumed every match
    the email left out, and a notified match is never picked up by a later
    digest.
    """
    from app.config import settings

    items = [(_match(), _posting(posting_key=f"k{i}", company=f"C{i}")) for i in range(40)]
    user = _user(email_digest_enabled=True)
    provider = FakeEmailProvider()

    outcome = await send_email_digest(user, items, provider)

    sent_body = provider.sent[0][2]
    named = [f"C{i}" for i in range(40) if f"- C{i}:" in sent_body]
    assert len(named) == settings.digest_max_email_matches
    assert len(outcome.matches) == len(named)
    assert {posting.company for _match, posting in outcome.matches} == set(named)


@pytest.mark.asyncio
async def test_sms_digest_outcome_reports_only_the_matches_it_sent():
    """Same contract for SMS: the "+N more" tail was never named, so it has
    to stay pending for a later digest rather than being marked delivered."""
    from app.config import settings

    items = [(_match(), _posting(posting_key=f"k{i}", company=f"C{i}")) for i in range(20)]
    user = _user(phone="+15555550100")
    provider = FakeSmsProvider()

    outcome = await send_sms_digest(user, items, provider)

    assert len(outcome.matches) == settings.digest_max_sms_matches
    assert len(outcome.matches) < len(items)
