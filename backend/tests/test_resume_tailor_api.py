from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import UTC, datetime

import httpx
import pytest
from docx import Document
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.api.resume_tailor import get_tailor_client
from app.api.resume_tailor import router as resume_tailor_router
from app.db.models import Base
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User
from sqlalchemy import select

PARSED_RESUME = {
    "name": "Alex",
    "contact": {"email": "alex@example.com", "phone": "", "location": "", "links": []},
    "sections": [
        {
            "heading": "Experience",
            "kind": "experience",
            "raw_text": "SWE Intern, Acme, 2025. Did a thing.",
            "items": [],
            "entries": [],
        }
    ],
    "unassigned_text": [],
}

TAILORED_RESUME = {
    "headline": "Backend Software Engineer",
    "contact": {"email": "alex@example.com", "phone": "", "location": "", "links": []},
    "sections": [
        {
            "heading": "Experience",
            "kind": "experience",
            "intro": "",
            "items": [],
            "entries": [
                {
                    "title": "SWE Intern",
                    "subtitle": "Acme",
                    "location": "",
                    "dates": "2025",
                    "body": "",
                    "bullets": ["Did a thing"],
                }
            ],
        }
    ],
}


class FakeTailorClient:
    def __init__(self, fail_for_company: str | None = None) -> None:
        self.fail_for_company = fail_for_company
        self.calls: list[str] = []
        self.parse_calls = 0
        self.parse_delay = 0.0

    async def parse_resume(self, resume_text: str):
        self.parse_calls += 1
        if self.parse_delay:
            await asyncio.sleep(self.parse_delay)
        return PARSED_RESUME

    async def rewrite_resume(
        self, parsed_resume, *, company, title, location, description, job_url
    ):
        self.calls.append(company)
        if self.fail_for_company and self.fail_for_company == company:
            raise RuntimeError("simulated failure")
        return TAILORED_RESUME


def _resume_bytes() -> bytes:
    document = Document()
    document.add_paragraph("Python and SQL experience building backend systems.")
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
async def tailor_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    from app.auth import security

    monkeypatch.setattr(security, "SessionLocal", session_factory)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(resume_tailor_router)
    fake_client = FakeTailorClient()
    app.dependency_overrides[get_tailor_client] = lambda: fake_client
    yield app, session_factory, fake_client
    await engine.dispose()


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["jobs_csrf"]}


async def _signup_and_seed_matches(client, session_factory, companies: list[str]) -> list[int]:
    await client.post(
        "/api/auth/signup",
        json={"name": "Alex", "email": "alex@example.com", "password": "a-long-password", "consent": True},
    )
    match_ids = []
    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        for company in companies:
            posting = PostingRow(
                posting_key=f"{company}|swe|remote",
                source="test",
                company=company,
                title="New Grad SWE",
                url="https://example.com/job",
                location="Remote",
                role_type="new_grad",
                status="open",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            session.add(posting)
            await session.flush()
            match = MatchRow(
                user_id=user.id,
                posting_id=posting.id,
                score=0.8,
                blurb="great fit",
                priority="normal",
                lane="slow",
                match_reason="new_posting",
                notified_channels=[],
                created_at=datetime.now(UTC),
            )
            session.add(match)
            await session.flush()
            match_ids.append(match.id)
        await session.commit()
    return match_ids


@pytest.mark.asyncio
async def test_tailor_single_posting_returns_pdf(tailor_app):
    app, session_factory, _fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_ids[0])]},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content.startswith(b"%PDF-")


@pytest.mark.asyncio
async def test_tailor_multiple_postings_returns_zip_with_one_pdf_each(tailor_app):
    app, session_factory, fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme", "Beta Corp"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_ids[0]), str(match_ids[1])]},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/zip"
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        names = archive.namelist()
        pdf_names = [n for n in names if n.endswith(".pdf")]
        assert len(pdf_names) == 2
        for name in pdf_names:
            assert archive.read(name).startswith(b"%PDF-")
        manifest = json.loads(archive.read("results.json"))
        assert all(item["status"] == "ok" for item in manifest)
        assert fake_client.parse_calls == 1
        assert set(fake_client.calls) == {"Acme", "Beta Corp"}


@pytest.mark.asyncio
async def test_tailor_one_job_failing_does_not_block_the_others(tailor_app):
    app, session_factory, fake_client = tailor_app
    fake_client.fail_for_company = "Beta Corp"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme", "Beta Corp"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_ids[0]), str(match_ids[1])]},
        )

        assert response.status_code == 200
        archive = zipfile.ZipFile(io.BytesIO(response.content))
        pdf_names = [n for n in archive.namelist() if n.endswith(".pdf")]
        assert len(pdf_names) == 1  # only Acme succeeded
        manifest = json.loads(archive.read("results.json"))
        statuses = {item["company"]: item["status"] for item in manifest}
        assert statuses["Acme"] == "ok"
        assert statuses["Beta Corp"] == "failed"


@pytest.mark.asyncio
async def test_tailor_single_failing_job_returns_502(tailor_app):
    app, session_factory, fake_client = tailor_app
    fake_client.fail_for_company = "Acme"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_ids[0])]},
        )

        assert response.status_code == 502


@pytest.mark.asyncio
async def test_tailor_returns_useful_error_before_proxy_deadline(tailor_app, monkeypatch):
    from app.api import resume_tailor as resume_tailor_api

    app, session_factory, fake_client = tailor_app
    fake_client.parse_delay = 0.05
    monkeypatch.setattr(resume_tailor_api, "TAILOR_REQUEST_TIMEOUT_SECONDS", 0.001)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={
                "file": (
                    "resume.docx",
                    _resume_bytes(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
            data={"match_ids": [str(match_ids[0])]},
        )

    assert response.status_code == 504
    assert response.json()["detail"] == (
        "Resume tailoring took too long. Select fewer jobs and try again."
    )


@pytest.mark.asyncio
async def test_tailor_requires_csrf(tailor_app):
    app, session_factory, _fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme"])

        response = await client.post(
            "/api/resume/tailor",
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_ids[0])]},
        )

        assert response.status_code == 403


@pytest.mark.asyncio
async def test_tailor_rejects_postings_not_belonging_to_current_user(tailor_app):
    app, session_factory, _fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup_and_seed_matches(client, session_factory, ["Acme"])

    async with session_factory() as session:
        other = User(
            name="Sam", email="sam@example.com", sms_provider="signalwire", opted_out=False,
            created_at=datetime.now(UTC),
        )
        session.add(other)
        await session.commit()
        other_id = other.id
        posting = PostingRow(
            posting_key="other|swe|remote", source="test", company="OtherCo", title="SWE",
            url="https://example.com/x", location="Remote", role_type="new_grad", status="open",
            first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        )
        session.add(posting)
        await session.flush()
        other_match = MatchRow(
            user_id=other_id, posting_id=posting.id, score=0.5, blurb="", priority="normal",
            lane="slow", match_reason="new_posting", notified_channels=[], created_at=datetime.now(UTC),
        )
        session.add(other_match)
        await session.commit()
        other_match_id = other_match.id

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client2:
        await client2.post(
            "/api/auth/login", json={"email": "alex@example.com", "password": "a-long-password"}
        )
        response = await client2.post(
            "/api/resume/tailor",
            headers=_csrf(client2),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(other_match_id)]},
        )

        assert response.status_code == 404


@pytest.mark.asyncio
async def test_tailor_rejects_malformed_resume(tailor_app):
    app, session_factory, _fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        match_ids = await _signup_and_seed_matches(client, session_factory, ["Acme"])

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.pdf", b"not a real pdf", "application/pdf")},
            data={"match_ids": [str(match_ids[0])]},
        )

        assert response.status_code == 422


@pytest.mark.asyncio
async def test_tailor_resolves_by_match_id_when_id_spaces_diverge(tailor_app):
    """Selection is by match id, and match ids drift away from posting ids
    as soon as postings exist that this user never matched (or one posting
    matches several users). The seeding helper above creates postings and
    matches in lockstep, so both sequences agree there and the two id spaces
    are indistinguishable — this seeds them deliberately apart so a lookup
    against the wrong column resolves to the wrong job or 404s.
    """
    app, session_factory, _fake_client = tailor_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/signup",
            json={"name": "Alex", "email": "alex@example.com", "password": "a-long-password", "consent": True},
        )
        async with session_factory() as session:
            user = (await session.execute(select(User))).scalar_one()
            for index in range(3):
                session.add(
                    PostingRow(
                        posting_key=f"decoy-{index}",
                        source="test",
                        company=f"Decoy {index}",
                        title="Unmatched role",
                        url="https://example.com/decoy",
                        location="Remote",
                        role_type="new_grad",
                        status="open",
                        first_seen_at=datetime.now(UTC),
                        last_seen_at=datetime.now(UTC),
                    )
                )
            await session.flush()
            wanted = PostingRow(
                posting_key="dreamco|swe|remote",
                source="test",
                company="DreamCo",
                title="New Grad SWE",
                url="https://example.com/job",
                location="Remote",
                role_type="new_grad",
                status="open",
                first_seen_at=datetime.now(UTC),
                last_seen_at=datetime.now(UTC),
            )
            session.add(wanted)
            await session.flush()
            match = MatchRow(
                user_id=user.id,
                posting_id=wanted.id,
                score=0.9,
                blurb="great fit",
                priority="normal",
                lane="slow",
                match_reason="new_posting",
                notified_channels=[],
                created_at=datetime.now(UTC),
            )
            session.add(match)
            await session.flush()
            match_id, posting_id = match.id, wanted.id
            await session.commit()

        assert match_id != posting_id, "id spaces must actually diverge for this to test anything"

        response = await client.post(
            "/api/resume/tailor",
            headers=_csrf(client),
            files={"file": ("resume.docx", _resume_bytes(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
            data={"match_ids": [str(match_id)]},
        )

        assert response.status_code == 200
        assert "DreamCo" in response.headers["content-disposition"]
