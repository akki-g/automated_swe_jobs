from datetime import UTC, datetime

from app.domain.models import RawPosting, RoleType
from app.ingest.normalize import build_posting_key, normalize


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
