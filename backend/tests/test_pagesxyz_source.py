import pytest

from app.domain.models import RoleType
from app.sources.aggregators import PagesXyzSource


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._payload


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient (matches the project's fixture-based
    provider tests, e.g. test_watchlist_detect.py)."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(response)

    def factory(*args, **kwargs):
        return fake

    monkeypatch.setattr("app.sources.aggregators.httpx.AsyncClient", factory)
    return fake


SAMPLE_ENTRIES = [
    {
        "id": "acme-1",
        "company": "acme",
        "company_name": "Acme",
        "title": "Software Engineer New Grad",
        "level": "",
        "location": "Remote",
        "apply_link": "https://example.com/apply/1",
        "posted_at": "2026-08-08T03:14:39.529541+00:00",
        "categories": "software-engineering",
    },
    {
        "id": "gamma-1",
        "company": "gamma",
        "company_name": "Gamma",
        "title": "Marketing Coordinator",
        "level": "Entry Level",
        "location": "Chicago",
        "apply_link": "https://example.com/apply/3",
        "posted_at": "2026-08-19T03:14:39.529541+00:00",
        "categories": "marketing",
    },
    {
        "id": "beta-1",
        "company": "beta",
        "company_name": "Beta Corp",
        "title": "Senior Site Reliability Engineer",
        "level": "Senior",
        "location": "NYC",
        "apply_link": "https://example.com/apply/2",
        "posted_at": None,
        "categories": "software-engineering",
    },
]


@pytest.mark.asyncio
async def test_fetch_sends_api_key_header_and_expected_params(monkeypatch):
    fake = _patch_client(monkeypatch, _FakeResponse(200, SAMPLE_ENTRIES))
    source = PagesXyzSource(api_key="test-key")

    postings = await source.fetch()

    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["headers"] == {"apikey": "test-key"}
    assert call["params"]["categories"] == "ilike.%software-engineering%"
    assert len(postings) == 3


@pytest.mark.asyncio
async def test_fetch_maps_fields_and_infers_role_type(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(200, SAMPLE_ENTRIES))
    source = PagesXyzSource(api_key="test-key")

    postings = await source.fetch()

    new_grad = next(p for p in postings if p.company == "Acme")
    assert new_grad.title == "Software Engineer New Grad"
    assert new_grad.url == "https://example.com/apply/1"
    assert new_grad.role_type == RoleType.NEW_GRAD
    assert new_grad.posted_at is not None

    senior = next(p for p in postings if p.company == "Beta Corp")
    assert (
        senior.role_type is None
    )  # not new-grad/intern-shaped, correctly unclassified
    assert senior.posted_at is None

    structured_entry_level = next(p for p in postings if p.company == "Gamma")
    assert structured_entry_level.role_type == RoleType.NEW_GRAD


@pytest.mark.asyncio
async def test_fetch_returns_empty_on_http_error(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(401, None))
    source = PagesXyzSource(api_key="bad-key")

    postings = await source.fetch()

    assert postings == []


@pytest.mark.asyncio
async def test_fetch_skips_malformed_entries(monkeypatch):
    entries = [{"id": "missing-fields", "company_name": "Acme"}]
    _patch_client(monkeypatch, _FakeResponse(200, entries))
    source = PagesXyzSource(api_key="test-key")

    postings = await source.fetch()

    assert postings == []


def test_source_name_reflects_category():
    source = PagesXyzSource(api_key="test-key", category="finance")
    assert source.name == "pagesxyz:finance"


def test_source_has_no_check_for_changes():
    """Best-effort/slow-lane-only — must fall back to the base Source's
    NotImplementedError so callers never mistake this for a fast-lane
    source."""
    source = PagesXyzSource(api_key="test-key")
    assert not hasattr(source, "_last_count")
    with pytest.raises(NotImplementedError):
        import asyncio

        asyncio.run(source.check_for_changes())
