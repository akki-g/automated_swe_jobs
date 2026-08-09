from datetime import UTC, datetime

from app.domain.models import RawPosting, RoleType
from app.ingest.normalize import build_posting_key, extract_description, normalize


def test_build_posting_key_normalizes_case_and_whitespace():
    key1 = build_posting_key("Acme  Inc.", "Software Engineer", "New York (Remote)")
    key2 = build_posting_key("acme", "software engineer", "new york")
    assert key1 == key2


def test_build_posting_key_strips_common_suffixes():
    key1 = build_posting_key("Widgets Corp", "SWE Intern", None)
    key2 = build_posting_key("Widgets", "SWE Intern", None)
    assert key1 == key2


def test_build_posting_key_differs_on_title():
    key1 = build_posting_key("Acme", "Backend Engineer", "Remote")
    key2 = build_posting_key("Acme", "Frontend Engineer", "Remote")
    assert key1 != key2


def test_build_posting_key_caps_each_component_length():
    """Regression for a real production incident: an oversized posting_key
    (e.g. a title or a long multi-location string) is a hard INSERT failure
    against postings.posting_key's VARCHAR column, and that failure took
    down the *entire* batch insert for the cycle, not just the offending
    row. Each component must be capped independent of the column width."""
    long_title = "Software Engineer " + "x" * 1000
    long_location = "; ".join(f"City {i}, ST" for i in range(50))

    key = build_posting_key("Acme", long_title, long_location)

    assert len(key) < 500  # comfortably under postings.posting_key's column width
    company_part, title_part, location_part = key.split("|")
    assert len(title_part) <= 150
    assert len(location_part) <= 150


def test_normalize_preserves_fields_and_computes_key():
    raw = RawPosting(
        source="github_new_grad",
        company="Acme Inc.",
        title="New Grad SWE",
        url="https://example.com/job/1",
        location="Remote",
        role_type=RoleType.NEW_GRAD,
        posted_at=datetime(2026, 8, 1, tzinfo=UTC),
        raw={"id": 1},
    )
    posting = normalize(raw)

    assert posting.company == raw.company
    assert posting.title == raw.title
    assert posting.url == raw.url
    assert posting.posting_key == build_posting_key(raw.company, raw.title, raw.location)


def test_extract_description_prefers_content_key():
    assert extract_description({"content": "<p>Build cool stuff</p>"}) == "Build cool stuff"


def test_extract_description_falls_back_through_known_keys_in_order():
    assert extract_description({"descriptionPlain": "Plain text wins"}) == "Plain text wins"
    assert extract_description({"description": "Generic description key"}) == "Generic description key"
    assert extract_description({"descriptionHtml": "<div>HTML fallback</div>"}) == "HTML fallback"


def test_extract_description_strips_html_and_collapses_whitespace():
    raw = {"content": "<p>Requires  <b>Python</b>\n\nand   SQL.</p>&amp; more"}
    assert extract_description(raw) == "Requires Python and SQL. & more"


def test_extract_description_returns_none_when_no_known_key_present():
    assert extract_description({"title": "SWE", "location": "Remote"}) is None
    assert extract_description({}) is None
    assert extract_description(None) is None  # type: ignore[arg-type]


def test_extract_description_truncates_very_long_text():
    raw = {"content": "x" * 5000}
    result = extract_description(raw)
    assert result is not None
    assert len(result) == 1200


def test_normalize_populates_description_from_raw():
    raw = RawPosting(
        source="greenhouse:acme",
        company="Acme",
        title="SWE",
        url="https://example.com/job/1",
        location="Remote",
        role_type=RoleType.NEW_GRAD,
        posted_at=None,
        raw={"content": "<p>Python, SQL, and distributed systems.</p>"},
    )
    posting = normalize(raw)

    assert posting.description == "Python, SQL, and distributed systems."


def test_normalize_description_is_none_when_source_has_none():
    raw = RawPosting(
        source="workday:acme:careers",
        company="Acme",
        title="SWE",
        url="https://example.com/job/1",
        location="Remote",
        role_type=RoleType.NEW_GRAD,
        posted_at=None,
        raw={"title": "SWE", "externalPath": "/job/1"},
    )
    posting = normalize(raw)

    assert posting.description is None
