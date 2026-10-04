"""APScheduler wrapper that runs the pipeline on a fixed interval."""

from datetime import datetime
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger
from loguru import logger

from src.config_loader import AppConfig, load_config
from src.utils import setup_logging


def run_job(config: AppConfig) -> None:
    """Run one pipeline cycle; never let an exception kill the scheduler."""
    from main import run_pipeline  # imported lazily to avoid a circular import

    logger.info("Scheduled run starting")
    try:
        stats = run_pipeline(config)
        logger.info(
            "Scheduled run finished: {} fetched, {} new, {} invalid, {} error(s)",
            stats.fetched, stats.new, stats.invalid, len(stats.errors),
        )
    except Exception:  # noqa: BLE001 - keep the scheduler alive
        logger.exception("Scheduled run crashed")


def start_scheduler(config: Optional[AppConfig] = None) -> None:
    """Block forever, running the pipeline every ``schedule.interval_hours`` hours."""
    config = config or load_config()
    setup_logging(config.paths.log_file, config.logging.level)

    scheduler = BlockingScheduler(timezone=config.schedule.timezone)
    scheduler.add_job(
        run_job,
        trigger=IntervalTrigger(hours=config.schedule.interval_hours),
        args=[config],
        id="skillpulse_pipeline",
        max_instances=1,      # never overlap two runs
        coalesce=True,        # merge missed runs into one
        replace_existing=True,
        next_run_time=datetime.now() if config.schedule.run_on_start else None,
    )
    logger.info("Scheduler started: every {} hour(s). Press Ctrl+C to stop.", config.schedule.interval_hours)
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")


if __name__ == "__main__":
    start_scheduler()
