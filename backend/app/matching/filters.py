from __future__ import annotations

import re

from app.domain.models import Criteria, Posting


_US_COUNTRY_ALIASES = {
    "america",
    "u s",
    "u s a",
    "united states",
    "united states of america",
    "us",
    "usa",
}
# Common feed shorthand that omits both country and state. Keep this list
# deliberately narrow: the live new-grad source uses NYC/SF heavily, whereas
# guessing every bare city worldwide would recreate the false positives the
# location filter is meant to prevent.
_US_CITY_ALIASES = {"nyc", "sf"}
_US_STATE_NAME_TO_CODE = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "district of columbia": "DC",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
    # US territories that appear in nationwide job searches.
    "american samoa": "AS",
    "guam": "GU",
    "northern mariana islands": "MP",
    "puerto rico": "PR",
    "us virgin islands": "VI",
}
_US_STATE_CODES = frozenset(_US_STATE_NAME_TO_CODE.values())
_US_STATE_CODE_PATTERN = re.compile(
    r"(?<![A-Za-z])(" + "|".join(sorted(_US_STATE_CODES)) + r")(?![A-Za-z])"
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalized_location(value: str) -> str:
    return _NON_ALNUM.sub(" ", value.lower()).strip()


def _contains_phrase(haystack: str, phrase: str) -> bool:
    return f" {phrase} " in f" {haystack} "


def _state_codes_in_location(raw_location: str, normalized_location: str) -> set[str]:
    codes = set(_US_STATE_CODE_PATTERN.findall(raw_location))
    codes.update(
        code
        for state_name, code in _US_STATE_NAME_TO_CODE.items()
        if _contains_phrase(normalized_location, state_name)
    )
    return codes


def _preferred_state_code(raw_preference: str, normalized_preference: str) -> str | None:
    named = _US_STATE_NAME_TO_CODE.get(normalized_preference)
    if named is not None:
        return named
    candidate = raw_preference.strip().upper().replace(".", "")
    return candidate if candidate in _US_STATE_CODES else None


def _looks_like_us_location(raw_location: str, normalized_location: str) -> bool:
    if any(
        _contains_phrase(normalized_location, alias) for alias in _US_COUNTRY_ALIASES
    ):
        return True
    if _state_codes_in_location(raw_location, normalized_location):
        return True
    if any(
        _contains_phrase(normalized_location, alias) for alias in _US_CITY_ALIASES
    ):
        return True
    # A bare "Remote" carries no conflicting country and is useful to a
    # United-States seeker. Qualified foreign remote locations (for example,
    # "Remote in Canada") do not pass this branch.
    return normalized_location in {"remote", "fully remote", "remote only", "virtual"}


def location_matches(posting_location: str | None, preference: str) -> bool:
    """Match free-form city/state/country preferences against source text.

    The onboarding UI explicitly accepts countries and full state names, but
    most feeds emit city plus state abbreviation (``Seattle, WA``). A literal
    substring comparison therefore made ``United States`` reject nearly the
    entire US inventory before ranking. Preserve substring matching for free-
    form cities while adding country and state aliases for the structured
    forms users naturally enter.
    """
    if not posting_location or not preference.strip():
        return False

    normalized_posting = _normalized_location(posting_location)
    normalized_preference = _normalized_location(preference)
    if not normalized_preference:
        return False
    if _contains_phrase(normalized_posting, normalized_preference):
        return True

    preferred_state = _preferred_state_code(preference, normalized_preference)
    if preferred_state is not None:
        return preferred_state in _state_codes_in_location(
            posting_location, normalized_posting
        )

    if normalized_preference in _US_COUNTRY_ALIASES:
        return _looks_like_us_location(posting_location, normalized_posting)

    return False


def criteria_mismatch_reason(posting: Posting, criteria: Criteria) -> str | None:
    """Return the first deterministic rejection reason, or ``None`` on fit.

    Exposing the reason keeps filtering behavior observable without another
    model call; match_new_postings logs these counts as a per-user funnel.
    """
    if criteria.role_types and posting.role_type not in criteria.role_types:
        return "role_type"

    if criteria.sponsorship_required is not None:
        posting_sponsorship = posting.raw.get("sponsorship_available")
        if (
            posting_sponsorship is not None
            and bool(posting_sponsorship) != criteria.sponsorship_required
        ):
            return "sponsorship"

    if (
        criteria.min_date
        and posting.posted_at
        and posting.posted_at < criteria.min_date
    ):
        return "min_date"

    if criteria.locations and not any(
        location_matches(posting.location, preference)
        for preference in criteria.locations
    ):
        return "location"

    if criteria.keywords:
        title_company = f"{posting.title} {posting.company}".lower()
        description = (posting.description or "").lower()
        haystack = f"{title_company} {description}"
        if description and not any(
            keyword.lower() in haystack for keyword in criteria.keywords
        ):
            return "keyword"

    return None


def matches_criteria(posting: Posting, criteria: Criteria) -> bool:
    """Deterministic rule filters: role type, keywords, location, sponsorship,
    and minimum posted date. LLM ranking (score/blurb) runs only on postings
    that survive this — see spec: Matching."""
    return criteria_mismatch_reason(posting, criteria) is None


def filter_postings(postings: list[Posting], criteria: Criteria) -> list[Posting]:
    return [p for p in postings if matches_criteria(p, criteria)]
