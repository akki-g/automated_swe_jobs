from __future__ import annotations

import pytest

from app.domain.models import RoleType
from app.sources.ats.workday import WorkdaySource


def _payload(offset: int, total: int = 25) -> dict:
    return {
        "total": total,
        "jobPostings": [
            {
                "title": "2027 Analyst, Structured Credit" if offset == 0 else "Marketing Graduate Program",
                "externalPath": f"/job/Boston/role-{offset}",
                "locationsText": "Boston",
                "postedOn": "Posted Today",
            }
        ],
    }


@pytest.mark.asyncio
async def test_workday_fetch_paginates_with_explicit_bound(monkeypatch):
    source = WorkdaySource(
        host="example.wd1.myworkdayjobs.com",
        tenant="example",
        site="Careers",
        company="Example Co",
        max_pages=2,
    )
    offsets = []

    async def fake_page(_client, offset):
        offsets.append(offset)
        return _payload(offset, total=2000)

    monkeypatch.setattr(source, "_fetch_page", fake_page)
    postings = await source.fetch()

    assert offsets == [0, 20]
    assert len(postings) == 2
    assert postings[0].company == "Example Co"
    assert postings[0].role_type == RoleType.NEW_GRAD
    assert postings[0].url == "https://example.wd1.myworkdayjobs.com/en-US/Careers/job/Boston/role-0"


@pytest.mark.asyncio
async def test_workday_change_detection_uses_first_page_identity(monkeypatch):
    source = WorkdaySource(
        host="example.wd1.myworkdayjobs.com",
        tenant="example",
        site="Careers",
        company="Example Co",
    )
    payloads = [_payload(0), _payload(0), _payload(1)]

    async def fake_page(_client, _offset):
        return payloads.pop(0)

    monkeypatch.setattr(source, "_fetch_page", fake_page)
    assert await source.check_for_changes() is True
    assert await source.check_for_changes() is False
    assert await source.check_for_changes() is True
