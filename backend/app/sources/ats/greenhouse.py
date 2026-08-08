from __future__ import annotations

import logging

import httpx

from app.domain.models import RawPosting
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)


class GreenhouseSource(Source):
    """Adapter for a company's public Greenhouse job board API."""

    def __init__(self, company_slug: str) -> None:
        self.company_slug = company_slug
        self.name = f"greenhouse:{company_slug}"
        self._last_count: int | None = None

    def _url(self) -> str:
        return f"https://boards-api.greenhouse.io/v1/boards/{self.company_slug}/jobs"

    async def check_for_changes(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get(self._url())
                response.raise_for_status()
                jobs = response.json().get("jobs", [])
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: check_for_changes failed", self.name, exc_info=True)
            return True
        changed = len(jobs) != self._last_count
        self._last_count = len(jobs)
        return changed

    async def fetch(self) -> list[RawPosting]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(self._url(), params={"content": "true"})
                response.raise_for_status()
                jobs = response.json().get("jobs", [])
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
        location = (job.get("location") or {}).get("name")
        return RawPosting(
            source=self.name,
            company=self.company_slug,
            title=title,
            url=job["absolute_url"],
            location=location,
            role_type=infer_role_type(title),
            posted_at=None,
            raw=job,
        )
