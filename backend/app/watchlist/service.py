from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Watchlist as WatchlistRow
from app.ingest.normalize import normalize_company_key
from app.sources.base import Source
from app.sources.registry import registry
from app.watchlist.detect import detect_ats_board


def _row_to_dict(row: WatchlistRow) -> dict:
    return {
        "company_name": row.company_name,
        "status": row.status,
        "ats_provider": row.ats_provider,
    }


async def add_company(session: AsyncSession, user_id: int, company_name: str) -> dict:
    """Add a company to a user's watchlist, auto-detecting its ATS board.
    Idempotent per (user, normalized company name) — re-adding an existing
    entry returns its current status rather than re-probing (see spec
    addendum: Company watchlist)."""
    company_key = normalize_company_key(company_name)
    if not company_key:
        return {"status": "error", "message": "no company name given"}

    existing = (
        await session.execute(
            select(WatchlistRow).where(
                WatchlistRow.user_id == user_id, WatchlistRow.company_key == company_key
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return _row_to_dict(existing)

    provider_slug = await detect_ats_board(company_name)
    row = WatchlistRow(
        user_id=user_id,
        company_name=company_name,
        company_key=company_key,
        ats_provider=provider_slug[0] if provider_slug else None,
        ats_slug=provider_slug[1] if provider_slug else None,
        status="active" if provider_slug else "not_found",
        created_at=datetime.now(UTC),
    )
    session.add(row)
    await session.flush()
    if provider_slug:
        registry.get_or_create_ats_source(*provider_slug)
    return _row_to_dict(row)


async def list_companies(session: AsyncSession, user_id: int) -> list[dict]:
    rows = (
        (await session.execute(select(WatchlistRow).where(WatchlistRow.user_id == user_id)))
        .scalars()
        .all()
    )
    return [_row_to_dict(row) for row in rows]


async def remove_company(session: AsyncSession, user_id: int, company_name: str) -> dict:
    company_key = normalize_company_key(company_name)
    row = (
        await session.execute(
            select(WatchlistRow).where(
                WatchlistRow.user_id == user_id, WatchlistRow.company_key == company_key
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return {"status": "not_found", "company_name": company_name}
    await session.delete(row)
    await session.flush()
    return {"status": "removed", "company_name": company_name}


async def sync_watchlist_sources(session: AsyncSession) -> list[Source]:
    """Every actively-detected watchlist entry, deduped across users,
    resolved to a persistent Source instance via the registry — see
    app.sources.registry for why reusing the same instance across cycles
    matters for change-detection state."""
    rows = (
        (await session.execute(select(WatchlistRow).where(WatchlistRow.status == "active")))
        .scalars()
        .all()
    )
    seen: set[tuple[str, str]] = set()
    sources: list[Source] = []
    for row in rows:
        if row.ats_provider is None or row.ats_slug is None:
            continue
        key = (row.ats_provider, row.ats_slug)
        if key in seen:
            continue
        seen.add(key)
        sources.append(registry.get_or_create_ats_source(row.ats_provider, row.ats_slug))
    return sources


async def watchlisted_company_keys(session: AsyncSession, user_id: int) -> set[str]:
    """Normalized company keys this user is actively watching — matching
    uses this to force instant priority regardless of LLM score (see spec
    addendum: watchlist priority)."""
    rows = (
        await session.execute(
            select(WatchlistRow.company_key).where(
                WatchlistRow.user_id == user_id, WatchlistRow.status == "active"
            )
        )
    ).scalars().all()
    return set(rows)


async def watchlisted_company_keys_by_user(
    session: AsyncSession, user_ids: list[int]
) -> dict[int, set[str]]:
    """Batched form of watchlisted_company_keys() for every id in user_ids in
    one query — used by the per-cycle match loop so it isn't issuing one
    extra round-trip per active user per cycle (N+1)."""
    if not user_ids:
        return {}
    rows = (
        await session.execute(
            select(WatchlistRow.user_id, WatchlistRow.company_key).where(
                WatchlistRow.user_id.in_(user_ids), WatchlistRow.status == "active"
            )
        )
    ).all()
    by_user: dict[int, set[str]] = {user_id: set() for user_id in user_ids}
    for user_id, company_key in rows:
        by_user[user_id].add(company_key)
    return by_user
