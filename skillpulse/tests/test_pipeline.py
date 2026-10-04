"""End-to-end test of collect -> clean -> NLP -> validate -> store using an offline fake fetcher."""

import tempfile
from pathlib import Path

from main import RunStats, build_extractor, collect_all, process_raw
from src.config_loader import load_config
from src.storage import Storage
from tests.test_parser import SAMPLE_HTML


class FakeFetcher:
    """Serves canned responses instead of touching the network."""

    def get_text(self, url, params=None, check_robots=True):
        return SAMPLE_HTML

    def get_json(self, url, params=None, check_robots=False):
        tags = (params or {}).get("tags", "")
        if tags.startswith("story"):
            return {"hits": [{"objectID": "99", "title": "Ask HN: Who is hiring? (October 2026)"}]}
        return {
            "nbPages": 1,
            "hits": [
                {"objectID": "1", "parent_id": 99, "created_at": "2026-10-02T09:00:00Z",
                 "comment_text": "Acme Labs | Senior AI Engineer | Remote | $150k-$180k<p>Python, LangChain and AWS."},
                {"objectID": "2", "parent_id": 1, "comment_text": "Is this still open?"},
            ],
        }


def test_full_offline_pipeline_produces_clean_enriched_records():
    config = load_config()
    config.nlp.use_spacy = False
    stats = RunStats()
    raw = collect_all(config, FakeFetcher(), stats)
    assert stats.errors == []
    assert {r["source"] for r in raw} == {"fake_jobs", "hn_hiring"}

    valid, invalid, unique = process_raw(raw, build_extractor(config))
    assert invalid == 0 and unique == len(valid) == 3                    # 2 fake_jobs + 1 HN post
    hn = next(r for r in valid if r.source == "hn_hiring")
    assert hn.remote and hn.seniority == "Senior" and hn.salary_max == 180000.0
    assert {"Python", "LangChain", "AWS"} <= set(hn.skills)

    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(Path(tmp) / "o.csv", Path(tmp) / "d.db")
        assert len(storage.save_records(valid)) == 3
        assert len(storage.save_records(valid)) == 0                      # second run: all duplicates


def test_failing_source_is_isolated_not_fatal():
    class BrokenFetcher(FakeFetcher):
        def get_json(self, *args, **kwargs):
            raise RuntimeError("API down")

    config = load_config()
    stats = RunStats()
    raw = collect_all(config, BrokenFetcher(), stats)
    assert {r["source"] for r in raw} == {"fake_jobs"}
    assert len(stats.errors) == 1 and stats.errors[0].startswith("hn_hiring")
