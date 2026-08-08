from __future__ import annotations

from app.domain.models import RawPosting
from app.sources.base import Source


class IndeedSource(Source):
    """Placeholder for Indeed as a best-effort source (see spec: Sources —
    Big boards). Not implemented for the same ToS/anti-bot reasons as
    LinkedInSource; Indeed does offer a publisher/affiliate API in some
    regions, which would be the ToS-compliant way to integrate this source
    if pursued later, rather than HTML scraping. Never registered in
    default_sources(); fetch() always returns empty.
    """

    name = "indeed"

    async def fetch(self) -> list[RawPosting]:
        return []
