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


def curate_two_section_digest(
    pending: list[tuple[Match, Posting]],
    already_sent: list[tuple[Match, Posting]],
    *,
    overall_cap: int,
    just_dropped_cap: int,
    per_company_cap: int,
) -> tuple[list[tuple[Match, Posting]], list[tuple[Match, Posting]]]:
    """Splits an email digest into ("Just Dropped", "For You") — see spec
    addendum: email digest sections.

    `pending` and `already_sent` are mutually exclusive by construction (the
    caller partitions on whether a match has ever been emailed before — see
    pipeline.gather_pending_digests / gather_previously_sent_email_matches),
    so nothing needs deduplication between the two returned lists: a match
    that appears in "Just Dropped" here can never also appear in "For You"
    in the same email, and once shown in "Just Dropped" it moves to the
    `already_sent` pool for every future email (the caller marks it
    delivered), so it can never appear in "Just Dropped" again either.

    just_dropped is drawn only from `pending`, capped at just_dropped_cap.
    for_you is drawn only from `already_sent`, filling whatever's left of
    overall_cap after just_dropped — so a quiet day with few brand-new
    matches still fills out the email with still-relevant older ones
    instead of running short.
    """
    just_dropped = curate_matches(pending, overall_cap=just_dropped_cap, per_company_cap=per_company_cap)
    remaining_cap = max(0, overall_cap - len(just_dropped))
    for_you = curate_matches(already_sent, overall_cap=remaining_cap, per_company_cap=per_company_cap)
    return just_dropped, for_you
