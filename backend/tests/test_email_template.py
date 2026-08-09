from datetime import UTC, datetime

from app.db.models import Match, Posting
from app.notify.email_template import render_digest_email


def _match(**overrides) -> Match:
    defaults = dict(
        id=1, user_id=1, posting_id=1, score=0.8, blurb="Great fit for your backend skills.",
        priority="normal", lane="slow", match_reason="new_posting", notified_channels=[],
        matched_target_field=None, created_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Match(**defaults)


def _posting(**overrides) -> Posting:
    defaults = dict(
        id=1, posting_key="k1", source="test", company="Acme", title="New Grad SWE",
        url="https://example.com/job/1", location="Remote", status="open",
        first_seen_at=datetime.now(UTC), last_seen_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return Posting(**defaults)


def test_render_digest_email_empty_items_has_friendly_no_matches_message():
    text, html = render_digest_email("Alex Morgan", [], "https://app.example.com/?view=matches")

    assert "No new job matches today" in text
    assert "No new job matches today" in html
    assert "https://app.example.com/?view=matches" in text
    assert "https://app.example.com/?view=matches" in html


def test_render_digest_email_greets_by_first_name():
    text, html = render_digest_email("Alex Morgan", [], "https://app.example.com")

    assert "Hi Alex," in text
    assert "Hi Alex," in html
    assert "Morgan" not in text


def test_render_digest_email_includes_company_title_and_apply_link():
    items = [(_match(), _posting())]

    text, html = render_digest_email("Alex", items, "https://app.example.com")

    assert "Acme" in text and "New Grad SWE" in text
    assert "https://example.com/job/1" in text
    assert "Acme" in html and "New Grad SWE" in html
    assert "https://example.com/job/1" in html
    assert "View &amp; apply" in html


def test_render_digest_email_shows_high_fit_pill_only_for_high_priority():
    high = [(_match(priority="high"), _posting())]
    normal = [(_match(priority="normal"), _posting())]

    _, high_html = render_digest_email("Alex", high, "https://app.example.com")
    _, normal_html = render_digest_email("Alex", normal, "https://app.example.com")

    assert "High fit" in high_html
    assert "High fit" not in normal_html


def test_render_digest_email_shows_target_field_pill_when_present():
    items = [(_match(matched_target_field="consulting"), _posting())]

    _, html = render_digest_email("Alex", items, "https://app.example.com")

    assert "Consulting" in html


def test_render_digest_email_links_to_matches_url():
    text, html = render_digest_email("Alex", [(_match(), _posting())], "https://app.example.com/?view=matches")

    assert "https://app.example.com/?view=matches" in text
    assert "https://app.example.com/?view=matches" in html
    assert "View all your matches" in text
    assert "View all your matches" in html


def test_render_digest_email_escapes_html_from_external_job_data():
    """Company/title/blurb come from scraped external sources — must never
    be interpreted as HTML in the rendered email."""
    items = [
        (
            _match(blurb="<script>alert(1)</script>"),
            _posting(company="Acme <b>Corp</b>", title='Engineer "SWE" & More'),
        )
    ]

    _, html = render_digest_email("Alex", items, "https://app.example.com")

    assert "<script>" not in html
    assert "<b>Corp</b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;Corp&lt;/b&gt;" in html
