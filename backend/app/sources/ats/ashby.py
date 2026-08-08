from __future__ import annotations

import logging

import httpx

from app.domain.models import RawPosting
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)


class AshbySource(Source):
    """Adapter for a company's public Ashby job board API."""

    def __init__(self, board_name: str) -> None:
        self.board_name = board_name
        self.name = f"ashby:{board_name}"
        self._last_count: int | None = None

    def _url(self) -> str:
        return f"https://api.ashbyhq.com/posting-api/job-board/{self.board_name}"

    async def _fetch_raw(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.get(self._url())
            response.raise_for_status()
            return response.json().get("jobs", [])

    async def check_for_changes(self) -> bool:
        try:
            jobs = await self._fetch_raw()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: check_for_changes failed", self.name, exc_info=True)
            return True
        changed = len(jobs) != self._last_count
        self._last_count = len(jobs)
        return changed

    async def fetch(self) -> list[RawPosting]:
        try:
            jobs = await self._fetch_raw()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []

        self._last_count = len(jobs)
        postings = []
        for job in jobs:
            try:
                postings.append(self._to_raw_posting(job))
            except (KeyError, TypeError):
                logger.debug("%s: skipping malformed job: %r", self.name, job)
        return postings

    def _to_raw_posting(self, job: dict) -> RawPosting:
        title = job["title"]
        location = job.get("location")
        return RawPosting(
            source=self.name,
            company=self.board_name,
            title=title,
            url=job["jobUrl"],
            location=location,
            role_type=infer_role_type(title),
            posted_at=None,
            raw=job,
        )
