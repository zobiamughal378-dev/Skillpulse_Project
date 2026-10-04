"""Persistence: append-only CSV export plus a SQLite database with historical snapshots."""

import csv
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

from src.cleaner import JobRecord
from src.utils import ensure_parent_dir, utc_now

CSV_COLUMNS = [
    "job_id", "source", "title", "company", "location", "remote", "seniority",
    "salary_min", "salary_max", "salary_currency", "skills", "posted_date",
    "url", "description", "scraped_at",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    source          TEXT NOT NULL,
    title           TEXT NOT NULL,
    company         TEXT,
    location        TEXT,
    remote          INTEGER NOT NULL DEFAULT 0,
    seniority       TEXT,
    salary_min      REAL,
    salary_max      REAL,
    salary_currency TEXT,
    skills          TEXT,
    posted_date     TEXT,
    url             TEXT,
    description     TEXT,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_skills (
    job_id TEXT NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    skill  TEXT NOT NULL,
    PRIMARY KEY (job_id, skill)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT NOT NULL,
    finished_at     TEXT,
    status          TEXT NOT NULL DEFAULT 'running',
    records_fetched INTEGER DEFAULT 0,
    records_new     INTEGER DEFAULT 0,
    records_invalid INTEGER DEFAULT 0,
    errors          TEXT
);
CREATE TABLE IF NOT EXISTS skill_snapshots (
    run_id        INTEGER NOT NULL,
    snapshot_date TEXT NOT NULL,
    skill         TEXT NOT NULL,
    job_count     INTEGER NOT NULL,
    PRIMARY KEY (run_id, skill)
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    run_id        INTEGER PRIMARY KEY,
    snapshot_date TEXT NOT NULL,
    total_jobs    INTEGER NOT NULL,
    remote_jobs   INTEGER NOT NULL,
    avg_salary    REAL
);
CREATE INDEX IF NOT EXISTS idx_jobs_source ON jobs(source);
CREATE INDEX IF NOT EXISTS idx_jobs_posted ON jobs(posted_date);
CREATE INDEX IF NOT EXISTS idx_skills_skill ON job_skills(skill);
"""


class Storage:
    """Writes job records to SQLite (source of truth) and to a CSV export."""

    def __init__(self, csv_path: Path, db_path: Path) -> None:
        self.csv_path = ensure_parent_dir(Path(csv_path))
        self.db_path = ensure_parent_dir(Path(db_path))
        self._init_db()

    # -------------------------------------------------------------- connection
    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection, commit on success, roll back on error, always close."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Create tables and indexes if they do not exist."""
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    # -------------------------------------------------------------- run log
    def start_run(self, started_at: Optional[datetime] = None) -> int:
        """Insert a new row in ``runs`` and return its id."""
        started = (started_at or utc_now()).isoformat()
        with self._connect() as conn:
            cursor = conn.execute("INSERT INTO runs (started_at, status) VALUES (?, 'running')", (started,))
            return int(cursor.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        fetched: int,
        new: int,
        invalid: int,
        errors: list[str],
        finished_at: Optional[datetime] = None,
    ) -> None:
        """Close a run with its final counters and any error messages."""
        with self._connect() as conn:
            conn.execute(
                """UPDATE runs SET finished_at = ?, status = ?, records_fetched = ?,
                   records_new = ?, records_invalid = ?, errors = ? WHERE run_id = ?""",
                ((finished_at or utc_now()).isoformat(), status, fetched, new, invalid,
                 "; ".join(errors) if errors else None, run_id),
            )

    # -------------------------------------------------------------- records
    def save_records(self, records: list[JobRecord]) -> list[JobRecord]:
        """Insert unseen jobs, refresh ``last_seen`` for known ones, append new jobs to the CSV.

        Returns the list of records that were new to the database.
        """
        new_records: list[JobRecord] = []
        with self._connect() as conn:
            for record in records:
                seen_at = record.scraped_at.isoformat()
                exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (record.job_id,)).fetchone()
                if exists:
                    conn.execute("UPDATE jobs SET last_seen = ? WHERE job_id = ?", (seen_at, record.job_id))
                    continue
                conn.execute(
                    """INSERT INTO jobs (job_id, source, title, company, location, remote, seniority,
                       salary_min, salary_max, salary_currency, skills, posted_date, url, description,
                       first_seen, last_seen)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        record.job_id, record.source, record.title, record.company, record.location,
                        int(record.remote), record.seniority, record.salary_min, record.salary_max,
                        record.salary_currency, ", ".join(record.skills),
                        record.posted_date.isoformat() if record.posted_date else None,
                        record.url, record.description, seen_at, seen_at,
                    ),
                )
                conn.executemany(
                    "INSERT OR IGNORE INTO job_skills (job_id, skill) VALUES (?, ?)",
                    [(record.job_id, skill) for skill in record.skills],
                )
                new_records.append(record)
        self.append_csv(new_records)
        logger.info("Storage: {} new, {} already known", len(new_records), len(records) - len(new_records))
        return new_records

    def append_csv(self, records: list[JobRecord]) -> None:
        """Append records to the CSV, writing the header only when the file is new or empty."""
        if not records:
            return
        write_header = not self.csv_path.exists() or self.csv_path.stat().st_size == 0
        with self.csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS)
            if write_header:
                writer.writeheader()
            for record in records:
                row = record.model_dump()
                row["skills"] = "; ".join(record.skills)
                row["posted_date"] = record.posted_date.isoformat() if record.posted_date else ""
                row["scraped_at"] = record.scraped_at.isoformat()
                writer.writerow({column: ("" if row.get(column) is None else row.get(column)) for column in CSV_COLUMNS})

    # -------------------------------------------------------------- snapshots
    def snapshot(self, run_id: int, snapshot_date: Optional[date] = None) -> None:
        """Store skill counts and market totals as of this run (for trend charts)."""
        day = (snapshot_date or utc_now().date()).isoformat()
        with self._connect() as conn:
            conn.execute("DELETE FROM skill_snapshots WHERE run_id = ?", (run_id,))
            conn.execute(
                """INSERT INTO skill_snapshots (run_id, snapshot_date, skill, job_count)
                   SELECT ?, ?, skill, COUNT(*) FROM job_skills GROUP BY skill""",
                (run_id, day),
            )
            conn.execute(
                """INSERT OR REPLACE INTO market_snapshots (run_id, snapshot_date, total_jobs, remote_jobs, avg_salary)
                   SELECT ?, ?, COUNT(*), COALESCE(SUM(remote), 0),
                          AVG(CASE WHEN salary_min IS NOT NULL AND salary_max IS NOT NULL
                                   THEN (salary_min + salary_max) / 2.0 END)
                   FROM jobs""",
                (run_id, day),
            )

    def count_jobs(self) -> int:
        """Total number of jobs stored."""
        with self._connect() as conn:
            return int(conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0])
