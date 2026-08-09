from datetime import UTC, datetime, timedelta

from app.db.models import Match, Posting
from app.notify.curate import curate_matches, curate_two_section_digest


def _match(**overrides) -> Match:
    defaults = dict(
        id=1, user_id=1, posting_id=1, score=0.5, blurb="", priority="normal",
        lane="slow", match_reason="new_posting", notified_channels=[], created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Match(**defaults)


def _posting(**overrides) -> Posting:
    defaults = dict(
        id=1, posting_key="k1", source="test", company="Acme", title="SWE",
        url="https://example.com/1", status="open",
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Posting(**defaults)


def test_curate_matches_sorts_by_score_descending():
    items = [
        (_match(id=1, score=0.3), _posting(id=1, company="A")),
        (_match(id=2, score=0.9), _posting(id=2, company="B")),
        (_match(id=3, score=0.6), _posting(id=3, company="C")),
    ]

    curated = curate_matches(items, overall_cap=10, per_company_cap=10)

    assert [posting.company for _match, posting in curated] == ["B", "C", "A"]


def test_curate_matches_enforces_per_company_cap():
    """Regression for the real complaint: one prolific company must not
    crowd out every other company's matches in a single digest. Uses an
    overall_cap that the diverse pass alone can satisfy, so backfill (tested
    separately below) doesn't mask what this test is checking."""
    items = [(_match(id=i, score=0.9 - i * 0.01), _posting(id=i, company="Acme")) for i in range(10)]
    items.append((_match(id=100, score=0.5), _posting(id=100, company="Other Co")))

    curated = curate_matches(items, overall_cap=3, per_company_cap=2)

    companies = [posting.company for _match, posting in curated]
    assert companies.count("Acme") == 2
    assert "Other Co" in companies


def test_curate_matches_backfills_from_overflow_when_diversity_undershoots_cap():
    """Only two companies posted at all — the diverse pass alone (capped at
    2 each) would only fill 4 slots; the digest should still fill up to
    overall_cap from the overflow rather than send fewer than necessary."""
    items = [(_match(id=i, score=0.9 - i * 0.01), _posting(id=i, company="Acme")) for i in range(5)]
    items += [(_match(id=100 + i, score=0.5 - i * 0.01), _posting(id=100 + i, company="Beta")) for i in range(5)]

    curated = curate_matches(items, overall_cap=8, per_company_cap=2)

    assert len(curated) == 8


def test_curate_matches_respects_overall_cap_even_with_many_companies():
    items = [(_match(id=i, score=1.0 - i * 0.01), _posting(id=i, company=f"Co{i}")) for i in range(20)]

    curated = curate_matches(items, overall_cap=5, per_company_cap=2)

    assert len(curated) == 5
    # Highest-scoring 5 (one per distinct company here) should be chosen.
    assert [posting.company for _match, posting in curated] == ["Co0", "Co1", "Co2", "Co3", "Co4"]


def test_curate_matches_handles_empty_input():
    assert curate_matches([], overall_cap=10, per_company_cap=2) == []


def test_curate_matches_tiebreaks_by_created_at_when_scores_equal():
    older = datetime.now(UTC) - timedelta(hours=2)
    newer = datetime.now(UTC)
    items = [
        (_match(id=1, score=0.7, created_at=older), _posting(id=1, company="A")),
        (_match(id=2, score=0.7, created_at=newer), _posting(id=2, company="B")),
    ]

    curated = curate_matches(items, overall_cap=10, per_company_cap=10)

    assert [posting.company for _match, posting in curated] == ["B", "A"]


def test_curate_two_section_digest_caps_just_dropped_independent_of_overall():
    pending = [(_match(id=i, score=0.9 - i * 0.01), _posting(id=i, company=f"Co{i}")) for i in range(10)]
    already_sent = [(_match(id=100 + i, score=0.5), _posting(id=100 + i, company=f"Old{i}")) for i in range(10)]

    just_dropped, for_you = curate_two_section_digest(
        pending, already_sent, overall_cap=15, just_dropped_cap=5, per_company_cap=10
    )

    assert len(just_dropped) == 5
    assert len(for_you) == 10  # fills the rest of the 15 cap
    assert len(just_dropped) + len(for_you) == 15


def test_curate_two_section_digest_for_you_excludes_pending_by_construction():
    """for_you is drawn only from already_sent — a pending (never-emailed)
    match must never leak into for_you just because just_dropped didn't
    have room for it."""
    pending = [(_match(id=i, score=0.9), _posting(id=i, company=f"Co{i}")) for i in range(10)]

    just_dropped, for_you = curate_two_section_digest(
        pending, [], overall_cap=15, just_dropped_cap=5, per_company_cap=10
    )

    assert len(just_dropped) == 5
    assert for_you == []  # nothing in already_sent to draw from


def test_curate_two_section_digest_handles_empty_pools():
    just_dropped, for_you = curate_two_section_digest(
        [], [], overall_cap=15, just_dropped_cap=5, per_company_cap=2
    )
    assert just_dropped == []
    assert for_you == []


def test_curate_two_section_digest_for_you_respects_per_company_cap():
    """Isolated from backfill by matching overall_cap to per_company_cap
    exactly (see curate_matches's own backfill behavior/tests) — otherwise
    a single-company already_sent pool would legitimately backfill past
    the cap, same as curate_matches does when nothing else is available."""
    already_sent = [(_match(id=i, score=0.9 - i * 0.01), _posting(id=i, company="Acme")) for i in range(10)]

    _just_dropped, for_you = curate_two_section_digest(
        [], already_sent, overall_cap=2, just_dropped_cap=0, per_company_cap=2
    )

    assert len(for_you) == 2
