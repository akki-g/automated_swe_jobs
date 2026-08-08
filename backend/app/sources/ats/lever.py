from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.domain.models import RawPosting
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)


class LeverSource(Source):
    """Adapter for a company's public Lever job postings API."""

    def __init__(self, company_slug: str) -> None:
        self.company_slug = company_slug
        self.name = f"lever:{company_slug}"
        self._last_count: int | None = None

    def _url(self) -> str:
        return f"https://api.lever.co/v0/postings/{self.company_slug}"

    async def _fetch_raw(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self._url(), params={"mode": "json"})
            response.raise_for_status()
            return response.json()

    async def check_for_changes(self) -> bool:
        try:
            postings = await self._fetch_raw()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: check_for_changes failed", self.name, exc_info=True)
            return True
        changed = len(postings) != self._last_count
        self._last_count = len(postings)
        return changed

    async def fetch(self) -> list[RawPosting]:
        try:
            postings = await self._fetch_raw()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []

        self._last_count = len(postings)
        results = []
        for posting in postings:
            try:
                results.append(self._to_raw_posting(posting))
            except (KeyError, TypeError):
                logger.debug("%s: skipping malformed posting: %r", self.name, posting)
        return results

    def _to_raw_posting(self, posting: dict) -> RawPosting:
        title = posting["text"]
        categories = posting.get("categories") or {}
        location = categories.get("location")
        posted_at = None
        if posting.get("createdAt") is not None:
            try:
                posted_at = datetime.fromtimestamp(posting["createdAt"] / 1000, tz=UTC)
            except (TypeError, ValueError, OSError):
                posted_at = None
        return RawPosting(
            source=self.name,
            company=self.company_slug,
            title=title,
            url=posting["hostedUrl"],
            location=location,
            role_type=infer_role_type(title),
            posted_at=posted_at,
            raw=posting,
        )
