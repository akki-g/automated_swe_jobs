from __future__ import annotations

from app.config import settings
from app.sources.aggregators import (
    PAGESXYZ_CATEGORIES,
    PAGESXYZ_LIMIT_PER_CATEGORY,
    PagesXyzSource,
)
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
        # Kept in a separate dict from _by_name (not just a separate flag) so
        # reliable_tier_sources()'s `list(self._by_name.values())` can never
        # accidentally include a best-effort aggregator — the fast lane must
        # never see these regardless of how this class evolves later.
        self._aggregators_by_name: dict[str, Source] = {}
        self._aggregators_loaded = False

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

    def ensure_aggregator_sources(self) -> None:
        """Load best-effort aggregator sources once (slow-lane only — see
        default_sources()). Each is gated on its own config being present so
        an unconfigured aggregator is silently absent rather than erroring."""
        if self._aggregators_loaded:
            return
        if settings.pagesxyz_api_key:
            for category in PAGESXYZ_CATEGORIES:
                source = PagesXyzSource(
                    api_key=settings.pagesxyz_api_key,
                    category=category,
                    limit=PAGESXYZ_LIMIT_PER_CATEGORY,
                )
                self._aggregators_by_name.setdefault(source.name, source)
        self._aggregators_loaded = True

    def aggregator_sources(self) -> list[Source]:
        """Best-effort, slow-lane-only sources (see spec: Sources —
        Aggregator feeds) — kept out of reliable_tier_sources() entirely, so
        the fast lane never touches them."""
        self.ensure_aggregator_sources()
        return list(self._aggregators_by_name.values())

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
        self._aggregators_by_name.clear()
        self._aggregators_loaded = False


registry = SourceRegistry()
