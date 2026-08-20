from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db.models import Criteria as CriteriaRow
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.domain.models import Criteria, Posting, Priority, RoleType, TargetField
from app.ingest.dedupe import dedupe_postings, filter_new
from app.ingest.link_check import check_link_alive
from app.ingest.normalize import extract_description, normalize, normalize_company_key
from app.matching.filters import filter_postings
from app.matching.rank import AnthropicClient, compute_priority, rank_postings
from app.notify.dispatch import DigestItem
from app.sources.base import Source
from app.watchlist.service import watchlisted_company_keys_by_user

logger = logging.getLogger(__name__)

_POSTING_KEY_QUERY_BATCH_SIZE = 5_000
# Version 1 could mark a profile complete when no open inventory existed.
# Version 2 retries those profiles after deployment and only advances when a
# non-empty inventory was actually considered without ranking failures.
# Version 3 replaces the bounded newest-N window with a cursor over the whole
# open corpus, so a profile's matches no longer depend on when it signed up.
# Bumping the version re-scans every existing profile once; pairs they were
# already matched on are excluded before ranking, so the re-scan only pays
# for postings that were previously out of reach.
_PROFILE_BACKFILL_VERSION = 3

# How many times a profile may be ranked without full coverage before the
# backfill accepts what it got and stops retrying. Retrying is what recovers
# a profile from a transient ranking failure (an API error, or the
# max_tokens truncation that returned zero results for a whole batch), and
# those clear within a cycle or two. A posting the model never returns a
# result for does not clear, and demanding complete coverage pinned such a
# profile in the retry loop forever: never marked complete, re-ranking its
# whole inventory every single cycle. Bounding the retries keeps the
# recovery while making the pathological case terminate. Only cycles that
# actually ranked something count (see backfill_completed_profiles), so
# waiting for the first scrape to land never burns an attempt.
_MAX_BACKFILL_ATTEMPTS = 5


@dataclass
class MatchCycleResult:
    instant: list[tuple[User, MatchRow, PostingRow]]
    digest_items: list[DigestItem]
    failed_user_ids: set[int] = field(default_factory=set)


async def _fetch_source(source: Source) -> list:
    try:
        return await source.fetch()
    except Exception:  # noqa: BLE001 - isolate one bad source from the rest
        logger.warning(
            "source %s failed", getattr(source, "name", source), exc_info=True
        )
        return []


async def run_sources(sources: list[Source]) -> list[Posting]:
    """Fetch every source concurrently (see spec: Source isolation — each
    source is independent I/O against a different host, so there's no reason
    to pay the sum of every source's latency instead of the max); a failing
    source contributes nothing rather than aborting the cycle."""
    raw_batches = await asyncio.gather(*(_fetch_source(source) for source in sources))

    normalized = [normalize(raw) for batch in raw_batches for raw in batch]
    return dedupe_postings(normalized)


def _new_posting_row(posting: Posting, now: datetime) -> PostingRow:
    return PostingRow(
        posting_key=posting.posting_key,
        source=posting.source,
        company=posting.company,
        title=posting.title,
        url=posting.url,
        location=posting.location,
        role_type=posting.role_type.value if posting.role_type else None,
        posted_at=posting.posted_at,
        raw=posting.raw,
        status="open",
        first_seen_at=now,
        last_seen_at=now,
    )


async def _insert_and_touch(
    session: AsyncSession, postings: list[Posting], now: datetime
) -> list[PostingRow]:
    """Bump last_seen_at on already-known postings and insert rows for new
    ones. Shared by the first attempt and the post-rollback retry so the
    last_seen_at bump never gets silently dropped on a retry (see
    store_new_postings).

    Looks up existing rows by the *batch's own* posting_keys rather than
    loading the whole postings table — the table only grows (9,810+ rows in
    a single live run already, per README) and this runs on every fast-lane
    (~2 min) and slow-lane (~15 min) tick, so an unscoped SELECT would get
    slower every cycle for no benefit. The batch lookup is index-backed and
    chunked so broad source coverage cannot exceed driver parameter limits."""
    batch_keys = [posting.posting_key for posting in postings]
    existing: list[PostingRow] = []
    # Broad category feeds can make one slow-lane sweep tens of thousands of
    # rows. Chunk the expanding IN predicate so provider growth cannot cross
    # PostgreSQL/driver bind-parameter limits and take down ingestion.
    for start in range(0, len(batch_keys), _POSTING_KEY_QUERY_BATCH_SIZE):
        key_chunk = batch_keys[start : start + _POSTING_KEY_QUERY_BATCH_SIZE]
        existing.extend(
            (
                await session.execute(
                    select(PostingRow).where(PostingRow.posting_key.in_(key_chunk))
                )
            )
            .scalars()
            .all()
        )
    existing_by_key = {row.posting_key: row for row in existing}

    for posting in postings:
        row = existing_by_key.get(posting.posting_key)
        if row is not None:
            row.last_seen_at = now
            # Staleness is an inference from not seeing a posting for several
            # sweeps, not a terminal state. If a source returns the posting
            # again, it is open by definition and must become eligible for
            # new-profile backfills and user-facing matches again. Confirmed
            # dead links use the separate terminal-ish "closed" state and are
            # deliberately not reopened here.
            if row.status == "stale":
                row.status = "open"

    new_postings = filter_new(postings, set(existing_by_key.keys()))
    new_rows: list[PostingRow] = []
    for posting in new_postings:
        row = _new_posting_row(posting, now)
        session.add(row)
        new_rows.append(row)
    return new_rows


async def store_new_postings(
    session: AsyncSession, postings: list[Posting]
) -> list[PostingRow]:
    """Insert postings not already present (by posting_key); update
    last_seen_at on ones already known. Returns the newly-inserted rows."""
    now = datetime.now(UTC)
    new_rows = await _insert_and_touch(session, postings, now)

    try:
        await session.flush()
    except IntegrityError:
        # Another lane (or a concurrent cycle) inserted one of these
        # posting_keys first — see spec: Scheduling, idempotency across
        # lanes. Roll back this batch and redo the *whole* pass (including
        # the last_seen_at bumps, not just the new-row insert) against
        # what's actually in the DB now, rather than crash the cycle or
        # silently lose this cycle's freshness bumps.
        await session.rollback()
        new_rows = await _insert_and_touch(session, postings, now)
        await session.flush()

    return new_rows


async def mark_stale_postings(session: AsyncSession) -> int:
    """Postings not re-seen for N consecutive cycles are inferred closed (see
    spec: Sources — Posting staleness). Uses a time-based proxy (N cycles ×
    the slow-lane interval) rather than an explicit per-cycle counter, since
    the cadence is fixed."""
    threshold = datetime.now(UTC) - timedelta(
        minutes=settings.slow_lane_interval_minutes * settings.stale_after_cycles
    )
    result = await session.execute(
        update(PostingRow)
        .where(PostingRow.status == "open", PostingRow.last_seen_at < threshold)
        .values(status="stale")
    )
    return result.rowcount or 0


def _criteria_row_to_domain(row: CriteriaRow) -> Criteria:
    return Criteria(
        user_id=row.user_id,
        role_types=tuple(RoleType(rt) for rt in row.role_types),
        target_fields=tuple(TargetField(field) for field in row.target_fields),
        keywords=tuple(row.keywords),
        locations=tuple(row.locations),
        sponsorship_required=row.sponsorship_required,
        min_date=row.min_date,
        freeform_notes=row.freeform_notes,
        resume_profile=row.resume_profile or {},
    )


def _posting_row_to_domain(row: PostingRow) -> Posting:
    return Posting(
        posting_key=row.posting_key,
        source=row.source,
        company=row.company,
        title=row.title,
        url=row.url,
        location=row.location,
        role_type=RoleType(row.role_type) if row.role_type else None,
        posted_at=row.posted_at,
        raw=row.raw,
        # Re-derived from the already-stored raw payload rather than a
        # dedicated column — no schema change needed, and it stays correct
        # even for postings stored before this field existed.
        description=extract_description(row.raw),
    )


# Bounds how many Claude ranking calls run at once across users in a single
# match cycle. Ranking is per-user I/O with no shared mutable state, so it's
# safe to run concurrently (see _rank_for_user); the semaphore just keeps a
# cycle with many active users from firing dozens of simultaneous requests at
# the Anthropic API, mirroring the bounded-concurrency pattern notify/dispatch.py
# already uses for outbound sends.
_RANK_CONCURRENCY = asyncio.Semaphore(5)


async def _rank_for_user(
    user: User,
    criteria: Criteria,
    survivors: list[Posting],
    rank_client: AnthropicClient,
) -> list:
    try:
        async with _RANK_CONCURRENCY:
            return await rank_postings(survivors, criteria, rank_client)
    except Exception:  # noqa: BLE001 - one user's bad state must not sink the whole cycle
        logger.warning(
            "match_new_postings: ranking failed for user_id=%s", user.id, exc_info=True
        )
        return []


# Bounds how many link-liveness checks run at once. Cheap relative to a
# ranking call (a streamed GET reading only the status line, see
# ingest/link_check.py) so this can run higher than _RANK_CONCURRENCY —
# checking is bounded by *new* postings this cycle, not (user, posting)
# pairs, since a dead link is dead for everyone.
_LINK_CHECK_CONCURRENCY = asyncio.Semaphore(20)
_LINK_CHECK_TIMEOUT_SECONDS = 8.0


async def _check_one_link(client: httpx.AsyncClient, row: PostingRow) -> bool | None:
    async with _LINK_CHECK_CONCURRENCY:
        return await check_link_alive(row.url, client)


async def _find_dead_links(rows: list[PostingRow]) -> set[str]:
    """Confirms which of this cycle's brand-new postings already have a
    dead link (an unambiguous 404/410/451) before anyone can be matched
    against them (see spec addendum: link validation — the concrete
    complaint was users seeing 'empty'/gone job postings in their digest).
    Checked once per posting here, not once per (user, posting) pair, since
    many users can share the same posting and a dead link is dead for all
    of them. Network errors, timeouts, and 5xx are inconclusive and never
    treated as dead (see ingest/link_check.py's fail-open philosophy)."""
    if not rows:
        return set()
    async with httpx.AsyncClient(
        timeout=_LINK_CHECK_TIMEOUT_SECONDS, follow_redirects=True
    ) as client:
        # return_exceptions so one unexpected failure can't cancel the whole
        # gather and cost this cycle its link validation entirely; anything
        # that did raise is inconclusive, i.e. not dead.
        results = await asyncio.gather(
            *(_check_one_link(client, row) for row in rows), return_exceptions=True
        )
    return {row.posting_key for row, alive in zip(rows, results) if alive is False}


async def match_new_postings(
    session: AsyncSession,
    new_postings: list[PostingRow],
    rank_client: AnthropicClient,
    lane: str = "slow",
    *,
    user_ids: list[int] | None = None,
    match_reason: str = "new_posting",
    validate_links: bool = True,
) -> MatchCycleResult:
    """Rule-filter every new posting against every active user's criteria,
    then batch survivors to Claude for scoring/blurb (see spec: Matching).
    Splits results into instant (high-priority) sends and digest items.

    The rule-filter pass is pure/cheap and runs sequentially; the one
    per-user Claude call this produces is real network I/O with no shared
    state between users, so those calls run concurrently (bounded by
    _RANK_CONCURRENCY) instead of one-at-a-time — a cycle with N active users
    no longer takes N sequential round-trips to Claude, which matters most on
    the fast lane's ~2 min budget."""
    if not new_postings:
        return MatchCycleResult(instant=[], digest_items=[])

    user_conditions = [
        User.opted_out.is_(False),
        # Legacy SMS users have no password hash and remain eligible exactly
        # as before. Web users wait until onboarding is complete.
        or_(User.password_hash.is_(None), User.profile_completed_at.is_not(None)),
    ]
    if user_ids is not None:
        user_conditions.append(User.id.in_(user_ids))

    users = (
        (
            await session.execute(
                select(User)
                .where(*user_conditions)
                .options(selectinload(User.criteria))
            )
        )
        .scalars()
        .all()
    )
    postings_domain = [_posting_row_to_domain(row) for row in new_postings]
    postings_by_key = {row.posting_key: row for row in new_postings}

    # Backfills deliberately pass already-stored postings. Some may already
    # have matched this user after onboarding but before the backfill job ran.
    # Remove those pairs before ranking, both to avoid wasted model calls and
    # to uphold uq_matches_user_posting without relying on a flush failure.
    existing_match_keys_by_user: dict[int, set[str]] = {}
    if users and new_postings:
        existing_pairs = (
            await session.execute(
                select(MatchRow.user_id, PostingRow.posting_key)
                .join(PostingRow, MatchRow.posting_id == PostingRow.id)
                .where(
                    MatchRow.user_id.in_([user.id for user in users]),
                    MatchRow.posting_id.in_([row.id for row in new_postings]),
                )
            )
        ).all()
        for matched_user_id, posting_key in existing_pairs:
            existing_match_keys_by_user.setdefault(matched_user_id, set()).add(
                posting_key
            )

    # Link validation is for postings entering the system for the first time
    # — one check per posting, before anyone can be matched against it. The
    # profile backfill re-reads the whole back catalogue instead, so checking
    # there would re-request tens of thousands of employer URLs every cycle
    # and still tell established users nothing new: their own matches were
    # never re-validated either. Callers that pass already-known rows opt out
    # (see backfill_completed_profiles); dead links remain excluded from the
    # dashboard by api/matches.py's status filter.
    dead_keys: set[str] = set()
    if validate_links:
        try:
            dead_keys = await _find_dead_links(new_postings)
        except Exception:  # noqa: BLE001 - link validation must never sink the whole cycle
            logger.warning(
                "match_new_postings: link validation failed unexpectedly", exc_info=True
            )
            dead_keys = set()
    if dead_keys:
        for row in new_postings:
            if row.posting_key in dead_keys:
                row.status = "closed"
        logger.info(
            "match_new_postings: %d posting(s) had a dead link (404/410/451) — excluded from matching",
            len(dead_keys),
        )
        postings_domain = [p for p in postings_domain if p.posting_key not in dead_keys]

    now = datetime.now(UTC)

    instant: list[tuple[User, MatchRow, PostingRow]] = []
    digest_items: list[DigestItem] = []
    failed_user_ids: set[int] = set()

    # Pass 1: rule-filter every user's criteria against this cycle's new
    # postings (pure, no I/O) and note which users have any survivors.
    candidates: list[tuple[User, Criteria, list[Posting]]] = []
    for user in users:
        try:
            criteria_row = user.criteria
            if criteria_row is None:
                continue
            criteria = _criteria_row_to_domain(criteria_row)
            already_matched = existing_match_keys_by_user.get(user.id, set())
            survivors = [
                posting
                for posting in filter_postings(postings_domain, criteria)
                if posting.posting_key not in already_matched
            ]
            if not survivors:
                continue
            candidates.append((user, criteria, survivors))
        except Exception:  # noqa: BLE001 - one user's bad state must not sink the whole cycle
            logger.warning(
                "match_new_postings: filtering failed for user_id=%s",
                user.id,
                exc_info=True,
            )
            continue

    if not candidates:
        return MatchCycleResult(instant=[], digest_items=[])

    # Pass 2: one watchlist query for every candidate user (instead of one
    # per user in a loop) and one concurrent batch of Claude calls.
    watched_by_user = await watchlisted_company_keys_by_user(
        session, [user.id for user, _criteria, _survivors in candidates]
    )
    rank_results_by_user = await asyncio.gather(
        *(
            _rank_for_user(user, criteria, survivors, rank_client)
            for user, criteria, survivors in candidates
        )
    )

    # Pass 3: turn each user's rank results into match rows against the
    # shared session — cheap, in-memory, sequential (AsyncSession isn't
    # safe to use concurrently).
    for (user, _criteria, survivors), rank_results in zip(
        candidates, rank_results_by_user
    ):
        if len({result.posting_key for result in rank_results}) < len(survivors):
            # The ranker contract asks for one result per posting. Missing
            # results normally mean a failed/truncated provider response. A
            # profile backfill uses this signal to leave its completion
            # marker unset and retry only the still-unmatched pairs later.
            failed_user_ids.add(user.id)
        watched_keys = watched_by_user.get(user.id, set())
        rank_by_key = {r.posting_key: r for r in rank_results}

        matches_for_digest: list[tuple[MatchRow, PostingRow]] = []
        for posting in survivors:
            rank_result = rank_by_key.get(posting.posting_key)
            if rank_result is None:
                # No score for this posting this cycle — either the LLM
                # call failed (rank_postings already logged it) or the
                # model just didn't return a result for this key. Leave
                # it unmatched rather than writing a permanent
                # score=0/blurb="" placeholder: the unique
                # (user_id, posting_id) constraint on `matches` would
                # otherwise block ever retrying it (see spec: Data
                # flow — "A failed LLM call ... remain unmatched ... and
                # are naturally re-evaluated next cycle").
                continue

            is_watched = normalize_company_key(posting.company) in watched_keys
            if not is_watched and rank_result.score < settings.min_match_score:
                # A real (not failed) score that's still a poor fit must
                # never become a stored match — without this gate, every
                # rule-filter survivor that got ranked at all (however weak
                # the fit, e.g. 0.05) was persisted and could reach a
                # digest, which is what actually made early digests feel
                # irrelevant. Watchlisted companies bypass this: the user
                # explicitly asked to hear about that company regardless of
                # score (see spec addendum: watchlist priority).
                continue

            posting_row = postings_by_key[posting.posting_key]
            priority = compute_priority(rank_result.score)
            if is_watched:
                # An explicit watchlist add is a stronger signal than the
                # score threshold — see spec addendum: watchlist priority.
                priority = Priority.HIGH

            match_row = MatchRow(
                user_id=user.id,
                posting_id=posting_row.id,
                score=rank_result.score,
                blurb=rank_result.blurb,
                priority=priority.value,
                lane=lane,
                match_reason="watchlist" if is_watched else match_reason,
                notified_channels=[],
                notified_at=None,
                matched_target_field=(
                    rank_result.target_field.value if rank_result.target_field else None
                ),
                created_at=now,
            )
            session.add(match_row)

            if priority == Priority.HIGH:
                instant.append((user, match_row, posting_row))
            else:
                matches_for_digest.append((match_row, posting_row))

        if matches_for_digest:
            digest_items.append(DigestItem(user=user, matches=matches_for_digest))

    await session.flush()
    return MatchCycleResult(
        instant=instant,
        digest_items=digest_items,
        failed_user_ids=failed_user_ids,
    )


async def backfill_completed_profiles(
    session: AsyncSession,
    rank_client: AnthropicClient,
) -> MatchCycleResult:
    """Evaluate completed profiles against the whole open corpus, one page
    per cycle.

    Normal scrape matching intentionally processes only newly inserted rows.
    That is efficient for established users, but it makes a profile's matches
    a function of *when it signed up* rather than what it asked for: two
    identical profiles created a month apart end up with wildly different
    match counts (observed in production: 2,487 vs 113 for comparable
    criteria), and everything already in the database at signup is invisible
    to the newer one.

    Earlier versions narrowed that gap with a one-time pass over the newest
    N postings per role type, but a bounded window can only ever be a smaller
    version of the same bug. Instead, each profile carries a cursor over
    postings.id (see User.initial_match_backfill_cursor) and advances through
    the entire open corpus a page at a time, completing only once the cursor
    exhausts it. From then on the fast/slow lanes keep it current, so the
    steady state is unchanged — the scan is what a profile pays once to catch
    up with everything that predates it.

    Time still matters, just not signup time: `posted_at` remains available
    for "new postings" filtering, and staleness/dead-link status decides what
    counts as open at all.
    """
    pending_users = (
        (
            await session.execute(
                select(User)
                .where(
                    User.password_hash.is_not(None),
                    User.profile_completed_at.is_not(None),
                    User.initial_match_backfill_version < _PROFILE_BACKFILL_VERSION,
                    User.opted_out.is_(False),
                )
                .options(selectinload(User.criteria))
                .order_by(User.profile_completed_at.asc(), User.id.asc())
                .limit(settings.profile_backfill_max_users_per_cycle)
            )
        )
        .scalars()
        .all()
    )
    if not pending_users:
        return MatchCycleResult(instant=[], digest_items=[])

    completed_at = datetime.now(UTC)
    instant: list[tuple[User, MatchRow, PostingRow]] = []
    digest_items: list[DigestItem] = []
    failed_user_ids: set[int] = set()

    page_size = settings.profile_backfill_max_postings_per_user
    for user in pending_users:
        # Each profile walks its own cursor, so profiles that joined at
        # different times can be mid-scan at different offsets without
        # interfering. Ordered by id (monotonic per insert) rather than
        # posted_at, which is frequently null and not unique — the scan needs
        # a total order it can resume from, not a relevance order, since
        # every page is reached eventually either way.
        page_conditions = [
            PostingRow.status == "open",
            PostingRow.id > user.initial_match_backfill_cursor,
        ]
        # Mirror matching.filters' role-type rule in SQL so pages aren't
        # mostly rows the rule filter is about to discard — most of the
        # corpus is neither new-grad nor internship, and scanning it anyway
        # multiplies how long a new profile waits for its first matches.
        # Empty role_types means "no constraint" there, so it must mean the
        # same here; an IN clause also excludes unclassified (NULL) rows,
        # which is what the filter does when role types are set.
        requested_role_types = list(
            user.criteria.role_types or [] if user.criteria is not None else []
        )
        if requested_role_types:
            page_conditions.append(PostingRow.role_type.in_(requested_role_types))

        page = (
            (
                await session.execute(
                    select(PostingRow)
                    .where(*page_conditions)
                    .order_by(PostingRow.id.asc())
                    .limit(page_size)
                )
            )
            .scalars()
            .all()
        )

        if not page:
            # Cursor exhausted. An empty first page also covers the
            # fresh-deployment case where no scrape has landed yet: the
            # profile is only marked complete once inventory exists to have
            # been considered, so an empty database leaves it pending.
            if await _open_corpus_is_empty(session):
                failed_user_ids.add(user.id)
                logger.warning(
                    "profile backfill: no open postings yet; leaving user_id=%s pending",
                    user.id,
                )
                continue
            user.initial_matches_generated_at = completed_at
            user.initial_match_backfill_version = _PROFILE_BACKFILL_VERSION
            logger.info(
                "profile backfill: user_id=%s complete — whole open corpus considered",
                user.id,
            )
            continue

        page_result = await match_new_postings(
            session,
            list(page),
            rank_client,
            lane="backfill",
            user_ids=[user.id],
            match_reason="profile_backfill",
            validate_links=False,
        )
        instant.extend(page_result.instant)
        digest_items.extend(page_result.digest_items)

        failed = user.id in page_result.failed_user_ids
        if failed:
            failed_user_ids.add(user.id)
            user.initial_match_backfill_attempts += 1
            if user.initial_match_backfill_attempts < _MAX_BACKFILL_ATTEMPTS:
                # Leave the cursor where it is so the same page is retried.
                continue
            # A posting the ranker never returns a result for would otherwise
            # pin the cursor here forever, stalling the rest of the scan.
            # Skip the page and keep going: those postings stay unmatched,
            # exactly as a below-threshold score would.
            logger.warning(
                "profile backfill: user_id=%s skipping page ending at posting_id=%d "
                "after %d incomplete attempts — keeping the matches that did rank",
                user.id,
                page[-1].id,
                _MAX_BACKFILL_ATTEMPTS,
            )

        # Page dealt with (fully ranked, or given up on): advance past it and
        # restore the budget, so attempts counts consecutive failures on the
        # *current* page rather than accumulating across a long healthy scan.
        user.initial_match_backfill_cursor = page[-1].id
        user.initial_match_backfill_attempts = 0

        if len(page) < page_size:
            # A short page is the end of the corpus — no need to spend
            # another cycle proving the next one is empty. Anything ingested
            # after this point is new, which is the fast/slow lanes' job.
            user.initial_matches_generated_at = completed_at
            user.initial_match_backfill_version = _PROFILE_BACKFILL_VERSION
            logger.info(
                "profile backfill: user_id=%s complete — whole open corpus considered",
                user.id,
            )

    await session.flush()
    return MatchCycleResult(
        instant=instant, digest_items=digest_items, failed_user_ids=failed_user_ids
    )


async def _open_corpus_is_empty(session: AsyncSession) -> bool:
    row = (
        await session.execute(
            select(PostingRow.id).where(PostingRow.status == "open").limit(1)
        )
    ).first()
    return row is None


async def gather_pending_digests(
    session: AsyncSession,
    channel: str | None = None,
    *,
    email_due_time: str | None = None,
    email_digest_date: date | None = None,
) -> list[DigestItem]:
    """Collect every un-sent normal-priority match, from either lane, grouped
    by user — the digest cycle's own read, decoupled from whatever cadence
    produced the matches (see spec: Data flow — Digest cycle)."""
    conditions = [User.opted_out.is_(False)]
    email_user_conditions = [
        User.email.is_not(None),
        User.email_digest_enabled.is_(True),
        User.profile_completed_at.is_not(None),
    ]
    if email_due_time is not None:
        email_user_conditions.append(User.email_digest_time <= email_due_time)
    if email_digest_date is not None:
        email_user_conditions.append(
            or_(
                User.last_email_digest_sent_on.is_(None),
                User.last_email_digest_sent_on < email_digest_date,
            )
        )
    if channel is None:
        conditions.extend(
            [MatchRow.priority == Priority.NORMAL.value, MatchRow.notified_at.is_(None)]
        )
    elif channel == "sms":
        conditions.extend(
            [MatchRow.priority == Priority.NORMAL.value, User.phone.like("+%")]
        )
    elif channel == "email":
        conditions.extend(email_user_conditions)
        # SMS STOP currently sets opted_out. It should pause matching as it
        # always has, but an already-matched posting is still eligible for a
        # user-requested email digest, so remove the SMS-only opt-out filter.
        conditions = conditions[1:]
    else:
        raise ValueError(f"unsupported digest channel: {channel}")

    rows = (
        await session.execute(
            select(MatchRow, PostingRow, User)
            .join(PostingRow, MatchRow.posting_id == PostingRow.id)
            .join(User, MatchRow.user_id == User.id)
            .where(*conditions)
            .order_by(MatchRow.created_at, MatchRow.id)
        )
    ).all()

    by_user: dict[int, DigestItem] = {}
    for match_row, posting_row, user in rows:
        if channel is not None and channel in (match_row.notified_channels or []):
            continue
        item = by_user.get(user.id)
        if item is None:
            item = DigestItem(user=user, matches=[])
            by_user[user.id] = item
        item.matches.append((match_row, posting_row))

    if channel == "email":
        # The product promise is one daily email for every completed profile,
        # including a concise no-new-matches note on quiet days. The joined
        # query above cannot return users with zero pending rows, so union in
        # all eligible recipients after grouping the real matches.
        eligible_users = (
            (await session.execute(select(User).where(*email_user_conditions)))
            .scalars()
            .all()
        )
        for user in eligible_users:
            by_user.setdefault(user.id, DigestItem(user=user, matches=[]))

    return list(by_user.values())


async def gather_previously_sent_email_matches(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, list[tuple[MatchRow, PostingRow]]]:
    """Matches already delivered by email before, for users about to get
    another one — the "For You" pool (see spec addendum: email digest
    sections, notify/curate.py::curate_two_section_digest). Excludes
    postings link validation has since marked closed, and bounded to the
    last `digest_for_you_max_age_days` so this doesn't grow into resurfacing
    arbitrarily old matches forever as a user's history accumulates.

    Mutually exclusive with gather_pending_digests(channel="email")'s
    result by construction: a match is either not yet emailed (pending) or
    already emailed (this function), never both.
    """
    if not user_ids:
        return {}
    cutoff = datetime.now(UTC) - timedelta(days=settings.digest_for_you_max_age_days)
    rows = (
        await session.execute(
            select(MatchRow, PostingRow)
            .join(PostingRow, MatchRow.posting_id == PostingRow.id)
            .where(
                MatchRow.user_id.in_(user_ids),
                PostingRow.status != "closed",
                MatchRow.created_at >= cutoff,
            )
        )
    ).all()
    by_user: dict[int, list[tuple[MatchRow, PostingRow]]] = {
        user_id: [] for user_id in user_ids
    }
    for match_row, posting_row in rows:
        if "email" in (match_row.notified_channels or []):
            by_user[match_row.user_id].append((match_row, posting_row))
    return by_user
