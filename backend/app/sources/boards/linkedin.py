from __future__ import annotations

from app.domain.models import RawPosting
from app.sources.base import Source


class LinkedInSource(Source):
    """Placeholder for LinkedIn as a best-effort source (see spec: Sources —
    Big boards). Not implemented: LinkedIn has no public jobs API for this
    use case, and scraping its site violates its Terms of Service and is
    actively defended against (IP bans, JS challenges, and CFAA claims in
    past litigation — see spec's own callout). This class exists so the
    Source protocol's shape is visible in the codebase; it is intentionally
    never registered in default_sources() and fetch() always returns empty
    rather than scraping.
    """

    name = "linkedin"

    async def fetch(self) -> list[RawPosting]:
        return []
