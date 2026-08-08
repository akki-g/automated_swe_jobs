from __future__ import annotations

from abc import ABC, abstractmethod

from app.domain.models import RawPosting


class Source(ABC):
    """A pluggable job source. A source that errors during fetch() must return
    an empty list rather than raise, so one dead source never sinks a scrape
    cycle (see spec: Error handling principles — Source isolation)."""

    name: str

    @abstractmethod
    async def fetch(self) -> list[RawPosting]:
        ...

    async def check_for_changes(self) -> bool:
        """Cheap change-detection for the fast lane. Only reliable-tier sources
        implement this; others are slow-lane only (see spec: Sources)."""
        raise NotImplementedError(f"{self.name} does not support check_for_changes()")
