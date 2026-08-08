from __future__ import annotations

from io import BytesIO

import httpx
import pytest
from docx import Document
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.api.profile import router as profile_router
from app.api.resume import get_resume_client
from app.api.resume import router as resume_router
from app.db.models import Base, Criteria, User


class FakeResumeClient:
    async def create_message(self, *, system, messages, tools):
        assert "Python and SQL" in messages[0]["content"]
        return {
            "content": [
                {
                    "type": "tool_use",
                    "name": tools[0]["name"],
                    "input": {
                        "skills": ["Python", "SQL"],
                        "past_titles": ["Data Intern"],
                        "experience_level": "student",
                        "years_experience": 1,
                        "inferred_target_fields": ["data_science_analytics"],
                        "education_fields": ["Statistics"],
                        "summary": "Student with analytics internship experience.",
                    },
                }
            ]
        }


@pytest.fixture
async def web_app(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    from app.auth import security

    monkeypatch.setattr(security, "SessionLocal", session_factory)
    app = FastAPI()
    app.include_router(auth_router)
    app.include_router(profile_router)
    app.include_router(resume_router)
    app.dependency_overrides[get_resume_client] = lambda: FakeResumeClient()
    yield app, session_factory
    await engine.dispose()


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["jobs_csrf"]}


@pytest.mark.asyncio
async def test_signup_records_consent_and_profile_flow(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/auth/signup",
            json={
                "name": "Alex Morgan",
                "email": "Alex@Example.com",
                "password": "a-long-password",
                "consent": True,
            },
        )
        assert response.status_code == 201
        assert response.json()["phone"] is None
        assert response.json()["email_digest_time"] == "08:00"
        assert "jobs_session" in client.cookies

        profile = await client.put(
            "/api/profile",
            headers=_csrf(client),
            json={
                "name": "Alex Morgan",
                "role_types": ["new_grad"],
                "target_fields": ["consulting", "finance_investment_banking"],
                "keywords": ["Strategy", "strategy", "Excel"],
                "locations": ["New York"],
                "sponsorship_required": False,
                "freeform_notes": "Interested in rotational programs.",
                "email_digest_enabled": True,
                "mark_complete": True,
            },
        )
        assert profile.status_code == 200
        assert profile.json()["profile_completed"] is True
        assert profile.json()["keywords"] == ["Strategy", "Excel"]

    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        criteria = (await session.execute(select(Criteria))).scalar_one()
        assert user.email == "alex@example.com"
        assert user.password_hash and "a-long-password" not in user.password_hash
        assert user.consent_method == "web-signup-terms-v1"
        assert user.consent_at is not None
        assert criteria.target_fields == ["consulting", "finance_investment_banking"]


@pytest.mark.asyncio
async def test_email_settings_update_delivery_time(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/signup",
            json={
                "name": "Alex",
                "email": "alex@example.com",
                "password": "a-long-password",
                "consent": True,
            },
        )
        response = await client.put(
            "/api/profile/settings",
            headers=_csrf(client),
            json={"email_digest_enabled": True, "email_digest_time": "17:45"},
        )
        invalid = await client.put(
            "/api/profile/settings",
            headers=_csrf(client),
            json={"email_digest_enabled": True, "email_digest_time": "25:00"},
        )

        assert response.status_code == 200
        assert response.json()["email_digest_time"] == "17:45"
        assert invalid.status_code == 422

    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        assert user.email_digest_time == "17:45"


@pytest.mark.asyncio
async def test_profile_mutation_rejects_missing_csrf(web_app):
    app, _session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/signup",
            json={
                "name": "Alex",
                "email": "alex@example.com",
                "password": "a-long-password",
                "consent": True,
            },
        )
        response = await client.put(
            "/api/profile",
            json={
                "name": "Alex",
                "role_types": ["intern"],
                "target_fields": ["marketing"],
            },
        )
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_login_accepts_only_the_hashed_password(web_app):
    app, _session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/signup",
            json={
                "name": "Alex",
                "email": "alex@example.com",
                "password": "a-long-password",
                "consent": True,
            },
        )
        await client.post("/api/auth/logout")

        rejected = await client.post(
            "/api/auth/login",
            json={"email": "alex@example.com", "password": "wrong"},
        )
        accepted = await client.post(
            "/api/auth/login",
            json={"email": "ALEX@example.com", "password": "a-long-password"},
        )

        assert rejected.status_code == 401
        assert accepted.status_code == 200
        assert accepted.json()["name"] == "Alex"


@pytest.mark.asyncio
async def test_resume_upload_persists_only_structured_profile(web_app):
    app, session_factory = web_app
    document = Document()
    raw_resume_text = "Python and SQL. Private full work description that must not be stored."
    document.add_paragraph(raw_resume_text)
    content = BytesIO()
    document.save(content)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await client.post(
            "/api/auth/signup",
            json={
                "name": "Alex",
                "email": "alex@example.com",
                "password": "a-long-password",
                "consent": True,
            },
        )
        response = await client.post(
            "/api/profile/resume",
            headers=_csrf(client),
            files={
                "file": (
                    "resume.docx",
                    content.getvalue(),
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                )
            },
        )
        assert response.status_code == 200
        assert response.json()["resume_profile"]["skills"] == ["Python", "SQL"]

    async with session_factory() as session:
        criteria = (await session.execute(select(Criteria))).scalar_one()
        serialized = str(criteria.resume_profile)
        assert criteria.resume_profile["experience_level"] == "student"
        assert raw_resume_text not in serialized
        assert "Private full work description" not in serialized
