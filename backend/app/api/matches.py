from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import get_current_user, get_db_session, require_csrf
from app.config import settings
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.notify.curate import curate_matches
from app.notify.dispatch import matches_url
from app.notify.email_resend import ResendEmailProvider
from app.notify.email_template import render_digest_email

router = APIRouter(prefix="/api/matches", tags=["matches"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200

# How long one "visit" to the matches page lasts for the purposes of
# matches_last_viewed_at. Every request inside this window compares against
# the same watermark, so browsing and filtering don't erase the user's own
# "New" markers; the first request after it re-baselines.
_VISIT_WINDOW = timedelta(minutes=30)


def _as_utc(value: datetime | None) -> datetime | None:
    """Treat a stored datetime as UTC-aware for Python-side comparisons.

    PostgreSQL returns these aware (TIMESTAMPTZ), SQLite returns them naive
    — its DATETIME type doesn't round-trip tzinfo — so comparing one against
    datetime.now(UTC) raises on SQLite and mixing the two is exactly the
    class of bug this endpoint already hit in production. Every writer in
    this codebase uses datetime.now(UTC), so a naive value is UTC.
    """
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class MatchItem(BaseModel):
    id: int
    company: str
    title: str
    url: str
    location: str | None
    role_type: str | None
    posted_at: datetime | None
    score: float
    blurb: str
    priority: str
    matched_target_field: str | None
    saved: bool
    created_at: datetime
    is_new: bool


class MatchListResponse(BaseModel):
    items: list[MatchItem]
    total: int


class SaveRequest(BaseModel):
    saved: bool


class SaveResponse(BaseModel):
    id: int
    saved: bool


class ResendEmailResponse(BaseModel):
    sent: bool
    match_count: int


def get_email_provider() -> ResendEmailProvider:
    return ResendEmailProvider()


@router.get("", response_model=MatchListResponse)
async def list_matches(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    company: str | None = None,
    location: str | None = None,
    target_field: str | None = None,
    priority: str | None = None,
    min_score: Annotated[float | None, Query(ge=0.0, le=1.0)] = None,
    since: datetime | None = None,
    until: datetime | None = None,
    saved: bool | None = None,
    new_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MatchListResponse:
    """The signed-in user's own matches, joined with their posting, filtered
    and paginated (see spec: matches page). Only ever reads WHERE user_id =
    the current user — there is no cross-user access path here.

    `is_new` / `new_only` are both computed against a baseline that is fixed
    for the whole *visit*, not recomputed per request. The matches page
    re-fetches on every filter change, so a per-request watermark made the
    "New" badges vanish the moment the user touched a filter, and left
    `new_only=true` structurally unable to match anything: opening the page
    advanced the watermark to now, and ticking the box was the very next
    request.

    Two timestamps make that work. `matches_visit_started_at` marks when the
    current visit began, and a request more than _VISIT_WINDOW after it
    starts a new one. `matches_last_viewed_at` is the comparison baseline
    and only moves at a visit boundary, to where the *previous* visit began
    — so everything that arrived since the user was last here reads as new,
    for every request of this visit.
    """
    now = datetime.now(UTC)
    visit_started_at = user.matches_visit_started_at
    is_new_visit = (
        _as_utc(visit_started_at) is None or now - _as_utc(visit_started_at) > _VISIT_WINDOW
    )
    # Stored form feeds the SQL predicate below (SQLite persists these naive,
    # and an aware bind parameter would not compare equal there); the
    # normalised copy feeds the Python-side comparisons.
    previous_viewed_at = visit_started_at if is_new_visit else user.matches_last_viewed_at
    previous_viewed_utc = _as_utc(previous_viewed_at)

    conditions = [
        MatchRow.user_id == user.id,
        # A posting link-validation already confirmed dead (see
        # ingest/link_check.py / pipeline.match_new_postings) must not
        # keep showing up as a "match" here just because the row predates
        # that check — the whole point of validating links is that a dead
        # one is never presented as available.
        PostingRow.status != "closed",
    ]
    if company:
        conditions.append(PostingRow.company.ilike(f"%{company}%"))
    if location:
        conditions.append(PostingRow.location.ilike(f"%{location}%"))
    if target_field:
        conditions.append(MatchRow.matched_target_field == target_field)
    if priority:
        conditions.append(MatchRow.priority == priority)
    if min_score is not None:
        conditions.append(MatchRow.score >= min_score)
    if since is not None:
        conditions.append(MatchRow.created_at >= since)
    if until is not None:
        conditions.append(MatchRow.created_at <= until)
    if saved is not None:
        conditions.append(MatchRow.saved == saved)
    if new_only and previous_viewed_at is not None:
        conditions.append(MatchRow.created_at > previous_viewed_at)

    base_query = (
        select(MatchRow, PostingRow)
        .join(PostingRow, MatchRow.posting_id == PostingRow.id)
        .where(*conditions)
    )

    total = (
        await session.execute(select(func.count()).select_from(base_query.subquery()))
    ).scalar_one()

    rows = (
        await session.execute(
            # Relevance-first by default (score desc) rather than newest-first
            # — a mediocre new match shouldn't bury a great older one. Filters
            # above are unaffected; this only changes default ordering within
            # whatever set of matches survives them.
            base_query.order_by(MatchRow.score.desc(), MatchRow.created_at.desc(), MatchRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()

    items = [
        MatchItem(
            id=match_row.id,
            company=posting_row.company,
            title=posting_row.title,
            url=posting_row.url,
            location=posting_row.location,
            role_type=posting_row.role_type,
            posted_at=posting_row.posted_at,
            score=match_row.score,
            blurb=match_row.blurb,
            priority=match_row.priority,
            matched_target_field=match_row.matched_target_field,
            saved=match_row.saved,
            created_at=match_row.created_at,
            is_new=(
                previous_viewed_utc is None
                or _as_utc(match_row.created_at) > previous_viewed_utc
            ),
        )
        for match_row, posting_row in rows
    ]

    if is_new_visit:
        # Only at a visit boundary: carry the baseline forward to where the
        # previous visit began, and open a new one. Requests within this
        # visit leave both untouched, so their results stay consistent.
        user.matches_last_viewed_at = previous_viewed_at
        user.matches_visit_started_at = now
        await session.commit()

    return MatchListResponse(items=items, total=total)


@router.post("/{match_id}/save", response_model=SaveResponse, dependencies=[Depends(require_csrf)])
async def save_match(
    match_id: int,
    body: SaveRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SaveResponse:
    match_row = (
        await session.execute(
            select(MatchRow).where(MatchRow.id == match_id, MatchRow.user_id == user.id)
        )
    ).scalar_one_or_none()
    if match_row is None:
        raise HTTPException(status_code=404, detail="Match not found")
    match_row.saved = body.saved
    await session.commit()
    return SaveResponse(id=match_row.id, saved=match_row.saved)


@router.post("/resend-email", response_model=ResendEmailResponse, dependencies=[Depends(require_csrf)])
async def resend_email(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    email_provider: Annotated[ResendEmailProvider, Depends(get_email_provider)],
) -> ResendEmailResponse:
    """On-demand "send me my matches now", triggered explicitly from the
    dashboard. Deliberately independent of the automated daily_email_cycle:
    it ignores email_digest_enabled (a manual action shouldn't be blocked by
    the automatic-subscription toggle) and never touches
    last_email_digest_sent_on or notified_channels, so pressing this can
    never cause the automated pipeline to skip or double-send anything it
    still owes the user — this is a separate action, not a substitute.
    """
    if not user.email:
        raise HTTPException(status_code=422, detail="Add an email address to your profile first")

    rows = (
        await session.execute(
            select(MatchRow, PostingRow)
            .join(PostingRow, MatchRow.posting_id == PostingRow.id)
            .where(MatchRow.user_id == user.id, PostingRow.status != "closed")
            .order_by(MatchRow.score.desc(), MatchRow.created_at.desc())
            .limit(200)
        )
    ).all()
    matches = [(match_row, posting_row) for match_row, posting_row in rows]

    curated = curate_matches(
        matches,
        overall_cap=settings.digest_max_email_matches,
        per_company_cap=settings.digest_max_per_company,
    )
    text, html = render_digest_email(user.name, curated, matches_url())
    success = await email_provider.send(user.email, "Your job matches", text, html=html)
    if not success:
        raise HTTPException(status_code=502, detail="Could not send the email right now — try again shortly")
    return ResendEmailResponse(sent=True, match_count=len(curated))
