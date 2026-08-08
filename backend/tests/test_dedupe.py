from app.domain.models import Posting
from app.ingest.dedupe import dedupe_postings, filter_new


def _posting(key: str) -> Posting:
    return Posting(
        posting_key=key,
        source="test",
        company="Acme",
        title="SWE",
        url=f"https://example.com/{key}",
        location=None,
        role_type=None,
        posted_at=None,
    )


def test_dedupe_postings_keeps_first_occurrence():
    a = _posting("k1")
    b = _posting("k1")
    c = _posting("k2")

    result = dedupe_postings([a, b, c])

    assert result == [a, c]


def test_dedupe_postings_empty_input():
    assert dedupe_postings([]) == []


def test_filter_new_excludes_existing_keys():
    a = _posting("k1")
    b = _posting("k2")

    result = filter_new([a, b], existing_keys={"k1"})

    assert result == [b]


def test_filter_new_all_new_when_no_existing_keys():
    a = _posting("k1")
    b = _posting("k2")

    result = filter_new([a, b], existing_keys=set())

    assert result == [a, b]
