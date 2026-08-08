from datetime import UTC, datetime

from app.domain.models import Criteria, Posting, RoleType
from app.matching.filters import matches_criteria


def _posting(**overrides) -> Posting:
    defaults = dict(
        posting_key="k",
        source="test",
        company="Acme",
        title="Software Engineer New Grad",
        url="https://example.com/1",
        location="New York, NY",
        role_type=RoleType.NEW_GRAD,
        posted_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    defaults.update(overrides)
    return Posting(**defaults)


def _criteria(**overrides) -> Criteria:
    defaults = dict(user_id=1)
    defaults.update(overrides)
    return Criteria(**defaults)


def test_empty_criteria_matches_everything():
    assert matches_criteria(_posting(), _criteria()) is True


def test_role_type_filter_excludes_mismatch():
    posting = _posting(role_type=RoleType.INTERN)
    criteria = _criteria(role_types=(RoleType.NEW_GRAD,))
    assert matches_criteria(posting, criteria) is False


def test_role_type_filter_includes_match():
    posting = _posting(role_type=RoleType.NEW_GRAD)
    criteria = _criteria(role_types=(RoleType.NEW_GRAD, RoleType.INTERN))
    assert matches_criteria(posting, criteria) is True


def test_location_filter_case_insensitive_substring():
    posting = _posting(location="New York, NY (Remote)")
    criteria = _criteria(locations=("new york",))
    assert matches_criteria(posting, criteria) is True


def test_location_filter_excludes_no_match():
    posting = _posting(location="Seattle, WA")
    criteria = _criteria(locations=("new york",))
    assert matches_criteria(posting, criteria) is False


def test_keyword_filter_matches_title_or_company():
    posting = _posting(title="Backend Software Engineer", company="Acme")
    criteria = _criteria(keywords=("backend",))
    assert matches_criteria(posting, criteria) is True


def test_keyword_filter_excludes_no_match():
    posting = _posting(title="Sales Associate")
    criteria = _criteria(keywords=("software", "engineer"))
    assert matches_criteria(posting, criteria) is False


def test_min_date_filter_excludes_older_postings():
    posting = _posting(posted_at=datetime(2026, 1, 1, tzinfo=UTC))
    criteria = _criteria(min_date=datetime(2026, 6, 1, tzinfo=UTC))
    assert matches_criteria(posting, criteria) is False


def test_min_date_filter_includes_newer_postings():
    posting = _posting(posted_at=datetime(2026, 7, 1, tzinfo=UTC))
    criteria = _criteria(min_date=datetime(2026, 6, 1, tzinfo=UTC))
    assert matches_criteria(posting, criteria) is True


def test_sponsorship_filter_excludes_explicit_mismatch():
    posting = _posting(raw={"sponsorship_available": False})
    criteria = _criteria(sponsorship_required=True)
    assert matches_criteria(posting, criteria) is False


def test_sponsorship_filter_includes_explicit_match():
    posting = _posting(raw={"sponsorship_available": True})
    criteria = _criteria(sponsorship_required=True)
    assert matches_criteria(posting, criteria) is True


def test_sponsorship_filter_includes_when_source_silent_on_sponsorship():
    posting = _posting(raw={})
    criteria = _criteria(sponsorship_required=True)
    assert matches_criteria(posting, criteria) is True


def test_combined_filters_all_must_pass():
    posting = _posting(
        role_type=RoleType.NEW_GRAD,
        location="Remote",
        title="Software Engineer",
        posted_at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    criteria = _criteria(
        role_types=(RoleType.NEW_GRAD,),
        locations=("remote",),
        keywords=("engineer",),
        min_date=datetime(2026, 6, 1, tzinfo=UTC),
    )
    assert matches_criteria(posting, criteria) is True
