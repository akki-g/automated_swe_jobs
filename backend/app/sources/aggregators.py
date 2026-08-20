from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import httpx

from app.domain.models import RawPosting, TargetField
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)

# pagesxyz exposes one category filter per request. Register a deliberately
# broad set so this best-effort tier supplements the tech-heavy GitHub lists
# for every field users can select during onboarding.
PAGESXYZ_CATEGORIES_BY_FIELD: dict[TargetField, tuple[str, ...]] = {
    TargetField.SOFTWARE_ENGINEERING: (
        "software-engineering",
        "information-technology",
    ),
    TargetField.DATA_SCIENCE_ANALYTICS: ("data-analysis", "data-science"),
    TargetField.PRODUCT_MANAGEMENT: ("product-management",),
    TargetField.FINANCE_INVESTMENT_BANKING: (
        "corporate-finance",
        "accounting",
        "wealth-management",
        "venture-capital",
        "equity-research",
        "middle-office",
    ),
    TargetField.CONSULTING: ("consulting",),
    TargetField.MARKETING: ("marketing", "communications"),
    TargetField.SALES: ("sales-broad", "sales-engineering", "customer-success"),
    TargetField.OPERATIONS: ("operations-management", "logistics", "supply-chain"),
    TargetField.DESIGN: ("product-design",),
}
PAGESXYZ_CATEGORIES = tuple(
    dict.fromkeys(
        category
        for categories in PAGESXYZ_CATEGORIES_BY_FIELD.values()
        for category in categories
    )
)
# Twenty-one category requests at the adapter's 1,000-row default could add
# 21,000 rows to one slow-lane batch before reliable sources are included.
# The newest 500 per category retains broad coverage while keeping the
# combined posting-key lookup below common database bind-parameter limits.
PAGESXYZ_LIMIT_PER_CATEGORY = 500


class RssFeedSource(Source):
    """Generic adapter for an RSS/JSON job feed built for consumption (see
    spec: Sources — Aggregator feeds). Slow-lane only: RSS feeds don't expose
    a cheap change signal, so this does not implement check_for_changes().

    Company is best-effort: RSS job feeds rarely carry a structured company
    field, so we parse it from a "Company - Title" pattern in the item title
    when present, falling back to the feed name. This degrades posting_key
    quality for this tier relative to ATS/GitHub sources (see spec's own
    caveat on dedupe being a heuristic, not a guarantee).
    """

    def __init__(self, name: str, feed_url: str) -> None:
        self.name = name
        self.feed_url = feed_url

    async def fetch(self) -> list[RawPosting]:
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(self.feed_url)
                response.raise_for_status()
                return self._parse(response.text)
        except httpx.HTTPError:
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []
        except ET.ParseError:
            logger.warning("%s: feed XML could not be parsed", self.name, exc_info=True)
            return []

    def _parse(self, xml_text: str) -> list[RawPosting]:
        root = ET.fromstring(xml_text)
        postings = []
        for item in root.iter("item"):
            title_el = item.find("title")
            link_el = item.find("link")
            if (
                title_el is None
                or link_el is None
                or not title_el.text
                or not link_el.text
            ):
                continue
            postings.append(self._to_raw_posting(item, title_el.text, link_el.text))
        return postings

    def _to_raw_posting(
        self, item: ET.Element, raw_title: str, link: str
    ) -> RawPosting:
        company, title = self._split_title(raw_title)
        pub_date_el = item.find("pubDate")
        posted_at: datetime | None = None
        if pub_date_el is not None and pub_date_el.text:
            try:
                posted_at = parsedate_to_datetime(pub_date_el.text)
            except (TypeError, ValueError):
                posted_at = None
        return RawPosting(
            source=self.name,
            company=company,
            title=title,
            url=link,
            location=None,
            role_type=infer_role_type(title),
            posted_at=posted_at,
            raw={"raw_title": raw_title},
        )

    def _split_title(self, raw_title: str) -> tuple[str, str]:
        if " - " in raw_title:
            company, _, title = raw_title.partition(" - ")
            return company.strip(), title.strip()
        return self.name, raw_title.strip()


class PagesXyzSource(Source):
    """Adapter for pagesxyz.com's job listings — a third-party board, not a
    documented public API. Its frontend calls a Supabase PostgREST endpoint
    directly (found via browser devtools, not published anywhere); we call
    the same endpoint with the same publishable ("anon"-style, safe to be
    client-exposed) API key its own frontend uses.

    Best-effort tier by design (see spec: Sources — same reasoning as
    LinkedIn/Indeed): this is someone else's backend, not a stable contract,
    so it's slow-lane only (no check_for_changes()) and isolated here so a
    breaking change on their end never touches the reliable ATS/GitHub
    backbone.
    """

    _URL = "https://frlkrjbedjjrtrknuunq.supabase.co/rest/v1/jobs"
    _SELECT = (
        "id,company,company_name,title,level,location,apply_link,"
        "salary_min,salary_max,years_min,years_max,posted_at,categories"
    )

    def __init__(
        self, api_key: str, category: str = "software-engineering", limit: int = 1000
    ) -> None:
        self.name = f"pagesxyz:{category}"
        self.api_key = api_key
        self.category = category
        self.limit = limit

    async def fetch(self) -> list[RawPosting]:
        params = {
            "select": self._SELECT,
            "order": "posted_at.desc,id.asc",
            "categories": f"ilike.%{self.category}%",
            "offset": "0",
            "limit": str(self.limit),
        }
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                response = await client.get(
                    self._URL, params=params, headers={"apikey": self.api_key}
                )
                response.raise_for_status()
                entries = response.json()
        except (httpx.HTTPError, ValueError):
            logger.warning("%s: fetch failed", self.name, exc_info=True)
            return []

        postings: list[RawPosting] = []
        for entry in entries:
            try:
                postings.append(self._to_raw_posting(entry))
            except (KeyError, TypeError):
                logger.debug("%s: skipping malformed entry: %r", self.name, entry)
        return postings

    def _to_raw_posting(self, entry: dict) -> RawPosting:
        title = entry["title"]
        company = entry.get("company_name") or entry.get("company") or self.name
        url = entry["apply_link"]
        if not title or not company or not url:
            raise KeyError("missing required field")

        posted_at: datetime | None = None
        raw_posted = entry.get("posted_at")
        if raw_posted:
            try:
                posted_at = datetime.fromisoformat(
                    raw_posted.replace("Z", "+00:00")
                ).astimezone(UTC)
            except (TypeError, ValueError):
                posted_at = None

        return RawPosting(
            source=self.name,
            company=company,
            title=title,
            url=url,
            location=entry.get("location") or None,
            # This aggregator provides a structured level even when a title
            # itself omits words like "intern" or "entry-level" (common for
            # marketing, sales, operations, and design). Use both signals so
            # those fields are not accidentally filtered out as senior roles.
            role_type=infer_role_type(title, entry.get("level")),
            posted_at=posted_at,
            raw=entry,
        )
