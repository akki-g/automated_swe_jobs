from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.config import settings
from app.db.models import Base, User
from app.webhooks import router


@pytest.fixture
def client(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    import app.db.session as db_session_module

    monkeypatch.setattr(db_session_module, "SessionLocal", session_factory)
    monkeypatch.setattr(settings, "signalwire_allow_unsigned_webhooks", True)

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        async with session_factory() as session:
            session.add(
                User(
                    name="Demo",
                    phone="+15555550100",
                    email="demo@example.com",
                    sms_provider="signalwire",
                    opted_out=False,
                    consent_at=datetime.now(UTC),
                    consent_method="verbal-friend-onboarding",
                    created_at=datetime.now(UTC),
                )
            )
            await session.commit()

    import asyncio

    asyncio.run(setup())

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_stop_opts_user_out(client):
    response = client.post(
        "/api/v1/webhooks/signalwire",
        data={"From": "+15555550100", "To": "+15555550199", "Body": "STOP"},
    )
    assert response.status_code == 200
    assert "unsubscribed" in response.text.lower()


def test_help_returns_help_text(client):
    response = client.post(
        "/api/v1/webhooks/signalwire",
        data={"From": "+15555550100", "To": "+15555550199", "Body": "HELP"},
    )
    assert response.status_code == 200
    assert "stop" in response.text.lower()


def test_unknown_number_gets_generic_reply(client):
    response = client.post(
        "/api/v1/webhooks/signalwire",
        data={"From": "+19999999999", "To": "+15555550199", "Body": "hi"},
    )
    assert response.status_code == 200
    assert "don't recognize" in response.text.lower()


def test_start_after_stop_resubscribes(client):
    client.post(
        "/api/v1/webhooks/signalwire",
        data={"From": "+15555550100", "To": "+15555550199", "Body": "STOP"},
    )
    response = client.post(
        "/api/v1/webhooks/signalwire",
        data={"From": "+15555550100", "To": "+15555550199", "Body": "START"},
    )
    assert "resubscribed" in response.text.lower()
