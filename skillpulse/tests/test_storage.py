"""Tests for src/storage.py: SQLite upserts, CSV appends and snapshots."""

import csv
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from src.cleaner import JobRecord
from src.storage import CSV_COLUMNS, Storage

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=timezone.utc)


def make_record(job_id: str, skills=("Python", "SQL"), remote=True, salary=(100000.0, 140000.0)) -> JobRecord:
    return JobRecord(
        job_id=job_id, source="test", title=f"Engineer {job_id}", company="Acme", location="Berlin",
        remote=remote, skills=list(skills), salary_min=salary[0], salary_max=salary[1],
        salary_currency="USD", scraped_at=NOW,
    )


def test_save_records_inserts_new_and_skips_known_jobs():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(Path(tmp) / "out.csv", Path(tmp) / "db.sqlite")
        first = storage.save_records([make_record("a"), make_record("b")])
        again = storage.save_records([make_record("a"), make_record("c")])
        assert [r.job_id for r in first] == ["a", "b"]
        assert [r.job_id for r in again] == ["c"]
        assert storage.count_jobs() == 3


def test_csv_header_written_once_and_only_new_rows_appended():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(Path(tmp) / "out.csv", Path(tmp) / "db.sqlite")
        storage.save_records([make_record("a")])
        storage.save_records([make_record("a"), make_record("b")])
        with (Path(tmp) / "out.csv").open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert list(rows[0].keys()) == CSV_COLUMNS
        assert [r["job_id"] for r in rows] == ["a", "b"]
        assert rows[0]["skills"] == "Python; SQL"


def test_snapshot_and_run_log_are_recorded():
    with tempfile.TemporaryDirectory() as tmp:
        storage = Storage(Path(tmp) / "out.csv", Path(tmp) / "db.sqlite")
        run_id = storage.start_run(NOW)
        storage.save_records([make_record("a"), make_record("b", skills=("Python",), remote=False)])
        storage.snapshot(run_id)
        storage.finish_run(run_id, "success", 2, 2, 0, [])
        conn = sqlite3.connect(Path(tmp) / "db.sqlite")
        counts = dict(conn.execute("SELECT skill, job_count FROM skill_snapshots").fetchall())
        market = conn.execute("SELECT total_jobs, remote_jobs, avg_salary FROM market_snapshots").fetchone()
        status = conn.execute("SELECT status, records_new FROM runs").fetchone()
        conn.close()
        assert counts == {"Python": 2, "SQL": 1}
        assert market == (2, 1, 120000.0)
        assert status == ("success", 2)
