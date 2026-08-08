from __future__ import annotations

import re

from app.domain.models import Posting, RawPosting

_SUFFIX_PATTERN = re.compile(
    r"\b(?:inc|llc|ltd|corp|corporation|co)\.?(?=\s|$)|\(remote\)|\(hybrid\)|\(onsite\)",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_component(value: str | None) -> str:
    if not value:
        return ""
    stripped = _SUFFIX_PATTERN.sub("", value)
    collapsed = _WHITESPACE_PATTERN.sub(" ", stripped).strip().lower()
    return collapsed


def normalize_company_key(company: str) -> str:
    """Public wrapper around the same company-name normalization
    build_posting_key uses, for callers outside ingest/ that need to compare
    a company name against posting identity (e.g. app.watchlist matching a
    watched company against incoming postings)."""
    return _normalize_component(company)


def build_posting_key(company: str, title: str, location: str | None) -> str:
    """Identity key for a posting, built from (company, title, location) rather
    than the source URL or a per-source ID — the same role commonly appears
    across multiple sources with different URLs (see spec: posting_key
    construction). This is a heuristic, not a guarantee.
    """
    return "|".join(
        (
            _normalize_component(company),
            _normalize_component(title),
            _normalize_component(location),
        )
    )


def normalize(raw: RawPosting) -> Posting:
    return Posting(
        posting_key=build_posting_key(raw.company, raw.title, raw.location),
        source=raw.source,
        company=raw.company,
        title=raw.title,
        url=raw.url,
        location=raw.location,
        role_type=raw.role_type,
        posted_at=raw.posted_at,
        raw=raw.raw,
    )
