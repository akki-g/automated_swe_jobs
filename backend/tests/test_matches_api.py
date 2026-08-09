from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.api.matches import router as matches_router
from app.db.models import Base
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User


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
    app.include_router(matches_router)
    yield app, session_factory
    await engine.dispose()


def _csrf(client: httpx.AsyncClient) -> dict[str, str]:
    return {"X-CSRF-Token": client.cookies["jobs_csrf"]}


async def _signup(client: httpx.AsyncClient, email: str = "alex@example.com") -> None:
    response = await client.post(
        "/api/auth/signup",
        json={"name": "Alex", "email": email, "password": "a-long-password", "consent": True},
    )
    assert response.status_code == 201


async def _seed_posting_and_match(
    session_factory,
    *,
    user_id: int,
    company: str = "Acme",
    title: str = "New Grad SWE",
    location: str | None = "Remote",
    score: float = 0.7,
    priority: str = "normal",
    matched_target_field: str | None = None,
    saved: bool = False,
    created_at: datetime | None = None,
) -> int:
    created_at = created_at or datetime.now(UTC)
    async with session_factory() as session:
        posting = PostingRow(
            posting_key=f"{company}|{title}|{location}",
            source="test",
            company=company,
            title=title,
            url="https://example.com/job",
            location=location,
            role_type="new_grad",
            status="open",
            first_seen_at=created_at,
            last_seen_at=created_at,
        )
        session.add(posting)
        await session.flush()
        match = MatchRow(
            user_id=user_id,
            posting_id=posting.id,
            score=score,
            blurb="good fit",
            priority=priority,
            lane="slow",
            match_reason="new_posting",
            matched_target_field=matched_target_field,
            saved=saved,
            notified_channels=[],
            created_at=created_at,
        )
        session.add(match)
        await session.commit()
        return match.id


async def _get_user_id(session_factory) -> int:
    async with session_factory() as session:
        user = (await session.execute(select(User))).scalar_one()
        return user.id


@pytest.mark.asyncio
async def test_list_matches_returns_only_current_users_matches(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        alex_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(session_factory, user_id=alex_id, company="Acme")

        # A second user's match must never appear in Alex's list.
        async with session_factory() as session:
            other = User(
                name="Sam",
                email="sam@example.com",
                sms_provider="signalwire",
                opted_out=False,
                created_at=datetime.now(UTC),
            )
            session.add(other)
            await session.commit()
            other_id = other.id
        await _seed_posting_and_match(session_factory, user_id=other_id, company="Other Co")

        response = await client.get("/api/matches")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 1
        assert body["items"][0]["company"] == "Acme"


@pytest.mark.asyncio
async def test_list_matches_requires_authentication(web_app):
    app, _session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/matches")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_matches_filters_by_company_location_and_priority(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(
            session_factory, user_id=user_id, company="Acme", location="New York", priority="high"
        )
        await _seed_posting_and_match(
            session_factory, user_id=user_id, company="Beta Corp", location="Remote", priority="normal"
        )

        by_company = await client.get("/api/matches", params={"company": "acme"})
        by_location = await client.get("/api/matches", params={"location": "remote"})
        by_priority = await client.get("/api/matches", params={"priority": "high"})

        assert [item["company"] for item in by_company.json()["items"]] == ["Acme"]
        assert [item["company"] for item in by_location.json()["items"]] == ["Beta Corp"]
        assert [item["company"] for item in by_priority.json()["items"]] == ["Acme"]


@pytest.mark.asyncio
async def test_list_matches_filters_by_target_field_min_score_and_saved(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(
            session_factory,
            user_id=user_id,
            company="Consulting Co",
            matched_target_field="consulting",
            score=0.9,
            saved=True,
        )
        await _seed_posting_and_match(
            session_factory, user_id=user_id, company="Marketing Co", matched_target_field="marketing", score=0.3
        )

        by_field = await client.get("/api/matches", params={"target_field": "consulting"})
        by_score = await client.get("/api/matches", params={"min_score": 0.5})
        by_saved = await client.get("/api/matches", params={"saved": "true"})

        assert [item["company"] for item in by_field.json()["items"]] == ["Consulting Co"]
        assert [item["company"] for item in by_score.json()["items"]] == ["Consulting Co"]
        assert [item["company"] for item in by_saved.json()["items"]] == ["Consulting Co"]


@pytest.mark.asyncio
async def test_list_matches_new_only_uses_previous_last_viewed_at(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        old_match_id = await _seed_posting_and_match(
            session_factory,
            user_id=user_id,
            company="Old Co",
            created_at=datetime.now(UTC) - timedelta(days=1),
        )

        # Never viewed before -> everything (including "old") counts as new.
        first_call = await client.get("/api/matches", params={"new_only": "true"})
        assert {item["id"] for item in first_call.json()["items"]} == {old_match_id}
        assert first_call.json()["items"][0]["is_new"] is True

        new_match_id = await _seed_posting_and_match(session_factory, user_id=user_id, company="New Co")

        # This call's own "new_only" set is computed against the *previous*
        # last_viewed_at (before first_call updated it) — only postings
        # created after that point should show, i.e. just the new one.
        second_call = await client.get("/api/matches", params={"new_only": "true"})
        assert {item["id"] for item in second_call.json()["items"]} == {new_match_id}


@pytest.mark.asyncio
async def test_list_matches_is_paginated(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        for i in range(3):
            await _seed_posting_and_match(session_factory, user_id=user_id, company=f"Co{i}")

        page = await client.get("/api/matches", params={"limit": 2, "offset": 1})

        assert page.json()["total"] == 3
        assert len(page.json()["items"]) == 2


@pytest.mark.asyncio
async def test_save_match_toggles_flag(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        match_id = await _seed_posting_and_match(session_factory, user_id=user_id)

        saved = await client.post(
            f"/api/matches/{match_id}/save", headers=_csrf(client), json={"saved": True}
        )
        unsaved = await client.post(
            f"/api/matches/{match_id}/save", headers=_csrf(client), json={"saved": False}
        )

        assert saved.status_code == 200
        assert saved.json() == {"id": match_id, "saved": True}
        assert unsaved.json() == {"id": match_id, "saved": False}


@pytest.mark.asyncio
async def test_save_match_rejects_missing_csrf(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        match_id = await _seed_posting_and_match(session_factory, user_id=user_id)

        response = await client.post(f"/api/matches/{match_id}/save", json={"saved": True})

        assert response.status_code == 403


@pytest.mark.asyncio
async def test_save_match_404s_for_another_users_match(web_app):
    app, session_factory = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")

        async with session_factory() as session:
            other = User(
                name="Sam",
                email="sam@example.com",
                sms_provider="signalwire",
                opted_out=False,
                created_at=datetime.now(UTC),
            )
            session.add(other)
            await session.commit()
            other_id = other.id
        other_match_id = await _seed_posting_and_match(session_factory, user_id=other_id)

        response = await client.post(
            f"/api/matches/{other_match_id}/save", headers=_csrf(client), json={"saved": True}
        )

        assert response.status_code == 404
