from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import zipfile
from dataclasses import dataclass
from typing import Annotated

import anyio
from fastapi import APIRouter, Depends, Form, HTTPException, Response, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import get_current_user, get_db_session, require_csrf
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from app.resume.compile_pdf import LatexCompileError, compile_latex_to_pdf
from app.resume.latex_template import render_latex
from app.resume.parse import MAX_RESUME_BYTES, ResumeParseError, extract_resume_text
from app.resume.tailor import ClaudeTailorClient, TailorClient, tailor_resume_content

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/resume", tags=["resume"])

MAX_POSTINGS_PER_REQUEST = 10

# Each job in a tailoring request is an independent Claude call + LaTeX
# compile with no shared state — safe to run concurrently. Bounded so a
# request selecting many jobs at once doesn't fire an unbounded number of
# simultaneous Claude calls / tectonic subprocesses.
_TAILOR_CONCURRENCY = asyncio.Semaphore(3)


def get_tailor_client() -> TailorClient:
    return ClaudeTailorClient()


@dataclass
class _JobResult:
    posting_id: int
    company: str
    title: str
    status: str  # "ok" | "failed"
    pdf_bytes: bytes | None = None
    error: str | None = None


def _safe_filename(company: str, title: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "_", f"{company}_{title}").strip("_")
    return f"{slug or 'resume'}.pdf"


async def _build_one(
    resume_text: str, contact_name: str, posting_row: PostingRow, client: TailorClient
) -> _JobResult:
    async with _TAILOR_CONCURRENCY:
        try:
            content = await tailor_resume_content(
                resume_text,
                company=posting_row.company,
                title=posting_row.title,
                location=posting_row.location,
                client=client,
            )
            tex = render_latex(content, contact_name=contact_name)
            pdf_bytes = await compile_latex_to_pdf(tex)
        except LatexCompileError:
            logger.warning("resume tailoring: LaTeX compile failed for posting_id=%s", posting_row.id, exc_info=True)
            return _JobResult(
                posting_id=posting_row.id,
                company=posting_row.company,
                title=posting_row.title,
                status="failed",
                error="Could not format the tailored resume for this posting",
            )
        except Exception:  # noqa: BLE001 - one job's failure must not sink the whole request
            logger.warning("resume tailoring: failed for posting_id=%s", posting_row.id, exc_info=True)
            return _JobResult(
                posting_id=posting_row.id,
                company=posting_row.company,
                title=posting_row.title,
                status="failed",
                error="Could not generate a tailored resume for this posting",
            )
    return _JobResult(
        posting_id=posting_row.id,
        company=posting_row.company,
        title=posting_row.title,
        status="ok",
        pdf_bytes=pdf_bytes,
    )


@router.post("/tailor", dependencies=[Depends(require_csrf)])
async def tailor_resume(
    user: Annotated[User, Depends(get_current_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    client: Annotated[TailorClient, Depends(get_tailor_client)],
    file: UploadFile,
    posting_ids: Annotated[list[int], Form()],
) -> Response:
    """One tailored PDF per selected posting — never the same document
    blended across postings (see spec: Resume tailoring, selection
    semantics). The uploaded resume is parsed to text in memory for this
    request only and is never persisted, matching the profile-resume
    upload's minimization policy."""
    posting_ids = list(dict.fromkeys(posting_ids))[:MAX_POSTINGS_PER_REQUEST]
    if not posting_ids:
        raise HTTPException(status_code=422, detail="Select at least one posting")

    data = await file.read(MAX_RESUME_BYTES + 1)
    await file.close()
    try:
        resume_text = await anyio.to_thread.run_sync(extract_resume_text, data, file.filename or "")
    except ResumeParseError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    rows = (
        await session.execute(
            select(MatchRow, PostingRow)
            .join(PostingRow, MatchRow.posting_id == PostingRow.id)
            .where(MatchRow.user_id == user.id, MatchRow.posting_id.in_(posting_ids))
        )
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="None of the selected postings were found")

    posting_rows = [posting_row for _match_row, posting_row in rows]
    results = await asyncio.gather(
        *(_build_one(resume_text, user.name, posting_row, client) for posting_row in posting_rows)
    )

    if len(results) == 1:
        only = results[0]
        if only.status == "failed":
            raise HTTPException(status_code=502, detail=only.error)
        return Response(
            content=only.pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="{_safe_filename(only.company, only.title)}"'
            },
        )

    buffer = io.BytesIO()
    manifest = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        used_names: set[str] = set()
        for result in results:
            manifest.append(
                {
                    "posting_id": result.posting_id,
                    "company": result.company,
                    "title": result.title,
                    "status": result.status,
                    "error": result.error,
                }
            )
            if result.status != "ok":
                continue
            name = _safe_filename(result.company, result.title)
            while name in used_names:
                name = f"{result.posting_id}_{name}"
            used_names.add(name)
            archive.writestr(name, result.pdf_bytes or b"")
        archive.writestr("results.json", json.dumps(manifest, indent=2))

    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="tailored_resumes.zip"'},
    )
