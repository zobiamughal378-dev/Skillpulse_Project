"""SkillPulse entry point: runs one complete pipeline cycle.

    python main.py            # scrape every active source once
    python main.py --demo     # fill the database with synthetic demo data
    python main.py --sources fake_jobs --no-alerts
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from loguru import logger

from src.cleaner import JobRecord, clean_records, validate_records
from src.config_loader import AppConfig, load_config
from src.demo_data import generate_demo_batch
from src.fetcher import Fetcher, iter_hn_items, iter_html_pages
from src.nlp_extractor import NLPExtractor
from src.notifier import Notifier
from src.parser import parse_fake_jobs, parse_hn_items
from src.storage import Storage
from src.utils import resolve_path, setup_logging, utc_now


@dataclass
class RunStats:
    """Counters describing one pipeline run."""

    run_id: int = 0
    fetched: int = 0
    unique: int = 0
    valid: int = 0
    invalid: int = 0
    new: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0


# ---------------------------------------------------------------------------
# Source collectors: one small function per source (add yours here)
# ---------------------------------------------------------------------------
def collect_fake_jobs(config: AppConfig, fetcher: Fetcher) -> list[dict[str, Any]]:
    """Scrape the fake-jobs practice site (HTML)."""
    cfg = config.sources.fake_jobs
    records: list[dict[str, Any]] = []
    pages = iter_html_pages(fetcher, cfg["base_url"], cfg.get("page_url_template"), int(cfg.get("max_pages", 1)))
    for html in pages:
        records.extend(parse_fake_jobs(html, cfg["selectors"], cfg["base_url"]))
    return records


def collect_hn_hiring(config: AppConfig, fetcher: Fetcher) -> list[dict[str, Any]]:
    """Read the latest Hacker News 'Who is hiring?' thread through the public Algolia API."""
    items = list(iter_hn_items(fetcher, config.sources.hn_hiring))
    return parse_hn_items(items, config.nlp.max_description_chars)


COLLECTORS: dict[str, Callable[[AppConfig, Fetcher], list[dict[str, Any]]]] = {
    "fake_jobs": collect_fake_jobs,
    "hn_hiring": collect_hn_hiring,
}


def collect_all(config: AppConfig, fetcher: Fetcher, stats: RunStats) -> list[dict[str, Any]]:
    """Run every active source; a failing source is logged and skipped, not fatal."""
    raw: list[dict[str, Any]] = []
    for name in config.sources.active:
        collector = COLLECTORS.get(name)
        if collector is None:
            message = f"{name}: unknown source"
            logger.error(message)
            stats.errors.append(message)
            continue
        try:
            logger.info("Collecting from source '{}'", name)
            raw.extend(collector(config, fetcher))
        except Exception as exc:  # noqa: BLE001 - isolate source failures
            logger.exception("Source '{}' failed", name)
            stats.errors.append(f"{name}: {exc}")
    return raw


def build_extractor(config: AppConfig) -> NLPExtractor:
    """Create the NLP extractor from configuration."""
    return NLPExtractor(
        taxonomy=config.skills,
        use_spacy=config.nlp.use_spacy,
        spacy_model=config.nlp.spacy_model,
        fuzzy_enabled=config.nlp.fuzzy_enabled,
        fuzzy_threshold=config.nlp.fuzzy_threshold,
    )


def process_raw(
    raw: list[dict[str, Any]],
    extractor: NLPExtractor,
    scraped_at: Optional[datetime] = None,
) -> tuple[list[JobRecord], int, int]:
    """Clean -> NLP-enrich -> validate. Returns (valid_records, invalid_count, unique_count)."""
    cleaned = clean_records(raw, scraped_at)
    enriched = extractor.enrich_many(cleaned)
    valid, invalid = validate_records(enriched)
    return valid, invalid, len(cleaned)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------
def run_pipeline(
    config: Optional[AppConfig] = None,
    sources: Optional[list[str]] = None,
    send_alerts: bool = True,
) -> RunStats:
    """Run one full cycle: fetch -> parse -> clean -> NLP -> validate -> store -> alert."""
    config = config or load_config()
    if sources:
        config.sources.active = sources
    started = utc_now()
    stats = RunStats()

    storage = Storage(resolve_path(config.paths.csv), resolve_path(config.paths.database))
    stats.run_id = storage.start_run(started)
    logger.info("=== Run #{} started ===", stats.run_id)

    try:
        fetcher = Fetcher(config.http)
        raw = collect_all(config, fetcher, stats)
        stats.fetched = len(raw)

        valid, stats.invalid, stats.unique = process_raw(raw, build_extractor(config), started)
        stats.valid = len(valid)

        new_records = storage.save_records(valid)
        stats.new = len(new_records)
        storage.snapshot(stats.run_id)

        if send_alerts and new_records:
            Notifier(config.alerts).notify(new_records)
        status = "success" if not stats.errors else ("partial" if raw else "failed")
    except Exception as exc:  # noqa: BLE001 - record the crash in the run log, then re-raise
        logger.exception("Pipeline crashed")
        stats.errors.append(f"pipeline: {exc}")
        storage.finish_run(stats.run_id, "failed", stats.fetched, stats.new, stats.invalid, stats.errors)
        raise

    stats.duration_seconds = (utc_now() - started).total_seconds()
    storage.finish_run(stats.run_id, status, stats.fetched, stats.new, stats.invalid, stats.errors)
    logger.info(
        "=== Run #{} {}: {} fetched, {} unique, {} new, {} invalid, {:.1f}s ===",
        stats.run_id, status, stats.fetched, stats.unique, stats.new, stats.invalid, stats.duration_seconds,
    )
    return stats


def run_demo(config: Optional[AppConfig] = None, weeks: int = 8, per_week: int = 55, seed: int = 7) -> RunStats:
    """Fill the database with synthetic weekly snapshots so the dashboard works offline."""
    config = config or load_config()
    storage = Storage(resolve_path(config.paths.csv), resolve_path(config.paths.database))
    extractor = build_extractor(config)
    totals = RunStats()
    now = utc_now().replace(microsecond=0)

    for week in range(weeks):
        scraped_at = now - timedelta(days=7 * (weeks - 1 - week))
        run_id = storage.start_run(scraped_at)
        raw = generate_demo_batch(week, weeks, per_week, scraped_at, seed)
        valid, invalid, unique = process_raw(raw, extractor, scraped_at)
        new = storage.save_records(valid)
        storage.snapshot(run_id, scraped_at.date())
        storage.finish_run(run_id, "success", len(raw), len(new), invalid, [], scraped_at + timedelta(seconds=42))
        totals.fetched += len(raw)
        totals.valid += len(valid)
        totals.invalid += invalid
        totals.new += len(new)
        totals.unique += unique
        totals.run_id = run_id
    logger.info("Demo data ready: {} jobs across {} weekly snapshots", totals.new, weeks)
    return totals


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Parse command-line options."""
    parser = argparse.ArgumentParser(description="SkillPulse - automated job market pipeline")
    parser.add_argument("--config", help="path to a config.yaml (default: config/config.yaml)")
    parser.add_argument("--demo", action="store_true", help="generate synthetic demo data instead of scraping")
    parser.add_argument("--sources", nargs="+", help="override the active sources, e.g. --sources fake_jobs")
    parser.add_argument("--no-alerts", action="store_true", help="do not send email / Telegram alerts")
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point; returns a process exit code."""
    args = parse_args(argv)
    config = load_config(args.config)
    setup_logging(config.paths.log_file, config.logging.level)
    try:
        if args.demo:
            run_demo(config)
            return 0
        stats = run_pipeline(config, sources=args.sources, send_alerts=not args.no_alerts)
    except Exception:  # noqa: BLE001 - already logged with a traceback
        return 1
    return 0 if stats.fetched or not stats.errors else 2


if __name__ == "__main__":
    sys.exit(main())
