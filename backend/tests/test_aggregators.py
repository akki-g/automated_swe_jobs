from app.sources.aggregators import RssFeedSource

SAMPLE_RSS = """<?xml version="1.0"?>
<rss><channel>
  <item>
    <title>Acme - New Grad Software Engineer</title>
    <link>https://example.com/jobs/1</link>
    <pubDate>Mon, 01 Jun 2026 12:00:00 GMT</pubDate>
  </item>
  <item>
    <title>Software Engineering Intern</title>
    <link>https://example.com/jobs/2</link>
  </item>
  <item>
    <title>Missing link item</title>
  </item>
</channel></rss>
"""


def test_parse_extracts_items_with_title_and_link():
    source = RssFeedSource("test_feed", "https://example.com/feed.xml")
    postings = source._parse(SAMPLE_RSS)

    assert len(postings) == 2
    assert postings[0].company == "Acme"
    assert postings[0].title == "New Grad Software Engineer"
    assert postings[0].posted_at is not None


def test_parse_falls_back_to_feed_name_when_no_company_in_title():
    source = RssFeedSource("test_feed", "https://example.com/feed.xml")
    postings = source._parse(SAMPLE_RSS)

    assert postings[1].company == "test_feed"
    assert postings[1].title == "Software Engineering Intern"


def test_parse_skips_items_missing_link():
    source = RssFeedSource("test_feed", "https://example.com/feed.xml")
    postings = source._parse(SAMPLE_RSS)

    urls = {p.url for p in postings}
    assert "https://example.com/jobs/2" in urls
    assert len(postings) == 2
