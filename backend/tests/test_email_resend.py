import pytest

from app.notify.email_resend import ResendEmailProvider


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError("error", request=None, response=self)


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

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return self._response


def _patch_client(monkeypatch, response: _FakeResponse) -> _FakeAsyncClient:
    fake = _FakeAsyncClient(response)

    def factory(*args, **kwargs):
        return fake

    monkeypatch.setattr("app.notify.email_resend.httpx.AsyncClient", factory)
    return fake


@pytest.mark.asyncio
async def test_send_success_posts_expected_payload(monkeypatch):
    fake = _patch_client(monkeypatch, _FakeResponse(200))
    provider = ResendEmailProvider(api_key="key123", from_email="alerts@example.com")

    result = await provider.send("user@example.com", "New matches", "body text")

    assert result is True
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"] == {"Authorization": "Bearer key123"}
    assert call["json"] == {
        "from": "alerts@example.com",
        "to": ["user@example.com"],
        "subject": "New matches",
        "text": "body text",
    }


@pytest.mark.asyncio
async def test_send_includes_html_part_when_given(monkeypatch):
    fake = _patch_client(monkeypatch, _FakeResponse(200))
    provider = ResendEmailProvider(api_key="key123", from_email="alerts@example.com")

    await provider.send("user@example.com", "New matches", "body text", html="<p>body</p>")

    assert fake.calls[0]["json"] == {
        "from": "alerts@example.com",
        "to": ["user@example.com"],
        "subject": "New matches",
        "text": "body text",
        "html": "<p>body</p>",
    }


@pytest.mark.asyncio
async def test_send_returns_false_on_http_error(monkeypatch):
    _patch_client(monkeypatch, _FakeResponse(422))
    provider = ResendEmailProvider(api_key="key123", from_email="alerts@example.com")

    result = await provider.send("user@example.com", "subject", "body")

    assert result is False
