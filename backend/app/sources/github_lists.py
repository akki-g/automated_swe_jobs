from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.domain.models import RawPosting, RoleType
from app.sources.base import Source

logger = logging.getLogger(__name__)

NEW_GRAD_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/"
    "dev/.github/scripts/listings.json"
)
INTERNSHIP_LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2026-Internships/"
    "dev/.github/scripts/listings.json"
)


class GitHubListSource(Source):
    """Adapter for the SimplifyJobs new-grad / internship listing repos.

    These repos publish a structured `listings.json` alongside their README
    tables, which is far cheaper and more stable to parse than scraping the
    rendered README markdown.
    """

    def __init__(self, name: str, listings_url: str, role_type: RoleType) -> None:
        self.name = name
        self.listings_url = listings_url
        self.role_type = role_type
        self._last_etag: str | None = None

    async def check_for_changes(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                headers = {"If-None-Match": self._last_etag} if self._last_etag else {}
                response = await client.head(self.listings_url, headers=headers)
                if response.status_code == 304:
                    return False
                etag = response.headers.get("etag")
                changed = etag != self._last_etag
                self._last_etag = etag
                return changed
        except httpx.HTTPError:
            logger.warning("%s: check_for_changes failed", self.name, exc_info=True)
            return True  # fail open so a transient error doesn't hide new postings

    async def fetch(self) -> list[RawPosting]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(self.listings_url)
                response.raise_for_status()
                self._last_etag = response.headers.get("etag")
                entries = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []

        postings: list[RawPosting] = []
        for entry in entries:
            if entry.get("active") is False or entry.get("is_visible") is False:
                continue
            try:
                postings.append(self._to_raw_posting(entry))
            except (KeyError, TypeError, ValueError):
                logger.debug("%s: skipping malformed entry: %r", self.name, entry)
        return postings

    def _to_raw_posting(self, entry: dict) -> RawPosting:
        company = entry.get("company_name") or entry.get("company") or ""
        title = entry.get("title") or entry.get("role") or ""
        url = entry.get("url") or entry.get("job_url") or entry.get("company_url") or ""
        locations = entry.get("locations") or entry.get("location") or []
        if isinstance(locations, list):
            location = "; ".join(str(loc) for loc in locations) or None
        else:
            location = str(locations) or None

        posted_at = None
        raw_date = entry.get("date_posted") or entry.get("date_updated")
        if raw_date is not None:
            try:
                posted_at = datetime.fromtimestamp(float(raw_date), tz=UTC)
            except (TypeError, ValueError, OSError):
                posted_at = None

        if not company or not title or not url:
            raise ValueError("missing required field")

        return RawPosting(
            source=self.name,
            company=company,
            title=title,
            url=url,
            location=location,
            role_type=self.role_type,
            posted_at=posted_at,
            raw=entry,
        )


def new_grad_source() -> GitHubListSource:
    return GitHubListSource("github_new_grad", NEW_GRAD_LISTINGS_URL, RoleType.NEW_GRAD)


def internship_source() -> GitHubListSource:
    return GitHubListSource("github_internship", INTERNSHIP_LISTINGS_URL, RoleType.INTERN)
