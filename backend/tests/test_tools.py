from datetime import UTC, datetime

import pytest

from app.assistant.tools import (
    _validate_criteria_changes,
    add_watchlist_company,
    get_criteria,
    list_watchlist,
    remove_watchlist_company,
    search_postings,
    update_criteria,
)
from app.db.models import Posting
from app.watchlist import service as watchlist_service


@pytest.mark.asyncio
async def test_update_criteria_creates_row_when_none_exists(db_session, demo_user):
    result = await update_criteria(
        db_session, demo_user.id, {"role_types": ["new_grad"], "keywords": ["backend"]}
    )
    assert result["role_types"] == ["new_grad"]
    assert result["keywords"] == ["backend"]


@pytest.mark.asyncio
async def test_update_criteria_partial_update_preserves_other_fields(db_session, demo_user):
    await update_criteria(db_session, demo_user.id, {"role_types": ["new_grad"], "locations": ["remote"]})
    result = await update_criteria(db_session, demo_user.id, {"keywords": ["ml"]})

    assert result["role_types"] == ["new_grad"]
    assert result["locations"] == ["remote"]
    assert result["keywords"] == ["ml"]


@pytest.mark.asyncio
async def test_get_criteria_returns_empty_dict_when_none_exists(db_session, demo_user):
    result = await get_criteria(db_session, demo_user.id)
    assert result == {}


@pytest.mark.asyncio
async def test_search_postings_filters_by_role_type_and_keyword(db_session):
    db_session.add_all(
        [
            Posting(
                posting_key="k1", source="test", company="Acme", title="New Grad Backend Engineer",
                url="https://example.com/1", role_type="new_grad", status="open",
                first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
            ),
            Posting(
                posting_key="k2", source="test", company="Acme", title="Intern Frontend Engineer",
                url="https://example.com/2", role_type="intern", status="open",
                first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
            ),
            Posting(
                posting_key="k3", source="test", company="Acme", title="Closed New Grad Role",
                url="https://example.com/3", role_type="new_grad", status="stale",
                first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
            ),
        ]
    )
    await db_session.flush()

    results = await search_postings(db_session, role_type="new_grad", keyword="backend")

    assert len(results) == 1
    assert results[0]["title"] == "New Grad Backend Engineer"


@pytest.mark.asyncio
async def test_search_postings_excludes_stale(db_session):
    db_session.add(
        Posting(
            posting_key="k1", source="test", company="Acme", title="Stale Role",
            url="https://example.com/1", role_type="new_grad", status="stale",
            first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    results = await search_postings(db_session)

    assert results == []


def test_validate_criteria_changes_drops_unrecognized_role_type():
    clean, warnings = _validate_criteria_changes({"role_types": ["new_grad", "staff_engineer"]})
    assert clean["role_types"] == ["new_grad"]
    assert warnings


def test_validate_criteria_changes_drops_non_boolean_sponsorship():
    clean, warnings = _validate_criteria_changes({"sponsorship_required": "yes please"})
    assert "sponsorship_required" not in clean
    assert warnings


def test_validate_criteria_changes_accepts_well_formed_input():
    clean, warnings = _validate_criteria_changes(
        {
            "role_types": ["new_grad", "intern"],
            "keywords": ["backend"],
            "locations": ["remote"],
            "sponsorship_required": True,
            "freeform_notes": "prefer fintech",
        }
    )
    assert clean == {
        "role_types": ["new_grad", "intern"],
        "keywords": ["backend"],
        "locations": ["remote"],
        "sponsorship_required": True,
        "freeform_notes": "prefer fintech",
    }
    assert warnings == []


@pytest.mark.asyncio
async def test_update_criteria_never_persists_an_invalid_role_type(db_session, demo_user):
    """A malformed/hallucinated role_type must never reach the DB — it
    would later crash matching's _criteria_row_to_domain (RoleType(rt) with
    no guard)."""
    result = await update_criteria(db_session, demo_user.id, {"role_types": ["new_grad", "bogus"]})
    assert result["role_types"] == ["new_grad"]
    assert "warnings" in result


@pytest.mark.asyncio
async def test_add_watchlist_company_delegates_to_service(db_session, demo_user, monkeypatch):
    async def fake_detect(name):
        return ("greenhouse", "acme")

    monkeypatch.setattr(watchlist_service, "detect_ats_board", fake_detect)

    result = await add_watchlist_company(db_session, demo_user.id, "Acme")

    assert result["status"] == "active"


@pytest.mark.asyncio
async def test_list_and_remove_watchlist_company_round_trip(db_session, demo_user, monkeypatch):
    async def fake_detect(name):
        return None

    monkeypatch.setattr(watchlist_service, "detect_ats_board", fake_detect)

    await add_watchlist_company(db_session, demo_user.id, "Mystery Co")
    listed = await list_watchlist(db_session, demo_user.id)
    assert len(listed) == 1

    removed = await remove_watchlist_company(db_session, demo_user.id, "Mystery Co")
    assert removed["status"] == "removed"
    assert await list_watchlist(db_session, demo_user.id) == []
