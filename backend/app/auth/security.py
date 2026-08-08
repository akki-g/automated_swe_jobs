from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Annotated

import jwt
from fastapi import Cookie, Depends, Header, HTTPException, Request, Response, status
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import User
from app.db.session import SessionLocal

SESSION_COOKIE = "jobs_session"
CSRF_COOKIE = "jobs_csrf"
_ALGORITHM = "HS256"
_password_hash = PasswordHash.recommended()


async def get_db_session():
    async with SessionLocal() as session:
        yield session


def hash_password(password: str) -> str:
    return _password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return _password_hash.verify(password, encoded)


def _encode_session(user_id: int, csrf_token: str) -> str:
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "csrf": csrf_token,
        "iat": now,
        "exp": now + timedelta(hours=settings.auth_session_hours),
        "type": "session",
    }
    return jwt.encode(payload, settings.auth_secret, algorithm=_ALGORITHM)


def set_auth_cookies(response: Response, user_id: int) -> None:
    csrf_token = secrets.token_urlsafe(32)
    max_age = settings.auth_session_hours * 60 * 60
    response.set_cookie(
        SESSION_COOKIE,
        _encode_session(user_id, csrf_token),
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf_token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=max_age,
        path="/",
    )


def clear_auth_cookies(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


def _decode_session(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.auth_secret, algorithms=[_ALGORITHM])
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    if payload.get("type") != "session" or not payload.get("sub"):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    return payload


async def get_current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    jobs_session: Annotated[str | None, Cookie()] = None,
) -> User:
    if not jobs_session:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    payload = _decode_session(jobs_session)
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session") from exc
    user = (await session.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid session")
    request.state.auth_payload = payload
    return user


async def require_csrf(
    request: Request,
    _user: Annotated[User, Depends(get_current_user)],
    csrf_cookie: Annotated[str | None, Cookie(alias=CSRF_COOKIE)] = None,
    csrf_header: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    token_csrf = getattr(request.state, "auth_payload", {}).get("csrf")
    if not csrf_cookie or not csrf_header or not token_csrf:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
    if not (
        secrets.compare_digest(csrf_cookie, csrf_header)
        and secrets.compare_digest(csrf_cookie, str(token_csrf))
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="CSRF validation failed")
