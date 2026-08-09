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


def test_render_digest_email_both_empty_has_friendly_no_matches_message():
    text, html = render_digest_email("Alex Morgan", [], [], "https://app.example.com/?view=matches")

    assert "No new job matches today" in text
    assert "No new job matches today" in html
    assert "https://app.example.com/?view=matches" in text
    assert "https://app.example.com/?view=matches" in html


def test_render_digest_email_greets_by_first_name():
    text, html = render_digest_email("Alex Morgan", [], [], "https://app.example.com")

    assert "Hi Alex," in text
    assert "Hi Alex," in html
    assert "Morgan" not in text


def test_render_digest_email_includes_company_title_and_apply_link():
    just_dropped = [(_match(), _posting())]

    text, html = render_digest_email("Alex", just_dropped, [], "https://app.example.com")

    assert "Acme" in text and "New Grad SWE" in text
    assert "https://example.com/job/1" in text
    assert "Acme" in html and "New Grad SWE" in html
    assert "https://example.com/job/1" in html
    assert "View &amp; apply" in html


def test_render_digest_email_shows_high_fit_pill_only_for_high_priority():
    high = [(_match(priority="high"), _posting())]
    normal = [(_match(priority="normal"), _posting())]

    _, high_html = render_digest_email("Alex", high, [], "https://app.example.com")
    _, normal_html = render_digest_email("Alex", normal, [], "https://app.example.com")

    assert "High fit" in high_html
    assert "High fit" not in normal_html


def test_render_digest_email_shows_target_field_pill_when_present():
    just_dropped = [(_match(matched_target_field="consulting"), _posting())]

    _, html = render_digest_email("Alex", just_dropped, [], "https://app.example.com")

    assert "Consulting" in html


def test_render_digest_email_links_to_matches_url():
    text, html = render_digest_email(
        "Alex", [(_match(), _posting())], [], "https://app.example.com/?view=matches"
    )

    assert "https://app.example.com/?view=matches" in text
    assert "https://app.example.com/?view=matches" in html
    assert "View all your matches" in text
    assert "View all your matches" in html


def test_render_digest_email_escapes_html_from_external_job_data():
    """Company/title/blurb come from scraped external sources — must never
    be interpreted as HTML in the rendered email."""
    just_dropped = [
        (
            _match(blurb="<script>alert(1)</script>"),
            _posting(company="Acme <b>Corp</b>", title='Engineer "SWE" & More'),
        )
    ]

    _, html = render_digest_email("Alex", just_dropped, [], "https://app.example.com")

    assert "<script>" not in html
    assert "<b>Corp</b>" not in html
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;Corp&lt;/b&gt;" in html


def test_render_digest_email_puts_each_section_under_its_own_heading():
    just_dropped = [(_match(id=1), _posting(posting_key="k1", company="NewCo", title="Fresh Role"))]
    for_you = [(_match(id=2), _posting(posting_key="k2", company="OldCo", title="Reminder Role"))]

    text, html = render_digest_email("Alex", just_dropped, for_you, "https://app.example.com")

    for content in (text, html):
        just_dropped_pos = content.index("NewCo")
        heading_pos = content.index("Just Dropped" if content is html else "Just dropped")
        for_you_heading_pos = content.index("For You" if content is html else "For you")
        old_co_pos = content.index("OldCo")
        assert heading_pos < just_dropped_pos < for_you_heading_pos < old_co_pos


def test_render_digest_email_omits_empty_section_heading():
    just_dropped = [(_match(), _posting())]

    _, html = render_digest_email("Alex", just_dropped, [], "https://app.example.com")

    assert "For You" not in html


def test_render_digest_email_omits_just_dropped_heading_when_only_for_you():
    for_you = [(_match(), _posting())]

    _, html = render_digest_email("Alex", [], for_you, "https://app.example.com")

    assert "Just Dropped" not in html
    assert "For You" in html


def test_render_digest_email_total_count_sums_both_sections():
    just_dropped = [(_match(id=1), _posting(posting_key="k1"))]
    for_you = [(_match(id=2), _posting(posting_key="k2")), (_match(id=3), _posting(posting_key="k3"))]

    _, html = render_digest_email("Alex", just_dropped, for_you, "https://app.example.com")

    assert "3 matches picked for you today" in html


def test_render_digest_email_uses_responsive_two_column_grid():
    """The grid must degrade to one column on narrow viewports — see
    _RESPONSIVE_STYLE — rather than staying two columns and forcing a phone
    reader to deal with cramped, unreadable half-width cards."""
    just_dropped = [(_match(), _posting())]

    _, html = render_digest_email("Alex", just_dropped, [], "https://app.example.com")

    assert "job-cell" in html
    assert "@media" in html
    assert "display: block !important" in html
