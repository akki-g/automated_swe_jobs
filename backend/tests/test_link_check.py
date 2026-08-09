import httpx
import pytest

from app.ingest.link_check import check_link_alive


class _FakeStreamResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeClient:
    def __init__(self, status_code: int | None = None, raise_error: bool = False):
        self._status_code = status_code
        self._raise_error = raise_error
        self.requested_urls: list[str] = []

    def stream(self, method, url):
        self.requested_urls.append(url)
        if self._raise_error:
            raise httpx.ConnectError("boom")
        return _FakeStreamResponse(self._status_code)


@pytest.mark.asyncio
async def test_check_link_alive_true_for_200():
    client = _FakeClient(status_code=200)
    assert await check_link_alive("https://example.com/job/1", client) is True


@pytest.mark.parametrize("status", [404, 410, 451])
@pytest.mark.asyncio
async def test_check_link_alive_false_for_dead_status_codes(status):
    client = _FakeClient(status_code=status)
    assert await check_link_alive("https://example.com/job/1", client) is False


@pytest.mark.asyncio
async def test_check_link_alive_none_on_server_error():
    """A 5xx says nothing about whether the listing itself is gone — must
    not be treated as dead."""
    client = _FakeClient(status_code=503)
    assert await check_link_alive("https://example.com/job/1", client) is None


@pytest.mark.asyncio
async def test_check_link_alive_none_on_network_error():
    """A timeout/connection error is inconclusive, not evidence of death —
    fail open (same philosophy as the rest of this codebase's source error
    handling)."""
    client = _FakeClient(raise_error=True)
    assert await check_link_alive("https://example.com/job/1", client) is None


@pytest.mark.asyncio
async def test_check_link_alive_false_for_empty_url():
    client = _FakeClient(status_code=200)
    assert await check_link_alive("", client) is False
    assert await check_link_alive("   ", client) is False
    assert client.requested_urls == []  # never even attempted a request


@pytest.mark.asyncio
async def test_check_link_alive_true_for_redirect_followed_to_success():
    # A client configured with follow_redirects=True would already resolve
    # this to the final status; check_link_alive itself is redirect-agnostic
    # and just reads whatever status the client's stream() call reports.
    client = _FakeClient(status_code=200)
    assert await check_link_alive("https://example.com/job/1", client) is True


@pytest.mark.asyncio
async def test_unparseable_url_is_inconclusive_rather_than_raising():
    """A URL httpx rejects before sending must come back as inconclusive,
    not as an exception. Not every such rejection is an httpx.HTTPError —
    httpx.InvalidURL derives straight from Exception — and one escaping here
    would abort the caller's asyncio.gather, costing the whole cycle its
    link validation rather than just this posting's.
    """

    class ExplodingClient:
        def stream(self, method, url):
            raise httpx.InvalidURL("malformed")

    assert await check_link_alive("http://exa mple.com/job", ExplodingClient()) is None


@pytest.mark.asyncio
async def test_a_single_bad_url_does_not_disable_validation_for_the_batch():
    """End-to-end version of the above, at the pipeline's gather."""
    from app.pipeline import _find_dead_links

    class Row:
        def __init__(self, key, url):
            self.posting_key, self.url = key, url

    rows = [Row("good", "https://example.com/live"), Row("bad", "http://exa mple.com/x"),
            Row("gone", "https://example.com/dead")]

    async def _check(url, client):
        if "exa mple" in url:
            raise httpx.InvalidURL("malformed")
        return False if url.endswith("dead") else True

    import app.pipeline as pipeline_module

    original = pipeline_module.check_link_alive
    pipeline_module.check_link_alive = _check
    try:
        dead = await _find_dead_links(rows)
    finally:
        pipeline_module.check_link_alive = original

    # The bad URL is inconclusive (fail open), and crucially the genuinely
    # dead posting is still detected instead of the batch being abandoned.
    assert dead == {"gone"}
