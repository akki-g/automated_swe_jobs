import re
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db.models import Criteria as CriteriaRow
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.db.models import Watchlist as WatchlistRow
from app.domain.models import Posting, Priority, RoleType
from app.pipeline import (
    _PROFILE_BACKFILL_VERSION,
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


def _completed_web_user(session, *, email: str, **criteria_overrides) -> User:
    """A finished web signup with criteria — the shape the backfill acts on."""
    user = User(
        name=email.split("@")[0],
        email=email,
        password_hash="argon2-placeholder",
        sms_provider="signalwire",
        opted_out=False,
        consent_at=datetime.now(UTC),
        consent_method="web-signup-terms-v1",
        created_at=datetime.now(UTC),
        profile_completed_at=datetime.now(UTC),
    )
    session.add(user)
    return user


def _criteria_for(user: User, **overrides) -> CriteriaRow:
    values = dict(
        role_types=["new_grad"],
        target_fields=["software_engineering"],
        keywords=[],
        locations=[],
        sponsorship_required=None,
        freeform_notes="",
        updated_at=datetime.now(UTC),
    )
    values.update(overrides)
    return CriteriaRow(user_id=user.id, **values)


async def _drain_backfill(session, client, *, cycles: int = 50):
    """Run the one-minute backfill cycle until it stops doing work, the way
    the scheduler would over several minutes."""
    for _ in range(cycles):
        await backfill_completed_profiles(session, client)
        await session.commit()


@pytest.mark.asyncio
async def test_backfill_seeds_newest_postings_on_first_turn(
    db_session, demo_user, monkeypatch
):
    """A fresh profile should not wait through the oldest corpus pages before
    seeing current jobs. The first page is a newest-first seed; the cursor
    scan still covers everything on later turns."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 2)
    user = _completed_web_user(db_session, email="newest-first@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(user))
    postings = [_posting(key=f"acme|new grad swe|city{i}") for i in range(5)]
    await store_new_postings(db_session, postings)
    await db_session.commit()

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    await db_session.refresh(user)

    matched = {
        row.posting_key
        for row in (
            await db_session.execute(
                select(PostingRow)
                .join(MatchRow, MatchRow.posting_id == PostingRow.id)
                .where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    }
    assert matched == {postings[-1].posting_key, postings[-2].posting_key}
    assert user.initial_match_backfill_recent_seeded is True
    assert user.initial_match_backfill_cursor == 0
    assert user.initial_match_backfill_version < _PROFILE_BACKFILL_VERSION


@pytest.mark.asyncio
async def test_backfill_scheduler_does_not_starve_new_profiles(
    db_session, demo_user, monkeypatch
):
    """More pending profiles than the per-cycle cap must rotate fairly.

    The production failure had six pending profiles and a cap of five. The
    old oldest-signup-first query selected the same five on every turn while
    their long corpus scans ran, so the newest account never got a page.
    """
    monkeypatch.setattr(settings, "profile_backfill_max_users_per_cycle", 2)
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 1)
    users = []
    for index in range(3):
        user = _completed_web_user(db_session, email=f"round-robin-{index}@example.com")
        user.profile_completed_at = datetime(2026, 8, 20, 12 + index, tzinfo=UTC)
        await db_session.flush()
        db_session.add(_criteria_for(user))
        users.append(user)
    await store_new_postings(
        db_session, [_posting(key=f"acme|new grad swe|city{i}") for i in range(3)]
    )
    await db_session.commit()

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()

    matched_user_ids = set(
        (
            await db_session.execute(
                select(MatchRow.user_id).where(
                    MatchRow.user_id.in_([user.id for user in users])
                )
            )
        )
        .scalars()
        .all()
    )
    assert matched_user_ids == {users[1].id, users[2].id}

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    matched_user_ids = set(
        (
            await db_session.execute(
                select(MatchRow.user_id).where(
                    MatchRow.user_id.in_([user.id for user in users])
                )
            )
        )
        .scalars()
        .all()
    )
    assert matched_user_ids == {user.id for user in users}


@pytest.mark.asyncio
async def test_completed_profile_gets_one_corrected_recent_seed(
    db_session, demo_user, monkeypatch
):
    """Profiles completed by phase 3 before the scheduler/location repair
    still need one newest-page retry, but must not pay for another complete
    historical scan."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 2)
    user = _completed_web_user(db_session, email="completed-before-fix@example.com")
    user.initial_match_backfill_version = _PROFILE_BACKFILL_VERSION
    user.initial_matches_generated_at = datetime.now(UTC)
    user.initial_match_backfill_recent_seeded = False
    await db_session.flush()
    db_session.add(_criteria_for(user))
    postings = [_posting(key=f"acme|new grad swe|city{i}") for i in range(4)]
    await store_new_postings(db_session, postings)
    await db_session.commit()

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    await db_session.refresh(user)

    matches = (
        (
            await db_session.execute(
                select(MatchRow).where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(matches) == 2
    assert user.initial_match_backfill_recent_seeded is True
    assert user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    matches = (
        (
            await db_session.execute(
                select(MatchRow).where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert len(matches) == 2


@pytest.mark.asyncio
async def test_backfill_covers_the_whole_corpus_not_just_one_page(
    db_session, demo_user, monkeypatch
):
    """The backfill considered only a bounded window of the newest postings
    and then consumed its one-time marker, so everything older stayed
    invisible to that user forever. It must page through the entire open
    corpus instead."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 3)
    user = _completed_web_user(db_session, email="new@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(user))
    postings = [_posting(key=f"acme|new grad swe|city{i}") for i in range(10)]
    await store_new_postings(db_session, postings)
    await db_session.commit()

    await _drain_backfill(db_session, FakeRankClient())

    matched = {
        row.posting_key
        for row in (
            await db_session.execute(
                select(PostingRow)
                .join(MatchRow, MatchRow.posting_id == PostingRow.id)
                .where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    }
    assert matched == {p.posting_key for p in postings}


@pytest.mark.asyncio
async def test_signup_time_does_not_change_the_match_set(
    db_session, demo_user, monkeypatch
):
    """The requirement, stated directly: two profiles with identical criteria
    must end up with identical matches regardless of when they signed up.

    `early` is matched the way an established user is — through the lane path,
    as each posting is ingested. `late` never sees those postings as new and
    depends entirely on the backfill. Under the bounded window, `late` could
    only ever reach the newest page of them.
    """
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 3)
    early = _completed_web_user(db_session, email="early@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(early))
    await db_session.commit()

    # Established user: matched incrementally as inventory arrives.
    postings = [_posting(key=f"acme|new grad swe|city{i}") for i in range(10)]
    for posting in postings:
        new_rows = await store_new_postings(db_session, [posting])
        await match_new_postings(db_session, new_rows, FakeRankClient())
        await db_session.commit()

    # Late signup: the same criteria, against inventory that is already old.
    late = _completed_web_user(db_session, email="late@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(late))
    await db_session.commit()

    await _drain_backfill(db_session, FakeRankClient())

    async def _keys(user_id: int) -> set[str]:
        return {
            row.posting_key
            for row in (
                await db_session.execute(
                    select(PostingRow)
                    .join(MatchRow, MatchRow.posting_id == PostingRow.id)
                    .where(MatchRow.user_id == user_id)
                )
            )
            .scalars()
            .all()
        }

    assert await _keys(late.id) == await _keys(early.id)


@pytest.mark.asyncio
async def test_backfill_stays_pending_until_the_corpus_is_exhausted(
    db_session, demo_user, monkeypatch
):
    """The completion marker means "this profile has seen every open
    posting", so it must not be set while pages remain."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 3)
    user = _completed_web_user(db_session, email="paging@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(user))
    await store_new_postings(
        db_session, [_posting(key=f"acme|new grad swe|city{i}") for i in range(10)]
    )
    await db_session.commit()

    await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    await db_session.refresh(user)
    assert user.initial_match_backfill_version < _PROFILE_BACKFILL_VERSION

    await _drain_backfill(db_session, FakeRankClient())
    await db_session.refresh(user)
    assert user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION


@pytest.mark.asyncio
async def test_backfill_reaches_postings_of_every_requested_role_type(
    db_session, demo_user, monkeypatch
):
    """A large inventory of one opportunity type must not crowd out the
    other — the original reason for the per-role-type windows, which the
    full-corpus scan has to keep satisfying."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 3)
    user = _completed_web_user(db_session, email="both@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(user, role_types=["new_grad", "intern"]))
    interns = [
        Posting(
            posting_key=f"acme|intern swe|city{i}",
            source="test",
            company="Acme",
            title="SWE Intern",
            url=f"https://example.com/i{i}",
            location=f"City{i}",
            role_type=RoleType.INTERN,
            posted_at=None,
        )
        for i in range(8)
    ]
    grads = [_posting(key=f"acme|new grad swe|city{i}") for i in range(2)]
    await store_new_postings(db_session, interns + grads)
    await db_session.commit()

    await _drain_backfill(db_session, FakeRankClient())

    matched_types = {
        row.role_type
        for row in (
            await db_session.execute(
                select(PostingRow)
                .join(MatchRow, MatchRow.posting_id == PostingRow.id)
                .where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    }
    assert matched_types == {"new_grad", "intern"}


@pytest.mark.asyncio
async def test_backfill_with_no_role_type_selected_still_reaches_everything(
    db_session, demo_user, monkeypatch
):
    """Empty role_types means "no opportunity-type constraint" in
    matching.filters, so the corpus scan must not narrow on it either —
    including postings whose role_type the sources never classified."""
    monkeypatch.setattr(settings, "profile_backfill_max_postings_per_user", 3)
    user = _completed_web_user(db_session, email="anytype@example.com")
    await db_session.flush()
    db_session.add(_criteria_for(user, role_types=[]))
    unclassified = [
        Posting(
            posting_key=f"acme|mystery role|city{i}",
            source="test",
            company="Acme",
            title="Mystery Role",
            url=f"https://example.com/m{i}",
            location=f"City{i}",
            role_type=None,
            posted_at=None,
        )
        for i in range(5)
    ]
    grads = [_posting(key=f"acme|new grad swe|city{i}") for i in range(5)]
    await store_new_postings(db_session, unclassified + grads)
    await db_session.commit()

    await _drain_backfill(db_session, FakeRankClient())

    matched = {
        row.posting_key
        for row in (
            await db_session.execute(
                select(PostingRow)
                .join(MatchRow, MatchRow.posting_id == PostingRow.id)
                .where(MatchRow.user_id == user.id)
            )
        )
        .scalars()
        .all()
    }
    assert matched == {p.posting_key for p in unclassified + grads}


class PermanentlyPartialRankClient:
    """Scores every posting except one, on every call.

    Distinct from a transient failure: retrying can never improve coverage,
    so a retry policy that demands complete coverage will retry forever.
    """

    def __init__(self, omit_key: str) -> None:
        self.omit_key = omit_key
        self.calls = 0

    async def create_message(self, *, system, messages, tools):
        self.calls += 1
        keys = re.findall(r'"posting_key":\s*"([^"]+)"', messages[0]["content"])
        results = [
            {"posting_key": key, "score": 0.6, "blurb": "ok"}
            for key in keys
            if key != self.omit_key
        ]
        return {
            "content": [
                {"type": "tool_use", "name": tools[0]["name"], "input": {"results": results}}
            ]
        }


@pytest.mark.asyncio
async def test_profile_backfill_stops_retrying_a_permanently_partial_ranking(
    db_session, demo_user
):
    """A profile must not be pinned in the retry loop forever.

    Completion required a rank result for *every* survivor, but a posting the
    model simply never returns a result for makes that unreachable: the
    profile stays pending and every one-minute cycle re-ranks its whole
    inventory again. That is the state the max_tokens truncation bug put real
    users into (it made coverage permanently zero), and it is what filled the
    logs with "maximum number of running instances reached". Recovering from
    transient failures must not mean retrying a permanent one indefinitely.
    """
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
    scored = _posting(key="acme|new grad swe|remote")
    never_scored = _posting(key="acme|backend engineer|nyc")
    await store_new_postings(db_session, [scored, never_scored])
    await db_session.commit()

    client = PermanentlyPartialRankClient(omit_key=never_scored.posting_key)
    for _ in range(12):
        await backfill_completed_profiles(db_session, client)
        await db_session.commit()

    await db_session.refresh(demo_user)
    assert demo_user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION
    assert demo_user.initial_matches_generated_at is not None


@pytest.mark.asyncio
async def test_waiting_for_inventory_never_burns_the_retry_budget(
    db_session, demo_user
):
    """The retry bound must not resurrect the bug it sits next to.

    A fresh deployment runs this cycle every minute while the first slow-lane
    scrape (15 min) is still pending, so a profile can see many empty cycles
    before any inventory exists. Those must not count as attempts — if they
    did, the profile would exhaust its budget against an empty database and
    be marked complete with no matches at all, which is exactly the v1 bug
    _PROFILE_BACKFILL_VERSION 2 exists to undo.
    """
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
    await db_session.commit()

    for _ in range(12):
        await backfill_completed_profiles(db_session, FakeRankClient())
        await db_session.commit()

    await db_session.refresh(demo_user)
    assert demo_user.initial_match_backfill_version == 0
    assert demo_user.initial_matches_generated_at is None

    # Inventory finally lands: the profile still has its full budget and
    # backfills normally.
    await store_new_postings(db_session, [_posting()])
    await db_session.commit()
    result = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()

    assert len(result.digest_items) == 1
    await db_session.refresh(demo_user)
    assert demo_user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION


@pytest.mark.asyncio
async def test_profile_backfill_keeps_matches_it_did_manage_to_score(
    db_session, demo_user
):
    """Giving up on the unscorable posting must not cost the user the
    matches that did rank — those are the whole point of the backfill."""
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
    scored = _posting(key="acme|new grad swe|remote")
    never_scored = _posting(key="acme|backend engineer|nyc")
    await store_new_postings(db_session, [scored, never_scored])
    await db_session.commit()

    client = PermanentlyPartialRankClient(omit_key=never_scored.posting_key)
    for _ in range(12):
        await backfill_completed_profiles(db_session, client)
        await db_session.commit()

    matched_keys = {
        row.posting_key
        for row in (
            await db_session.execute(
                select(PostingRow).join(MatchRow, MatchRow.posting_id == PostingRow.id)
            )
        )
        .scalars()
        .all()
    }
    assert matched_keys == {scored.posting_key}


@pytest.mark.asyncio
async def test_profile_backfill_stops_re_ranking_once_it_gives_up(
    db_session, demo_user
):
    """The cost half of the same bug: a pinned profile re-ranked its whole
    inventory every single cycle. Once the backfill gives up, further cycles
    must not keep calling the ranker for that user."""
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
    scored = _posting(key="acme|new grad swe|remote")
    never_scored = _posting(key="acme|backend engineer|nyc")
    await store_new_postings(db_session, [scored, never_scored])
    await db_session.commit()

    client = PermanentlyPartialRankClient(omit_key=never_scored.posting_key)
    for _ in range(12):
        await backfill_completed_profiles(db_session, client)
        await db_session.commit()
    calls_after_giving_up = client.calls

    await backfill_completed_profiles(db_session, client)
    await db_session.commit()

    assert client.calls == calls_after_giving_up


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
    assert demo_user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION

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
    assert demo_user.initial_match_backfill_version == 0

    retried = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()
    assert len(retried.digest_items) == 1
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is not None
    assert demo_user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION


@pytest.mark.asyncio
async def test_profile_backfill_does_not_complete_before_open_inventory_exists(
    db_session, demo_user
):
    """Regression for the production restart sequence: the one-minute
    backfill can run before the first slow-lane scrape. An empty inventory
    must leave the profile pending rather than permanently consuming its
    one-time marker."""
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
    await db_session.commit()

    empty_attempt = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()

    assert empty_attempt.failed_user_ids == {demo_user.id}
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is None
    assert demo_user.initial_match_backfill_version == 0

    await store_new_postings(db_session, [_posting()])
    await db_session.commit()
    retried = await backfill_completed_profiles(db_session, FakeRankClient())
    await db_session.commit()

    assert len(retried.digest_items) == 1
    await db_session.refresh(demo_user)
    assert demo_user.initial_matches_generated_at is not None
    assert demo_user.initial_match_backfill_version == _PROFILE_BACKFILL_VERSION


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
async def test_store_new_postings_reopens_stale_posting_when_seen_again(db_session):
    posting = _posting()
    rows = await store_new_postings(db_session, [posting])
    rows[0].status = "stale"
    await db_session.commit()

    new_rows = await store_new_postings(db_session, [posting])
    await db_session.commit()

    assert new_rows == []
    stored = (await db_session.execute(select(PostingRow))).scalar_one()
    assert stored.status == "open"


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
