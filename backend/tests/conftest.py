from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User


@pytest.fixture(autouse=True)
def _no_real_link_checks(monkeypatch):
    """pipeline.match_new_postings validates each new posting's link before
    anyone can be matched against it (see ingest/link_check.py) — a real
    httpx call by default. Test fixtures across this suite use placeholder
    URLs like https://example.com/1, which really resolve and can really
    404, so without this every pipeline test would be one unmocked network
    call away from flaking (and violate this repo's "no live network calls
    in tests" convention — see spec: Testing). Defaults every test to
    "everything is alive"; tests that specifically exercise link validation
    (see test_link_check.py, test_pipeline.py's dead-link tests) override
    this locally with their own monkeypatch."""

    async def _always_alive(url, client):
        return True

    monkeypatch.setattr("app.pipeline.check_link_alive", _always_alive)


@pytest_asyncio.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture
async def demo_user(db_session: AsyncSession) -> User:
    user = User(
        name="Demo",
        phone="+15555550100",
        email="demo@example.com",
        sms_provider="signalwire",
        opted_out=False,
        consent_at=datetime.now(UTC),
        consent_method="verbal-friend-onboarding",
        created_at=datetime.now(UTC),
    )
    db_session.add(user)
    await db_session.flush()
    return user
