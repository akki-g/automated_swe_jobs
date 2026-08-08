from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime
from typing import Annotated, Literal

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    clear_auth_cookies,
    get_current_user,
    get_db_session,
    hash_password,
    set_auth_cookies,
    verify_password,
)
from app.db.models import Criteria, User

router = APIRouter(prefix="/api/auth", tags=["auth"])

Password = Annotated[str, StringConstraints(min_length=10, max_length=128)]
_PHONE_PATTERN = re.compile(r"^\+[1-9]\d{7,14}$")


class SignupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: Password
    phone: str | None = Field(default=None, max_length=20)
    consent: Literal[True]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: int
    name: str
    email: str
    phone: str | None
    profile_completed: bool
    email_digest_enabled: bool


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email or "",
        phone=user.phone if user.phone and _PHONE_PATTERN.fullmatch(user.phone) else None,
        profile_completed=user.profile_completed_at is not None,
        email_digest_enabled=user.email_digest_enabled,
    )


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    body: SignupRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    email = str(body.email).strip().lower()
    phone = body.phone.strip() if body.phone else f"web:{secrets.token_hex(8)}"
    if body.phone and not _PHONE_PATTERN.fullmatch(phone):
        raise HTTPException(status_code=422, detail="Phone must use E.164 format, such as +15551234567")

    now = datetime.now(UTC)
    user = User(
        name=body.name.strip(),
        email=email,
        phone=phone,
        password_hash=await anyio.to_thread.run_sync(hash_password, body.password),
        sms_provider="signalwire",
        opted_out=False,
        consent_at=now,
        consent_method="web-signup-terms-v1",
        profile_completed_at=None,
        email_digest_enabled=True,
        created_at=now,
    )
    session.add(user)
    try:
        await session.flush()
        session.add(Criteria(user_id=user.id, updated_at=now))
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="An account already exists for that email or phone") from exc

    set_auth_cookies(response, user.id)
    return _user_response(user)


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    response: Response,
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> UserResponse:
    email = str(body.email).strip().lower()
    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    password_valid = bool(
        user
        and user.password_hash
        and await anyio.to_thread.run_sync(verify_password, body.password, user.password_hash)
    )
    if user is None or not password_valid:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    set_auth_cookies(response, user.id)
    return _user_response(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    clear_auth_cookies(response)


@router.get("/me", response_model=UserResponse)
async def me(user: Annotated[User, Depends(get_current_user)]) -> UserResponse:
    return _user_response(user)
