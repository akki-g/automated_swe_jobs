from __future__ import annotations

from app.db.models import Match, Posting


def curate_matches(
    items: list[tuple[Match, Posting]],
    *,
    overall_cap: int,
    per_company_cap: int,
) -> list[tuple[Match, Posting]]:
    """Pick the digest-worthy subset of a user's pending matches: highest
    score first, but capped per company so one prolific poster can't crowd
    out every other company in a single digest — the concrete complaint that
    motivated this (a real digest sent mostly one company's postings, with
    the rest barely relevant). If the diverse pass alone doesn't fill
    overall_cap (e.g. only two companies posted at all), backfill from the
    same score-ordered overflow rather than under-filling the digest.

    Pure — no I/O, no settings lookup; callers pass the caps they want
    (SMS/email use different overall_cap values, see notify/dispatch.py).
    """
    ranked = sorted(items, key=lambda pair: (pair[0].score, pair[0].created_at), reverse=True)

    selected: list[tuple[Match, Posting]] = []
    overflow: list[tuple[Match, Posting]] = []
    counts: dict[str, int] = {}

    for match, posting in ranked:
        count = counts.get(posting.company, 0)
        if count < per_company_cap and len(selected) < overall_cap:
            selected.append((match, posting))
            counts[posting.company] = count + 1
        else:
            overflow.append((match, posting))

    if len(selected) < overall_cap:
        selected.extend(overflow[: overall_cap - len(selected)])

    return selected
