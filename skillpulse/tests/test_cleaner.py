"""Tests for src/cleaner.py: normalisation, de-duplication and schema validation."""

from datetime import date, datetime, timezone

from src.cleaner import (
    clean_record, clean_records, compute_job_id, normalize_location, normalize_text,
    parse_date, validate_records,
)

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def test_normalize_text_removes_whitespace_artifacts():
    assert normalize_text("  Senior\t\n Dev\u00a0eloper \u200b ") == "Senior Dev eloper"
    assert normalize_text("   \n\t ") is None
    assert normalize_text(None) is None
    assert normalize_text("ｆｕｌｌｗｉｄｔｈ") == "fullwidth"        # NFKC normalisation


def test_normalize_location_and_parse_date_standardise_formats():
    assert normalize_location("  new york ,NY. ") == "New York, NY"
    assert normalize_location("REMOTE (EU)") == "Remote (EU)"
    assert normalize_location("Berlin, Germany") == "Berlin, Germany"
    assert parse_date("2021-04-08") == date(2021, 4, 8)
    assert parse_date("2026-10-01T14:03:21.000Z") == date(2026, 10, 1)
    assert parse_date("April 8, 2021") == date(2021, 4, 8)
    assert parse_date("not a date") is None


def test_clean_records_drops_untitled_and_deduplicates_by_hash():
    raw = [
        {"source": "s", "title": " Python  Dev ", "company": "Acme", "location": "berlin", "url": "https://a.example/1"},
        {"source": "s", "title": "Python Dev", "company": "ACME ", "location": "Berlin", "url": "https://a.example/1"},
        {"source": "s", "title": "   ", "company": "Nobody"},
        {"source": "s", "title": "Data Analyst", "company": "Acme"},
    ]
    cleaned = clean_records(raw, NOW)
    assert [r["title"] for r in cleaned] == ["Python Dev", "Data Analyst"]
    assert all(len(r["job_id"]) == 16 for r in cleaned)


def test_job_id_is_stable_and_case_insensitive():
    a = compute_job_id("s", "Python Dev", "Acme", "Berlin", None)
    assert a == compute_job_id("s", "python dev", " ACME", "berlin", None)
    assert a != compute_job_id("other", "Python Dev", "Acme", "Berlin", None)


def test_clean_record_rejects_invalid_urls():
    record = clean_record({"source": "s", "title": "Dev", "url": "javascript:alert(1)"}, NOW)
    assert record["url"] is None


def test_validate_records_counts_invalid_entries():
    good = clean_record({"source": "s", "title": "Dev", "company": "Acme"}, NOW)
    good.update(skills=["Python"], salary_min=100000.0, salary_max=120000.0, remote=True)
    bad = dict(good, title="")           # empty title must fail validation
    valid, invalid = validate_records([good, bad])
    assert invalid == 1 and len(valid) == 1
    assert valid[0].skills == ["Python"] and valid[0].remote is True
    assert valid[0].scraped_at == NOW
