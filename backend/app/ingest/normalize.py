from __future__ import annotations

import html
import re

from app.domain.models import Posting, RawPosting

_SUFFIX_PATTERN = re.compile(
    r"\b(?:inc|llc|ltd|corp|corporation|co)\.?(?=\s|$)|\(remote\)|\(hybrid\)|\(onsite\)",
    re.IGNORECASE,
)
_WHITESPACE_PATTERN = re.compile(r"\s+")

# Each of the three posting_key components (company/title/location) is
# capped at this length before joining. Real-world data breaks the naive
# "no cap" assumption in practice — e.g. a Workday posting listing a dozen+
# office locations joined with "; " can run past a thousand characters —
# and an oversized posting_key isn't just truncated on write, it's a hard
# INSERT failure against postings.posting_key's VARCHAR column that (per an
# actual production incident) took down the *entire* batch insert for that
# cycle, not just the one offending row, since store_new_postings only
# retries on IntegrityError, not a value-too-long DataError. Capping each
# component here bounds posting_key's total length deterministically,
# independent of whatever the column width happens to be (see
# db/models.py::Posting.posting_key and the matching migration widening it
# for already-deployed databases).
_MAX_COMPONENT_LENGTH = 150

# These are the persistence limits from db/models.py. Source payloads are
# untrusted and routinely exceed the display-oriented widths we started
# with: the GitHub internship feed currently contains multi-office location
# strings over 500 characters, and one Workday source name is 51 characters.
# Apply the limits before SQLAlchemy builds a batch INSERT so a single record
# can never roll the whole ingestion transaction back with StringDataRightTruncation.
_MAX_SOURCE_LENGTH = 255
_MAX_COMPANY_LENGTH = 255
_MAX_TITLE_LENGTH = 500
_MAX_URL_LENGTH = 1000
_MAX_LOCATION_LENGTH = 1000


def _cap_storage_value(value: str, limit: int) -> str:
    return value[:limit]


def _normalize_component(value: str | None) -> str:
    if not value:
        return ""
    stripped = _SUFFIX_PATTERN.sub("", value)
    collapsed = _WHITESPACE_PATTERN.sub(" ", stripped).strip().lower()
    return collapsed[:_MAX_COMPONENT_LENGTH]


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


_HTML_TAG_PATTERN = re.compile(r"<[^>]+>")
# Checked in order — whichever a given provider actually populates.
# Greenhouse's job-detail payload (fetched with ?content=true) uses
# "content"; Lever posting objects expose both a plain and an HTML
# description; Ashby likewise; Workday's *list* endpoint (the only one this
# codebase calls — see sources/ats/workday.py) doesn't include a
# description at all, so Workday postings simply have none, same as GitHub
# lists/RSS/pagesxyz.
_DESCRIPTION_KEYS = ("content", "descriptionPlain", "description", "descriptionHtml")
_MAX_DESCRIPTION_CHARS = 1200


def extract_description(raw: dict) -> str | None:
    """Best-effort plain-text excerpt of a posting's own description, pulled
    from whichever key the source's raw payload happens to use. Feeds the
    ranking prompt (see matching/rank.py) so the blurb it writes can cite
    specific skills/requirements the posting actually asks for, not just
    title/location. Returns None when the source doesn't carry one — that's
    common and expected, not an error."""
    if not isinstance(raw, dict):
        return None
    for key in _DESCRIPTION_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            text = html.unescape(_HTML_TAG_PATTERN.sub(" ", value))
            text = _WHITESPACE_PATTERN.sub(" ", text).strip()
            if text:
                return text[:_MAX_DESCRIPTION_CHARS]
    return None


def normalize(raw: RawPosting) -> Posting:
    return Posting(
        posting_key=build_posting_key(raw.company, raw.title, raw.location),
        source=_cap_storage_value(raw.source, _MAX_SOURCE_LENGTH),
        company=_cap_storage_value(raw.company, _MAX_COMPANY_LENGTH),
        title=_cap_storage_value(raw.title, _MAX_TITLE_LENGTH),
        url=_cap_storage_value(raw.url, _MAX_URL_LENGTH),
        location=(
            _cap_storage_value(raw.location, _MAX_LOCATION_LENGTH)
            if raw.location is not None
            else None
        ),
        role_type=raw.role_type,
        posted_at=raw.posted_at,
        raw=raw.raw,
        description=extract_description(raw.raw),
    )
