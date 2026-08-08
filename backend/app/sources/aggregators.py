from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime

import httpx

from app.domain.models import RawPosting
from app.sources.ats.common import infer_role_type
from app.sources.base import Source

logger = logging.getLogger(__name__)


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
            if title_el is None or link_el is None or not title_el.text or not link_el.text:
                continue
            postings.append(self._to_raw_posting(item, title_el.text, link_el.text))
        return postings

    def _to_raw_posting(self, item: ET.Element, raw_title: str, link: str) -> RawPosting:
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
