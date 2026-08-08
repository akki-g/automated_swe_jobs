from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Criteria as CriteriaRow
from app.db.models import Posting as PostingRow
from app.domain.models import RoleType
from app.watchlist import service as watchlist_service

TOOL_SCHEMAS = [
    {
        "name": "search_postings",
        "description": "Search stored job postings by role type, keyword, and/or location.",
        "input_schema": {
            "type": "object",
            "properties": {
                "role_type": {"type": "string", "enum": ["new_grad", "intern"]},
                "keyword": {"type": "string"},
                "location": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
        },
    },
    {
        "name": "get_criteria",
        "description": "Read the user's current search criteria.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "update_criteria",
        "description": (
            "Update the user's search criteria. Only include fields the user "
            "actually asked to change; omitted fields are left unchanged."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "role_types": {"type": "array", "items": {"type": "string", "enum": ["new_grad", "intern"]}},
                "keywords": {"type": "array", "items": {"type": "string"}},
                "locations": {"type": "array", "items": {"type": "string"}},
                "sponsorship_required": {"type": "boolean"},
                "freeform_notes": {"type": "string"},
            },
        },
    },
    {
        "name": "add_watchlist_company",
        "description": (
            "Track a specific company the user named — recognize a company name "
            "mentioned in conversation (e.g. 'let me know about anything at "
            "Stripe') and call this to start tracking it. Auto-detects the "
            "company's public job board; if none is found, tell the user we "
            "can't auto-track that company yet."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "remove_watchlist_company",
        "description": "Stop tracking a company the user previously asked to watch.",
        "input_schema": {
            "type": "object",
            "properties": {"company_name": {"type": "string"}},
            "required": ["company_name"],
        },
    },
    {
        "name": "list_watchlist",
        "description": "List the companies the user is currently tracking, and whether each is actively being monitored.",
        "input_schema": {"type": "object", "properties": {}},
    },
]

_VALID_ROLE_TYPES = {rt.value for rt in RoleType}


def _validate_criteria_changes(changes: dict) -> tuple[dict, list[str]]:
    """Best-effort validation of assistant-supplied criteria changes before
    they're persisted. Unknown/malformed fields are dropped (not stored)
    rather than crashing later in matching (see: _criteria_row_to_domain,
    which raises on an invalid RoleType). Returns (clean_changes, warnings)."""
    clean: dict = {}
    warnings: list[str] = []

    if "role_types" in changes:
        raw = changes["role_types"]
        values = raw if isinstance(raw, list) else [raw]
        valid = [v for v in values if isinstance(v, str) and v in _VALID_ROLE_TYPES]
        dropped = [v for v in values if v not in valid]
        if dropped:
            warnings.append(f"ignored unrecognized role_types: {dropped!r}")
        clean["role_types"] = valid

    for key in ("keywords", "locations"):
        if key in changes:
            raw = changes[key]
            values = raw if isinstance(raw, list) else [raw]
            clean[key] = [str(v) for v in values if isinstance(v, (str, int, float))]

    if "sponsorship_required" in changes:
        raw = changes["sponsorship_required"]
        if isinstance(raw, bool):
            clean["sponsorship_required"] = raw
        else:
            warnings.append("ignored non-boolean sponsorship_required")

    if "freeform_notes" in changes:
        clean["freeform_notes"] = str(changes["freeform_notes"])[:2000]

    return clean, warnings


async def search_postings(
    session: AsyncSession,
    role_type: str | None = None,
    keyword: str | None = None,
    location: str | None = None,
    limit: int = 10,
) -> list[dict]:
    stmt = select(PostingRow).where(PostingRow.status == "open").limit(min(limit, 25))
    if role_type:
        stmt = stmt.where(PostingRow.role_type == role_type)
    rows = (await session.execute(stmt)).scalars().all()

    keyword_lower = keyword.lower() if keyword else None
    location_lower = location.lower() if location else None
    results = []
    for row in rows:
        if keyword_lower and keyword_lower not in f"{row.title} {row.company}".lower():
            continue
        if location_lower and location_lower not in (row.location or "").lower():
            continue
        results.append(
            {"company": row.company, "title": row.title, "location": row.location, "url": row.url}
        )
    return results[:limit]


async def get_criteria(session: AsyncSession, user_id: int) -> dict:
    row = (
        await session.execute(select(CriteriaRow).where(CriteriaRow.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        return {}
    return {
        "role_types": row.role_types,
        "keywords": row.keywords,
        "locations": row.locations,
        "sponsorship_required": row.sponsorship_required,
        "freeform_notes": row.freeform_notes,
    }


async def update_criteria(session: AsyncSession, user_id: int, changes: dict) -> dict:
    """Persist criteria changes from the assistant. Inputs are validated
    first (see _validate_criteria_changes) so a malformed/hallucinated LLM
    tool call can't store an invalid role_type that would later crash
    matching (_criteria_row_to_domain does RoleType(rt) with no guard)."""
    clean_changes, warnings = _validate_criteria_changes(changes)

    row = (
        await session.execute(select(CriteriaRow).where(CriteriaRow.user_id == user_id))
    ).scalar_one_or_none()
    if row is None:
        row = CriteriaRow(
            user_id=user_id,
            role_types=[],
            keywords=[],
            locations=[],
            sponsorship_required=None,
            freeform_notes="",
            updated_at=datetime.now(UTC),
        )

    if "role_types" in clean_changes:
        row.role_types = clean_changes["role_types"]
    if "keywords" in clean_changes:
        row.keywords = clean_changes["keywords"]
    if "locations" in clean_changes:
        row.locations = clean_changes["locations"]
    if "sponsorship_required" in clean_changes:
        row.sponsorship_required = clean_changes["sponsorship_required"]
    if "freeform_notes" in clean_changes:
        row.freeform_notes = clean_changes["freeform_notes"]
    row.updated_at = datetime.now(UTC)

    session.add(row)
    await session.flush()
    result = await get_criteria(session, user_id)
    if warnings:
        result["warnings"] = warnings
    return result


async def add_watchlist_company(session: AsyncSession, user_id: int, company_name: str) -> dict:
    return await watchlist_service.add_company(session, user_id, company_name)


async def remove_watchlist_company(session: AsyncSession, user_id: int, company_name: str) -> dict:
    return await watchlist_service.remove_company(session, user_id, company_name)


async def list_watchlist(session: AsyncSession, user_id: int) -> list[dict]:
    return await watchlist_service.list_companies(session, user_id)
