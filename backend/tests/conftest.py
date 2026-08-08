from __future__ import annotations

from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, User


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
