from __future__ import annotations

from app.domain.models import Criteria, Posting


def matches_criteria(posting: Posting, criteria: Criteria) -> bool:
    """Deterministic rule filters: role type, keywords, location, sponsorship,
    and minimum posted date. LLM ranking (score/blurb) runs only on postings
    that survive this — see spec: Matching."""
    if criteria.role_types and posting.role_type not in criteria.role_types:
        return False

    if criteria.sponsorship_required is not None:
        posting_sponsorship = posting.raw.get("sponsorship_available")
        # Only exclude on an explicit, known mismatch — if the source didn't
        # report sponsorship info at all, we can't rule the posting out on
        # this basis, so it stays in and other filters decide.
        if (
            posting_sponsorship is not None
            and bool(posting_sponsorship) != criteria.sponsorship_required
        ):
            return False

    if (
        criteria.min_date
        and posting.posted_at
        and posting.posted_at < criteria.min_date
    ):
        return False

    if criteria.locations:
        posting_location = (posting.location or "").lower()
        if not any(loc.lower() in posting_location for loc in criteria.locations):
            return False

    if criteria.keywords:
        # Treat keywords as a hard filter only when the source supplies enough
        # job text to establish a real mismatch. Many list/Workday records
        # contain only title/company/location; rejecting "Software Engineer"
        # because its title omits "Python" made broad, relevant inventories
        # collapse to zero before the ranker ever saw them. With no detailed
        # text, fail open and let semantic ranking use the user's keywords.
        title_company = f"{posting.title} {posting.company}".lower()
        description = (posting.description or "").lower()
        haystack = f"{title_company} {description}"
        if description and not any(
            keyword.lower() in haystack for keyword in criteria.keywords
        ):
            return False

    return True


def filter_postings(postings: list[Posting], criteria: Criteria) -> list[Posting]:
    return [p for p in postings if matches_criteria(p, criteria)]
