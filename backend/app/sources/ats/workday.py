from __future__ import annotations

import logging

import httpx

from app.domain.models import RawPosting
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)

_PAGE_SIZE = 20  # Workday rejects larger values on public career-site APIs.


class WorkdaySource(Source):
    """Adapter for Workday's public, tenant-specific career-site endpoint.

    Large Workday boards commonly report 1,000-2,000 roles and expose only
    20 per page. `max_pages` deliberately bounds each slow-lane fetch to the
    newest result window so adding generalist employers does not create
    hundreds of requests per company every 15 minutes.
    """

    def __init__(
        self,
        *,
        host: str,
        tenant: str,
        site: str,
        company: str,
        max_pages: int = 10,
    ) -> None:
        self.host = host.strip().removeprefix("https://").rstrip("/")
        self.tenant = tenant
        self.site = site
        self.company = company
        self.max_pages = max(1, min(max_pages, 25))
        self.name = f"workday:{tenant}:{site}"
        self._last_fingerprint: tuple[int, tuple[str, ...]] | None = None

    def _url(self) -> str:
        return f"https://{self.host}/wday/cxs/{self.tenant}/{self.site}/jobs"

    async def _fetch_page(self, client: httpx.AsyncClient, offset: int) -> dict:
        response = await client.post(
            self._url(),
            json={
                "appliedFacets": {},
                "limit": _PAGE_SIZE,
                "offset": offset,
                "searchText": "",
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("jobPostings"), list):
            raise TypeError("unexpected Workday jobs payload")
        return payload

    @staticmethod
    def _fingerprint(payload: dict) -> tuple[int, tuple[str, ...]]:
        total = int(payload.get("total") or 0)
        paths = tuple(
            str(job.get("externalPath", ""))
            for job in payload.get("jobPostings", [])
            if isinstance(job, dict)
        )
        return total, paths

    async def check_for_changes(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                first = await self._fetch_page(client, 0)
        except (httpx.HTTPError, TypeError, ValueError):
            logger.warning("%s: check_for_changes failed", self.name, exc_info=True)
            return True
        fingerprint = self._fingerprint(first)
        changed = fingerprint != self._last_fingerprint
        self._last_fingerprint = fingerprint
        return changed

    async def fetch(self) -> list[RawPosting]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                first = await self._fetch_page(client, 0)
                total = int(first.get("total") or 0)
                pages = min(self.max_pages, max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE))
                payloads = [first]
                for page in range(1, pages):
                    payloads.append(await self._fetch_page(client, page * _PAGE_SIZE))
        except (httpx.HTTPError, TypeError, ValueError):
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []

        self._last_fingerprint = self._fingerprint(first)
        postings: list[RawPosting] = []
        for payload in payloads:
            for job in payload["jobPostings"]:
                try:
                    postings.append(self._to_raw_posting(job))
                except (KeyError, TypeError):
                    logger.debug("%s: skipping malformed posting: %r", self.name, job)
        return postings

    def _to_raw_posting(self, job: dict) -> RawPosting:
        title = job["title"]
        external_path = job["externalPath"]
        return RawPosting(
            source=self.name,
            company=self.company,
            title=title,
            url=f"https://{self.host}/en-US/{self.site}{external_path}",
            location=job.get("locationsText"),
            role_type=infer_role_type(title),
            posted_at=None,
            raw=job,
        )
