import pytest
from sqlalchemy import select

from app.db.models import Watchlist as WatchlistRow
from app.sources.registry import registry
from app.watchlist import service as watchlist_service


@pytest.fixture(autouse=True)
def _reset_registry():
    registry.reset()
    yield
    registry.reset()


@pytest.mark.asyncio
async def test_add_company_stores_detected_board(db_session, demo_user, monkeypatch):
    monkeypatch.setattr(
        watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "acme"))
    )

    result = await watchlist_service.add_company(db_session, demo_user.id, "Acme")

    assert result == {"company_name": "Acme", "status": "active", "ats_provider": "greenhouse"}
    row = (
        await db_session.execute(select(WatchlistRow).where(WatchlistRow.user_id == demo_user.id))
    ).scalar_one()
    assert row.ats_slug == "acme"


@pytest.mark.asyncio
async def test_add_company_records_not_found_when_no_board_detected(db_session, demo_user, monkeypatch):
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(None))

    result = await watchlist_service.add_company(db_session, demo_user.id, "Totally Unknown Co")

    assert result["status"] == "not_found"
    assert result["ats_provider"] is None


@pytest.mark.asyncio
async def test_add_company_is_idempotent_and_does_not_reprobe(db_session, demo_user, monkeypatch):
    calls = []

    async def fake_detect(name):
        calls.append(name)
        return ("greenhouse", "acme")

    monkeypatch.setattr(watchlist_service, "detect_ats_board", fake_detect)

    await watchlist_service.add_company(db_session, demo_user.id, "Acme")
    await watchlist_service.add_company(db_session, demo_user.id, "acme")  # different case, same key

    assert len(calls) == 1
    rows = (
        (await db_session.execute(select(WatchlistRow).where(WatchlistRow.user_id == demo_user.id)))
        .scalars()
        .all()
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_add_company_rejects_blank_name(db_session, demo_user):
    result = await watchlist_service.add_company(db_session, demo_user.id, "   ")
    assert result["status"] == "error"


@pytest.mark.asyncio
async def test_remove_company_deletes_existing_entry(db_session, demo_user, monkeypatch):
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "acme")))
    await watchlist_service.add_company(db_session, demo_user.id, "Acme")

    result = await watchlist_service.remove_company(db_session, demo_user.id, "Acme")

    assert result == {"status": "removed", "company_name": "Acme"}
    rows = (
        (await db_session.execute(select(WatchlistRow).where(WatchlistRow.user_id == demo_user.id)))
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_remove_company_reports_not_found_for_unwatched_company(db_session, demo_user):
    result = await watchlist_service.remove_company(db_session, demo_user.id, "Nope Corp")
    assert result["status"] == "not_found"


@pytest.mark.asyncio
async def test_list_companies_returns_all_entries_for_user(db_session, demo_user, monkeypatch):
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "acme")))
    await watchlist_service.add_company(db_session, demo_user.id, "Acme")
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(None))
    await watchlist_service.add_company(db_session, demo_user.id, "Unknown Co")

    results = await watchlist_service.list_companies(db_session, demo_user.id)

    assert {r["company_name"] for r in results} == {"Acme", "Unknown Co"}


@pytest.mark.asyncio
async def test_sync_watchlist_sources_dedupes_by_board_and_reuses_registry_instances(
    db_session, demo_user, monkeypatch
):
    # Two different watchlist entries (different company names/keys) that
    # both resolve to the same underlying board — sync must still return
    # exactly one Source for that board, not one per watchlist row.
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "acme")))
    await watchlist_service.add_company(db_session, demo_user.id, "Acme")
    await watchlist_service.add_company(db_session, demo_user.id, "Acme Also")

    sources_first = await watchlist_service.sync_watchlist_sources(db_session)
    sources_second = await watchlist_service.sync_watchlist_sources(db_session)

    assert len(sources_first) == 1
    # Same (provider, slug) must resolve to the SAME instance across calls —
    # this is what makes check_for_changes() state survive across scheduler
    # ticks (see app.sources.registry).
    by_name_first = {s.name: s for s in sources_first}
    by_name_second = {s.name: s for s in sources_second}
    assert by_name_first.keys() == by_name_second.keys()
    for name in by_name_first:
        assert by_name_first[name] is by_name_second[name]


@pytest.mark.asyncio
async def test_watchlisted_company_keys_only_returns_active_entries_for_that_user(
    db_session, demo_user, monkeypatch
):
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(("greenhouse", "acme")))
    await watchlist_service.add_company(db_session, demo_user.id, "Acme")
    monkeypatch.setattr(watchlist_service, "detect_ats_board", lambda name: _resolved(None))
    await watchlist_service.add_company(db_session, demo_user.id, "Unknown Co")

    keys = await watchlist_service.watchlisted_company_keys(db_session, demo_user.id)

    assert keys == {"acme"}


async def _resolved(value):
    return value
