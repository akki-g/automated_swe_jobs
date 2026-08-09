from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.api.auth import router as auth_router
from app.api.matches import get_email_provider
from app.api.matches import router as matches_router
from app.db.models import Base
from app.db.models import Match as MatchRow
from app.db.models import Posting as PostingRow
from app.db.models import User


class FakeEmailProvider:
    def __init__(self, success: bool = True):
        self.success = success
        self.sent: list[tuple] = []

    async def send(self, to, subject, body, html=None):
        self.sent.append((to, subject, body, html))
        return self.success


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
    fake_email = FakeEmailProvider()
    app.dependency_overrides[get_email_provider] = lambda: fake_email
    yield app, session_factory, fake_email
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
    posting_status: str = "open",
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
            status=posting_status,
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
    app, session_factory, _fake_email = web_app
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
    app, _session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/matches")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_list_matches_filters_by_company_location_and_priority(web_app):
    app, session_factory, _fake_email = web_app
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
    app, session_factory, _fake_email = web_app
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
    app, session_factory, _fake_email = web_app
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

        # Still the same visit, so the baseline has not moved: "new since
        # last visit" continues to mean everything since the *previous*
        # visit, which is still both of these. Re-baselining per request
        # instead would drop the old match from this list and clear its
        # badge mid-session — see list_matches on why the baseline is
        # visit-scoped.
        second_call = await client.get("/api/matches", params={"new_only": "true"})
        assert {item["id"] for item in second_call.json()["items"]} == {old_match_id, new_match_id}


@pytest.mark.asyncio
async def test_list_matches_is_paginated(web_app):
    app, session_factory, _fake_email = web_app
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
    app, session_factory, _fake_email = web_app
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
    app, session_factory, _fake_email = web_app
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
    app, session_factory, _fake_email = web_app
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


@pytest.mark.asyncio
async def test_is_new_and_new_only_survive_repeated_loads_in_one_visit(web_app):
    """The matches page re-fetches on every filter change. If each request
    advanced matches_last_viewed_at, the "New" badges would vanish the
    instant the user touched a filter, and `new_only` could never match
    anything: opening the page moved the watermark to now, and ticking the
    box is the very next request. The watermark must hold still for a visit.
    """
    app, session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        async with session_factory() as session:
            user = (await session.execute(select(User))).scalar_one()
            user_id = user.id
            await session.commit()
        for index in range(3):
            await _seed_posting_and_match(session_factory, user_id=user_id, company=f"C{index}")

        first = (await client.get("/api/matches")).json()
        assert [item["is_new"] for item in first["items"]] == [True, True, True]

        # A filter change — the second request of the same visit.
        second = (await client.get("/api/matches")).json()
        assert [item["is_new"] for item in second["items"]] == [True, True, True]

        only_new = (await client.get("/api/matches?new_only=true")).json()
        assert only_new["total"] == 3


@pytest.mark.asyncio
async def test_a_later_visit_rebaselines_what_counts_as_new(web_app):
    """The flip side: once the visit window has elapsed, coming back must
    re-baseline, or everything would stay "New" forever."""
    app, session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        async with session_factory() as session:
            user = (await session.execute(select(User))).scalar_one()
            user_id = user.id
            await session.commit()
        await _seed_posting_and_match(
            session_factory, user_id=user_id, created_at=datetime.now(UTC) - timedelta(days=2)
        )

        # First ever visit: nothing to compare against, so it reads as new.
        assert (await client.get("/api/matches")).json()["items"][0]["is_new"] is True

        # Age that visit so the next request counts as a fresh one.
        async with session_factory() as session:
            user = await session.get(User, user_id)
            user.matches_visit_started_at = datetime.now(UTC) - timedelta(hours=4)
            await session.commit()

        # Returning re-baselines to the previous visit, which this match
        # predates by two days.
        assert (await client.get("/api/matches")).json()["items"][0]["is_new"] is False


@pytest.mark.asyncio
async def test_list_matches_excludes_postings_link_validation_marked_closed(web_app):
    """A dead link (see ingest/link_check.py) must not keep showing up as a
    match just because the row predates that check."""
    app, session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(session_factory, user_id=user_id, company="Alive Co")
        await _seed_posting_and_match(
            session_factory, user_id=user_id, company="Dead Co", posting_status="closed"
        )

        response = await client.get("/api/matches")

        assert [item["company"] for item in response.json()["items"]] == ["Alive Co"]


@pytest.mark.asyncio
async def test_resend_email_sends_curated_current_matches(web_app):
    app, session_factory, fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(session_factory, user_id=user_id, company="Acme", score=0.9)

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.status_code == 200
        assert response.json() == {"sent": True, "match_count": 1}
        assert len(fake_email.sent) == 1
        to, subject, text, html = fake_email.sent[0]
        assert to == "alex@example.com"
        assert "Acme" in text
        assert "Acme" in html


@pytest.mark.asyncio
async def test_resend_email_excludes_closed_postings(web_app):
    app, session_factory, fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(
            session_factory, user_id=user_id, company="Dead Co", posting_status="closed"
        )

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.status_code == 200
        assert response.json()["match_count"] == 0
        assert "Dead Co" not in fake_email.sent[0][2]


@pytest.mark.asyncio
async def test_resend_email_requires_csrf(web_app):
    app, session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        response = await client.post("/api/matches/resend-email")
        assert response.status_code == 403


@pytest.mark.asyncio
async def test_resend_email_requires_authentication(web_app):
    app, _session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post("/api/matches/resend-email")
        assert response.status_code == 401


@pytest.mark.asyncio
async def test_resend_email_422s_without_an_email_address(web_app):
    app, session_factory, _fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        async with session_factory() as session:
            user = (await session.execute(select(User))).scalar_one()
            user.email = None
            await session.commit()

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.status_code == 422


@pytest.mark.asyncio
async def test_resend_email_502s_when_provider_fails(web_app):
    app, session_factory, fake_email = web_app
    fake_email.success = False
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client)
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(session_factory, user_id=user_id)

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.status_code == 502


@pytest.mark.asyncio
async def test_resend_email_ignores_another_users_matches(web_app):
    app, session_factory, fake_email = web_app
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
        await _seed_posting_and_match(session_factory, user_id=other_id, company="Other Co")

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.json()["match_count"] == 0
        assert "Other Co" not in fake_email.sent[0][2]


@pytest.mark.asyncio
async def test_resend_email_marks_just_dropped_delivered_so_it_never_repeats(web_app):
    """A never-before-emailed match shown in "Just Dropped" must not appear
    in "Just Dropped" again on a second resend — the exact "should not be
    repeated in different emails" requirement."""
    app, session_factory, fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        user_id = await _get_user_id(session_factory)
        await _seed_posting_and_match(session_factory, user_id=user_id, company="Acme")

        first = await client.post("/api/matches/resend-email", headers=_csrf(client))
        second = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert first.json()["match_count"] == 1
        assert "Just Dropped" in fake_email.sent[0][3]
        # Second send: the same match now comes back as "For You", not
        # "Just Dropped" again — still shown (still relevant), just not
        # re-presented as new.
        assert second.json()["match_count"] == 1
        assert "Acme" in fake_email.sent[1][2]
        assert "For You" in fake_email.sent[1][3]
        assert "Just Dropped" not in fake_email.sent[1][3]


@pytest.mark.asyncio
async def test_resend_email_caps_just_dropped_at_five_on_a_first_ever_send(web_app):
    """With no send history yet, "For You" has nothing to draw from — a
    first-ever email is capped at digest_max_just_dropped (5), not the
    overall 15, since 20 brand-new matches are all "Just Dropped" candidates
    and none are "For You" candidates yet."""
    app, session_factory, fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        user_id = await _get_user_id(session_factory)
        for i in range(20):
            await _seed_posting_and_match(
                session_factory, user_id=user_id, company=f"Co{i}", title=f"Role {i}", score=0.9 - i * 0.01
            )

        response = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert response.json()["match_count"] == 5
        async with session_factory() as session:
            all_matches = (await session.execute(select(MatchRow))).scalars().all()
            notified = [m for m in all_matches if "email" in (m.notified_channels or [])]
            assert len(notified) == 5


@pytest.mark.asyncio
async def test_resend_email_fills_up_to_fifteen_once_for_you_has_history(web_app):
    """Once earlier sends have built up a 10-item "For You" pool (two rounds
    of 5, since each round's "Just Dropped" is itself capped at 5), a later
    resend with more brand-new matches fills up to the full 15: 5 Just
    Dropped + 10 For You."""
    app, session_factory, fake_email = web_app
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        await _signup(client, "alex@example.com")
        user_id = await _get_user_id(session_factory)

        for i in range(5):
            await _seed_posting_and_match(session_factory, user_id=user_id, company=f"Old{i}", score=0.6)
        await client.post("/api/matches/resend-email", headers=_csrf(client))  # Old0-4 -> already sent

        for i in range(5, 10):
            await _seed_posting_and_match(session_factory, user_id=user_id, company=f"Old{i}", score=0.6)
        await client.post("/api/matches/resend-email", headers=_csrf(client))  # Old5-9 -> already sent

        for i in range(20):
            await _seed_posting_and_match(session_factory, user_id=user_id, company=f"New{i}", score=0.9)
        third = await client.post("/api/matches/resend-email", headers=_csrf(client))

        assert third.json()["match_count"] == 15
