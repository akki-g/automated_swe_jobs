from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import get_current_user, get_db_session, require_csrf
from app.db.models import Criteria, User
from app.domain.models import RoleType, TargetField

router = APIRouter(prefix="/api/profile", tags=["profile"])


def _normalize_digest_time(value: str) -> str:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("Email delivery time must use HH:MM format") from exc
    if parsed.second or parsed.microsecond:
        raise ValueError("Email delivery time must use HH:MM format")
    return parsed.strftime("%H:%M")


def _clean_list(values: list[str], *, max_items: int, max_length: int) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values[:max_items]:
        item = value.strip()
        key = item.casefold()
        if item and len(item) <= max_length and key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


class ProfileUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    role_types: list[RoleType] = Field(max_length=2)
    target_fields: list[TargetField] = Field(max_length=9)
    keywords: list[str] = Field(default_factory=list, max_length=30)
    locations: list[str] = Field(default_factory=list, max_length=20)
    sponsorship_required: bool | None = None
    freeform_notes: str = Field(default="", max_length=2000)
    email_digest_enabled: bool = True
    email_digest_time: str = "08:00"
    mark_complete: bool = False

    @field_validator("email_digest_time")
    @classmethod
    def validate_email_digest_time(cls, value: str) -> str:
        return _normalize_digest_time(value)

    @field_validator("keywords")
    @classmethod
    def validate_keywords(cls, values: list[str]) -> list[str]:
        return _clean_list(values, max_items=30, max_length=80)

    @field_validator("locations")
    @classmethod
    def validate_locations(cls, values: list[str]) -> list[str]:
        return _clean_list(values, max_items=20, max_length=120)


class ProfileResponse(BaseModel):
    name: str
    email: str
    role_types: list[RoleType]
    target_fields: list[TargetField]
    keywords: list[str]
    locations: list[str]
    sponsorship_required: bool | None
    freeform_notes: str
    resume_profile: dict
    resume_updated_at: datetime | None
    email_digest_enabled: bool
    email_digest_time: str
    profile_completed: bool


class EmailSettingsUpdate(BaseModel):
    email_digest_enabled: bool
    email_digest_time: str

    @field_validator("email_digest_time")
    @classmethod
    def validate_email_digest_time(cls, value: str) -> str:
        return _normalize_digest_time(value)


def _response(user: User, criteria: Criteria) -> ProfileResponse:
    return ProfileResponse(
        name=user.name,
        email=user.email or "",
        role_types=[RoleType(value) for value in (criteria.role_types or [])],
        target_fields=[TargetField(value) for value in (criteria.target_fields or [])],
        keywords=list(criteria.keywords or []),
        locations=list(criteria.locations or []),
        sponsorship_required=criteria.sponsorship_required,
        freeform_notes=criteria.freeform_notes or "",
        resume_profile=criteria.resume_profile or {},
        resume_updated_at=criteria.resume_updated_at,
        email_digest_enabled=user.email_digest_enabled,
        email_digest_time=user.email_digest_time,
        profile_completed=user.profile_completed_at is not None,
    )


async def _get_criteria(session: AsyncSession, user_id: int) -> Criteria:
    criteria = (
        await session.execute(select(Criteria).where(Criteria.user_id == user_id))
    ).scalar_one_or_none()
    if criteria is None:
        criteria = Criteria(user_id=user_id, updated_at=datetime.now(UTC))
        session.add(criteria)
        await session.flush()
    return criteria


@router.get("", response_model=ProfileResponse)
async def get_profile(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProfileResponse:
    criteria = await _get_criteria(session, user.id)
    return _response(user, criteria)


@router.put("", response_model=ProfileResponse, dependencies=[Depends(require_csrf)])
async def update_profile(
    body: ProfileUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProfileResponse:
    if body.mark_complete and (not body.role_types or not body.target_fields):
        raise HTTPException(
            status_code=422,
            detail="Choose at least one opportunity type and one target field to complete your profile",
        )
    criteria = await _get_criteria(session, user.id)
    now = datetime.now(UTC)
    user.name = body.name.strip()
    user.email_digest_enabled = body.email_digest_enabled
    user.email_digest_time = body.email_digest_time
    if body.mark_complete:
        user.profile_completed_at = user.profile_completed_at or now
    if user.profile_completed_at is not None:
        # Criteria edits can make previously-irrelevant open jobs newly
        # relevant. Re-run the bounded inventory backfill; existing match
        # pairs are excluded by the pipeline, so this only adds newly fitting
        # rows rather than duplicating prior results.
        user.initial_matches_generated_at = None
    criteria.role_types = [value.value for value in body.role_types]
    criteria.target_fields = [value.value for value in body.target_fields]
    criteria.keywords = body.keywords
    criteria.locations = body.locations
    criteria.sponsorship_required = body.sponsorship_required
    criteria.freeform_notes = body.freeform_notes.strip()
    criteria.updated_at = now
    await session.commit()
    return _response(user, criteria)


@router.put(
    "/settings", response_model=ProfileResponse, dependencies=[Depends(require_csrf)]
)
async def update_email_settings(
    body: EmailSettingsUpdate,
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> ProfileResponse:
    criteria = await _get_criteria(session, user.id)
    user.email_digest_enabled = body.email_digest_enabled
    user.email_digest_time = body.email_digest_time
    await session.commit()
    return _response(user, criteria)
