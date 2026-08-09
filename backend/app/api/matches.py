from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import get_current_user, get_db_session, require_csrf
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User

router = APIRouter(prefix="/api/matches", tags=["matches"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


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

    `is_new` / `new_only` are both computed against matches_last_viewed_at
    as it was *before* this call — read that value first, then update it to
    now at the end, so this call's own results aren't retroactively marked
    "not new" by its own bookkeeping.
    """
    previous_viewed_at = user.matches_last_viewed_at

    conditions = [MatchRow.user_id == user.id]
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
            base_query.order_by(MatchRow.created_at.desc(), MatchRow.id.desc())
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
            is_new=previous_viewed_at is None or match_row.created_at > previous_viewed_at,
        )
        for match_row, posting_row in rows
    ]

    user.matches_last_viewed_at = datetime.now(UTC)
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
