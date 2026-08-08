from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import get_current_user, get_db_session, require_csrf
from app.db.models import Criteria, User
from app.resume.extract import (
    ClaudeResumeClient,
    ResumeExtractionClient,
    extract_resume_profile,
)
from app.resume.parse import MAX_RESUME_BYTES, ResumeParseError, extract_resume_text

router = APIRouter(prefix="/api/profile", tags=["profile"])


def get_resume_client() -> ResumeExtractionClient:
    return ClaudeResumeClient()


@router.post("/resume", dependencies=[Depends(require_csrf)])
async def upload_resume(
    file: Annotated[UploadFile, File()],
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    client: Annotated[ResumeExtractionClient, Depends(get_resume_client)],
) -> dict:
    data = await file.read(MAX_RESUME_BYTES + 1)
    await file.close()
    try:
        text = await anyio.to_thread.run_sync(extract_resume_text, data, file.filename or "")
        profile = await extract_resume_profile(text, client)
    except ResumeParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Resume analysis is temporarily unavailable") from exc

    criteria = (
        await session.execute(select(Criteria).where(Criteria.user_id == user.id))
    ).scalar_one_or_none()
    if criteria is None:
        criteria = Criteria(user_id=user.id, updated_at=datetime.now(UTC))
        session.add(criteria)

    now = datetime.now(UTC)
    criteria.resume_profile = profile.to_dict()
    criteria.resume_updated_at = now
    criteria.updated_at = now
    await session.commit()
    return {"resume_profile": profile.to_dict(), "resume_updated_at": now}
