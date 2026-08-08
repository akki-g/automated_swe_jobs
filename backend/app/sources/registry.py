from __future__ import annotations

from app.sources.ats import build_ats_sources
from app.sources.ats.ashby import AshbySource
from app.sources.ats.greenhouse import GreenhouseSource
from app.sources.ats.lever import LeverSource
from app.sources.base import Source
from app.sources.github_lists import internship_source, new_grad_source

_PROVIDER_CLASSES: dict[str, type[Source]] = {
    "greenhouse": GreenhouseSource,
    "lever": LeverSource,
    "ashby": AshbySource,
}


class SourceRegistry:
    """Process-lifetime registry of Source instances.

    Reliable-tier sources carry change-detection state on the instance
    (_last_count / _last_etag — see GreenhouseSource, GitHubListSource) that
    only means anything if the *same* instance is reused across scheduler
    ticks. Building a fresh Source every cycle silently defeats
    check_for_changes() (it always looks "changed" against a blank slate),
    which is why this registry exists: every caller — the curated
    companies.yaml list and the dynamic watchlist — gets the same object
    back for a given (provider, slug) for the lifetime of the process.
    """

    def __init__(self) -> None:
        self._by_name: dict[str, Source] = {}
        self._curated_loaded = False

    def _add(self, source: Source) -> Source:
        return self._by_name.setdefault(source.name, source)

    def ensure_curated_sources(self) -> None:
        """Load the companies.yaml-driven sources once. Safe to call every
        cycle — a no-op after the first call."""
        if self._curated_loaded:
            return
        self._add(new_grad_source())
        self._add(internship_source())
        for source in build_ats_sources():
            self._add(source)
        self._curated_loaded = True

    def get_or_create_ats_source(self, provider: str, slug: str) -> Source:
        cls = _PROVIDER_CLASSES[provider]
        name = f"{provider}:{slug}"
        existing = self._by_name.get(name)
        if existing is not None:
            return existing
        return self._add(cls(slug))

    def reliable_tier_sources(self) -> list[Source]:
        """The curated (companies.yaml) reliable-tier sources only — callers
        that also want watchlist sources should union in
        app.watchlist.service.sync_watchlist_sources()'s result."""
        self.ensure_curated_sources()
        return list(self._by_name.values())

    def reset(self) -> None:
        """Test-only: clear all cached instances."""
        self._by_name.clear()
        self._curated_loaded = False


registry = SourceRegistry()
