"""Tests for src/parser.py: HTML extraction with fallbacks and HN header parsing."""

from src.parser import parse_fake_jobs, parse_hn_item, parse_hn_items, split_hn_header

SELECTORS = {
    "card": ["div.card-content"],
    "title": ["h2.title"],
    "company": ["h3.company"],
    "location": ["p.location"],
    "posted": ["time"],
    "link": ["footer a.card-footer-item"],
}
BASE_URL = "https://example.org/jobs/"

SAMPLE_HTML = """
<div class="card-content">
  <h2 class="title is-5">  Senior   Python Developer </h2>
  <h3 class="subtitle is-6 company">Payne, Roberts and Davis</h3>
  <p class="location">
      Stewartbury, AA
  </p>
  <p><time datetime="2021-04-08">2021-04-08</time></p>
  <footer class="card-footer">
    <a href="https://www.realpython.com" class="card-footer-item">Learn</a>
    <a href="/jobs/senior-python-developer-0.html" class="card-footer-item">Apply</a>
  </footer>
</div>
<div class="card-content">
  <h2 class="title is-5">Data Analyst</h2>
  <h3 class="subtitle is-6 company">Acme Corp</h3>
</div>
<div class="card-content">
  <h3 class="subtitle is-6 company">Card without a title</h3>
</div>
"""


def test_parse_fake_jobs_extracts_and_cleans_fields():
    records = parse_fake_jobs(SAMPLE_HTML, SELECTORS, BASE_URL)
    first = records[0]
    assert first["source"] == "fake_jobs"
    assert first["title"] == "Senior Python Developer"          # whitespace collapsed
    assert first["company"] == "Payne, Roberts and Davis"
    assert first["location"] == "Stewartbury, AA"
    assert first["posted"] == "2021-04-08"                        # datetime attribute preferred
    assert first["url"] == "https://example.org/jobs/senior-python-developer-0.html"  # last link, absolute


def test_parse_fake_jobs_missing_fields_become_none_and_untitled_cards_are_skipped():
    records = parse_fake_jobs(SAMPLE_HTML, SELECTORS, BASE_URL)
    assert len(records) == 2                                      # third card has no title
    second = records[1]
    assert second["title"] == "Data Analyst"
    assert second["location"] is None and second["posted"] is None and second["url"] is None


def test_parse_fake_jobs_uses_fallback_selectors():
    selectors = dict(SELECTORS, card=["article.nope", "div.card-content"], title=["h1.nope", "h2.title"])
    assert len(parse_fake_jobs(SAMPLE_HTML, selectors, BASE_URL)) == 2


def test_parse_fake_jobs_without_cards_returns_empty_list():
    assert parse_fake_jobs("<html><body><p>nothing here</p></body></html>", SELECTORS, BASE_URL) == []


def test_split_hn_header_detects_company_title_location():
    fields = split_hn_header("Acme Labs | Senior Backend Engineer | Remote (EU) | $140k-$170k | Full-time")
    assert fields == {"company": "Acme Labs", "title": "Senior Backend Engineer", "location": "Remote (EU)"}


def test_split_hn_header_rejects_non_job_lines():
    assert split_hn_header("Thanks for sharing, is this still open?") is None
    assert split_hn_header("x" * 400 + " | y") is None


def test_parse_hn_item_builds_record_from_comment_html():
    item = {
        "objectID": "123",
        "created_at": "2026-10-01T10:00:00.000Z",
        "comment_text": "Acme Labs | ML Engineer | Berlin | REMOTE | &#x24;120k-&#x24;150k<p>We use Python &amp; PyTorch."
                        "<p>Apply: <a href=\"https://acme.example\">acme.example</a>",
    }
    record = parse_hn_item(item)
    assert record["company"] == "Acme Labs" and record["title"] == "ML Engineer"
    assert record["url"] == "https://news.ycombinator.com/item?id=123"
    assert "Python & PyTorch" in record["description"]
    assert record["posted"] == "2026-10-01T10:00:00.000Z"


def test_parse_hn_items_skips_replies_and_empty_comments():
    items = [
        {"objectID": "1", "comment_text": "Great, thanks!"},
        {"objectID": "2", "comment_text": None},
        {"objectID": "3", "comment_text": "Foo Inc | Developer | Remote"},
    ]
    records = parse_hn_items(items)
    assert [r["company"] for r in records] == ["Foo Inc"]
