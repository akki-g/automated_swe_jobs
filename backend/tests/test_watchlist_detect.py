import pytest

from app.watchlist.detect import candidate_slugs, detect_ats_board


def test_candidate_slugs_single_word():
    assert candidate_slugs("Stripe") == ["stripe"]


def test_candidate_slugs_multi_word_gives_no_space_and_hyphenated():
    assert candidate_slugs("General Motors") == ["generalmotors", "general-motors"]


def test_candidate_slugs_strips_common_suffix_and_punctuation():
    assert candidate_slugs("Acme, Inc.") == ["acme"]


def test_candidate_slugs_empty_for_blank_input():
    assert candidate_slugs("   ") == []


class _FakeResponse:
    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if self._payload is _RAISE:
            raise ValueError("not json")
        return self._payload


_RAISE = object()


class _FakeAsyncClient:
    """Stands in for httpx.AsyncClient; routes GETs by URL prefix so tests
    don't hit the network (matches the project's fixture-based source tests)."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self._responses = responses

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url, params=None):
        for prefix, response in self._responses.items():
            if url.startswith(prefix):
                return response
        return _FakeResponse(404, _RAISE)


def _patch_client(monkeypatch, responses: dict[str, _FakeResponse]) -> None:
    def factory(*args, **kwargs):
        return _FakeAsyncClient(responses)

    monkeypatch.setattr("app.watchlist.detect.httpx.AsyncClient", factory)


@pytest.mark.asyncio
async def test_detect_ats_board_finds_greenhouse_match(monkeypatch):
    _patch_client(
        monkeypatch,
        {"https://boards-api.greenhouse.io/v1/boards/acme/jobs": _FakeResponse(200, {"jobs": []})},
    )

    result = await detect_ats_board("Acme")

    assert result == ("greenhouse", "acme")


@pytest.mark.asyncio
async def test_detect_ats_board_falls_through_to_lever(monkeypatch):
    _patch_client(
        monkeypatch,
        {"https://api.lever.co/v0/postings/acme": _FakeResponse(200, [])},
    )

    result = await detect_ats_board("Acme")

    assert result == ("lever", "acme")


@pytest.mark.asyncio
async def test_detect_ats_board_falls_through_to_ashby(monkeypatch):
    _patch_client(
        monkeypatch,
        {"https://api.ashbyhq.com/posting-api/job-board/acme": _FakeResponse(200, {"jobs": []})},
    )

    result = await detect_ats_board("Acme")

    assert result == ("ashby", "acme")


@pytest.mark.asyncio
async def test_detect_ats_board_returns_none_when_nothing_matches(monkeypatch):
    _patch_client(monkeypatch, {})

    result = await detect_ats_board("Some Totally Unknown Company")

    assert result is None


@pytest.mark.asyncio
async def test_detect_ats_board_returns_none_for_blank_company_name(monkeypatch):
    _patch_client(monkeypatch, {})

    result = await detect_ats_board("   ")

    assert result is None
