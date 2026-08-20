import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db.models import Criteria as CriteriaRow
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import Watchlist as WatchlistRow
from app.domain.models import Posting, Priority, RoleType
from app.pipeline import (
    backfill_completed_profiles,
    match_new_postings,
    store_new_postings,
)


def _posting(key: str = "acme|new grad swe|remote") -> Posting:
    return Posting(
        posting_key=key,
        source="test",
        company="Acme",
        title="New Grad SWE",
        url="https://example.com/1",
        location="Remote",
        role_type=RoleType.NEW_GRAD,
        posted_at=None,
    )


class FakeRankClient:
    """Returns a real (below-threshold) score for every posting_key it's
    asked about, so tests can distinguish "ranked, matched" from "no rank
    result, intentionally left unmatched" (see
    test_match_new_postings_skips_posting_with_no_rank_result)."""

    async def create_message(self, *, system, messages, tools):
        keys = re.findall(r'"posting_key":\s*"([^"]+)"', messages[0]["content"])
        results = [{"posting_key": key, "score": 0.6, "blurb": "ok"} for key in keys]
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": tools[0]["name"],
                    "input": {"results": results},
                }
            ]
        }


class EmptyRankClient:
    """Simulates the LLM returning no result for any posting (a rank
    failure, or the model simply omitting a key) — see rank_postings, which
    already returns [] on an LLM call failure."""

    async def create_message(self, *, system, messages, tools):
        return {
            "content": [
                {"type": "tool_use", "name": tools[0]["name"], "input": {"results": []}}
            ]
        }


@pytest.mark.asyncio
async def test_completed_web_profile_is_backfilled_from_existing_open_postings(
    db_session, demo_user
):
    """A posting stored before signup/profile completion must still become a
    match; waiting for store_new_postings to return it again can never work
    because that function correctly returns only newly inserted rows."""
    demo_user.password_hash = "argon2-placeholder"
    demo_user.profile_completed_at = datetime.now(UTC)
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            target_fields=["software_engineering"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await store_new_postings(db_session, [_posting()])
    await db_session.commit()

    result = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()

    assert len(result.digest_items) == 1
    match = (await db_session.execute(select(MatchRow))).scalar_one()
    assert match.user_id == demo_user.id
    assert match.lane == "backfill"
    assert match.match_reason == "profile_backfill"
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is not None

    # The completion marker and pair-level exclusion make repeat polls safe.
    repeated = await backfill_completed_profiles(db_session, FakeRankClient())
    assert repeated.instant == []
    assert repeated.digest_items == []
    assert len((await db_session.execute(select(MatchRow))).scalars().all()) == 1


@pytest.mark.asyncio
async def test_profile_backfill_retries_when_ranking_returns_no_results(
    db_session, demo_user
):
    demo_user.password_hash = "argon2-placeholder"
    demo_user.profile_completed_at = datetime.now(UTC)
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            target_fields=["software_engineering"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await store_new_postings(db_session, [_posting()])
    await db_session.commit()

    failed = await backfill_completed_profiles(db_session, EmptyRankClient())
    await db_session.commit()
    assert failed.failed_user_ids == {demo_user.id}
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is None

    retried = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    assert len(retried.digest_items) == 1
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is not None


@pytest.mark.asyncio
async def test_store_new_postings_is_idempotent_across_calls(db_session):
    posting = _posting()

    first_new = await store_new_postings(db_session, [posting])
    second_new = await store_new_postings(db_session, [posting])

    assert len(first_new) == 1
    assert len(second_new) == 0

    all_rows = (await db_session.execute(select(PostingRow))).scalars().all()
    assert len(all_rows) == 1


@pytest.mark.asyncio
async def test_store_new_postings_reapplies_last_seen_at_bump_after_integrity_error_retry(
    db_session,
):
    """The IntegrityError recovery path must redo the *whole* pass —
    including bumping last_seen_at on already-known postings in the same
    batch — not just re-insert the genuinely-new rows (see
    store_new_postings / _insert_and_touch)."""
    existing = _posting(key="already-known")
    await store_new_postings(db_session, [existing])
    await (
        db_session.commit()
    )  # durable, so the retry's rollback() below doesn't undo it

    original_last_seen = (
        (
            await db_session.execute(
                select(PostingRow).where(PostingRow.posting_key == "already-known")
            )
        )
        .scalar_one()
        .last_seen_at
    )

    new_posting = _posting(key="brand-new")
    real_flush = db_session.flush
    call_count = {"n": 0}

    async def flaky_flush():
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise IntegrityError("stmt", {}, Exception("dup"))
        await real_flush()

    db_session.flush = flaky_flush

    new_rows = await store_new_postings(db_session, [existing, new_posting])

    assert call_count["n"] == 2  # first attempt failed, retry succeeded
    assert {row.posting_key for row in new_rows} == {"brand-new"}

    refreshed = (
        await db_session.execute(
            select(PostingRow).where(PostingRow.posting_key == "already-known")
        )
    ).scalar_one()
    assert refreshed.last_seen_at > original_last_seen


@pytest.mark.asyncio
async def test_match_new_postings_never_double_matches_same_user_posting(
    db_session, demo_user
):
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    client = FakeRankClient()

    # Simulates the fast lane seeing this posting first.
    new_rows_fast = await store_new_postings(db_session, [posting])
    result_fast = await match_new_postings(
        db_session, new_rows_fast, client, lane="fast"
    )
    await db_session.commit()

    # Simulates the slow lane's full sweep re-fetching the same posting.
    new_rows_slow = await store_new_postings(db_session, [posting])
    result_slow = await match_new_postings(
        db_session, new_rows_slow, client, lane="slow"
    )
    await db_session.commit()

    assert len(new_rows_fast) == 1
    assert len(new_rows_slow) == 0  # already stored — slow lane sees nothing new

    total_matches_for_this_posting = len(result_fast.digest_items[0].matches)
    assert total_matches_for_this_posting == 1
    assert result_slow.digest_items == []  # nothing new to match

    all_matches = (await db_session.execute(select(MatchRow))).scalars().all()
    assert len(all_matches) == 1


@pytest.mark.asyncio
async def test_match_new_postings_skips_posting_with_no_rank_result(
    db_session, demo_user
):
    """A rank failure (or the model omitting a key) must leave the posting
    unmatched for that user, not write a permanent score=0/blurb="" match —
    otherwise the unique (user_id, posting_id) constraint blocks it from
    ever being retried (see spec: Data flow)."""
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, EmptyRankClient(), lane="fast"
    )
    await db_session.commit()

    assert result.instant == []
    assert result.digest_items == []
    all_matches = (await db_session.execute(select(MatchRow))).scalars().all()
    assert all_matches == []

    # Retrying with a real rank result should now succeed — the posting was
    # never "used up" by the failed attempt.
    result2 = await match_new_postings(
        db_session, new_rows, FakeRankClient(), lane="slow"
    )
    await db_session.commit()
    assert len(result2.digest_items) == 1


class LowScoreRankClient:
    """Scores every posting well below settings.min_match_score — simulates
    a real but poor-fit result, distinct from EmptyRankClient's "no result
    at all" failure case."""

    def __init__(self, score: float = 0.1) -> None:
        self.score = score

    async def create_message(self, *, system, messages, tools):
        keys = re.findall(r'"posting_key":\s*"([^"]+)"', messages[0]["content"])
        results = [
            {"posting_key": key, "score": self.score, "blurb": "weak fit"}
            for key in keys
        ]
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": tools[0]["name"],
                    "input": {"results": results},
                }
            ]
        }


@pytest.mark.asyncio
async def test_match_new_postings_drops_result_below_min_match_score(
    db_session, demo_user
):
    """A real but poor-fit score must never become a stored match — this is
    the actual bug behind 'the digest barely sent truly relevant jobs':
    every rule-filter survivor that got ranked at all, however weak the fit,
    used to become a permanent match."""
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, LowScoreRankClient(0.1), lane="slow"
    )
    await db_session.commit()

    assert result.instant == []
    assert result.digest_items == []
    assert (await db_session.execute(select(MatchRow))).scalars().all() == []


@pytest.mark.asyncio
async def test_match_new_postings_keeps_result_at_or_above_min_match_score(
    db_session, demo_user
):
    from app.config import settings

    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, LowScoreRankClient(settings.min_match_score), lane="slow"
    )
    await db_session.commit()

    assert len(result.digest_items) == 1


@pytest.mark.asyncio
async def test_incomplete_web_profile_is_not_ranked(db_session, demo_user):
    demo_user.password_hash = "argon2-placeholder"
    demo_user.profile_completed_at = None
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=[],
            target_fields=[],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, FakeRankClient(), lane="slow"
    )

    assert result.instant == []
    assert result.digest_items == []
    assert (await db_session.execute(select(MatchRow))).scalars().all() == []


@pytest.mark.asyncio
async def test_match_new_postings_forces_instant_priority_for_watchlisted_company(
    db_session, demo_user
):
    """A posting from a company the user is actively watching is always
    instant, regardless of its LLM score — see spec addendum: watchlist
    priority."""
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    db_session.add(
        WatchlistRow(
            user_id=demo_user.id,
            company_name="Acme",
            company_key="acme",
            ats_provider="greenhouse",
            ats_slug="acme",
            status="active",
            created_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()  # company="Acme"
    new_rows = await store_new_postings(db_session, [posting])

    # FakeRankClient always scores 0.6, well below the instant threshold but above min_match_score.
    result = await match_new_postings(
        db_session, new_rows, FakeRankClient(), lane="fast"
    )
    await db_session.commit()

    assert len(result.instant) == 1
    assert result.digest_items == []
    _user, match_row, _posting_row = result.instant[0]
    assert match_row.priority == Priority.HIGH.value
    assert match_row.match_reason == "watchlist"


class TargetFieldTaggingRankClient:
    """Returns the given target_field for every posting_key it's asked
    about, so tests can assert the tag survives into the persisted
    MatchRow."""

    def __init__(self, target_field: str | None) -> None:
        self.target_field = target_field

    async def create_message(self, *, system, messages, tools):
        keys = re.findall(r'"posting_key":\s*"([^"]+)"', messages[0]["content"])
        results = [
            {
                "posting_key": key,
                "score": 0.6,
                "blurb": "ok",
                "target_field": self.target_field,
            }
            for key in keys
            if self.target_field is not None
        ] or [{"posting_key": key, "score": 0.6, "blurb": "ok"} for key in keys]
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": tools[0]["name"],
                    "input": {"results": results},
                }
            ]
        }


@pytest.mark.asyncio
async def test_match_new_postings_persists_matched_target_field(db_session, demo_user):
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            target_fields=["consulting"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, TargetFieldTaggingRankClient("consulting"), lane="slow"
    )
    await db_session.commit()

    _match_row, _posting_row = result.digest_items[0].matches[0]
    assert _match_row.matched_target_field == "consulting"


@pytest.mark.asyncio
async def test_match_new_postings_leaves_matched_target_field_none_when_untagged(
    db_session, demo_user
):
    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            target_fields=["consulting"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, TargetFieldTaggingRankClient(None), lane="slow"
    )
    await db_session.commit()

    _match_row, _posting_row = result.digest_items[0].matches[0]
    assert _match_row.matched_target_field is None


@pytest.mark.asyncio
async def test_match_new_postings_excludes_posting_with_dead_link(
    db_session, demo_user, monkeypatch
):
    """A confirmed-dead link (404/410/451) must never become a match for
    anyone — see spec addendum: link validation, the concrete complaint
    that users were seeing 'empty'/gone postings in their digest."""

    async def _dead(url, client):
        return False

    monkeypatch.setattr("app.pipeline.check_link_alive", _dead)

    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, FakeRankClient(), lane="slow"
    )
    await db_session.commit()

    assert result.instant == []
    assert result.digest_items == []
    assert (await db_session.execute(select(MatchRow))).scalars().all() == []

    refreshed = (
        await db_session.execute(
            select(PostingRow).where(PostingRow.posting_key == posting.posting_key)
        )
    ).scalar_one()
    assert refreshed.status == "closed"


@pytest.mark.asyncio
async def test_match_new_postings_treats_inconclusive_link_check_as_alive(
    db_session, demo_user, monkeypatch
):
    """A network error/timeout/5xx is inconclusive, not evidence the posting
    is gone — must not block a match (fail-open, same philosophy as every
    other source/network interaction in this codebase)."""

    async def _inconclusive(url, client):
        return None

    monkeypatch.setattr("app.pipeline.check_link_alive", _inconclusive)

    db_session.add(
        CriteriaRow(
            user_id=demo_user.id,
            role_types=["new_grad"],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    result = await match_new_postings(
        db_session, new_rows, FakeRankClient(), lane="slow"
    )
    await db_session.commit()

    assert len(result.digest_items) == 1

    refreshed = (
        await db_session.execute(
            select(PostingRow).where(PostingRow.posting_key == posting.posting_key)
        )
    ).scalar_one()
    assert refreshed.status == "open"


@pytest.mark.asyncio
async def test_match_new_postings_checks_link_once_per_posting_not_per_user(
    db_session, monkeypatch
):
    """Two users both matching the same new posting must trigger exactly
    one link check for it, not one per (user, posting) pair."""
    from app.db.models import User as UserRow

    call_count = {"n": 0}

    async def _counting_alive(url, client):
        call_count["n"] += 1
        return True

    monkeypatch.setattr("app.pipeline.check_link_alive", _counting_alive)

    for email in ("alex@example.com", "sam@example.com"):
        user = UserRow(
            name=email,
            email=email,
            sms_provider="signalwire",
            opted_out=False,
            created_at=datetime.now(UTC),
        )
        db_session.add(user)
        await db_session.flush()
        db_session.add(
            CriteriaRow(
                user_id=user.id,
                role_types=["new_grad"],
                keywords=[],
                locations=[],
                sponsorship_required=None,
                freeform_notes="",
                updated_at=datetime.now(UTC),
            )
        )
    await db_session.flush()

    posting = _posting()
    new_rows = await store_new_postings(db_session, [posting])

    await match_new_postings(db_session, new_rows, FakeRankClient(), lane="slow")
    await db_session.commit()

    assert call_count["n"] == 1
