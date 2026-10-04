"""Cleaning, normalisation, de-duplication and schema validation of job records."""

import re
import unicodedata
from datetime import date, datetime
from typing import Any, Optional

from loguru import logger
from pydantic import BaseModel, Field, ValidationError, field_validator

from src.utils import stable_hash, truncate, utc_now

_ZERO_WIDTH = re.compile("[\u200b\u200c\u200d\u2060\ufeff]")
_WHITESPACE = re.compile(r"\s+")
_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%m/%d/%Y")
MAX_TITLE_CHARS = 200
_ACRONYMS = {"EMEA", "APAC", "LATAM", "USA", "UAE"}


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
class JobRecord(BaseModel):
    """Validated, analysis-ready job posting."""

    job_id: str
    source: str
    title: str
    company: Optional[str] = None
    location: Optional[str] = None
    remote: bool = False
    seniority: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_currency: Optional[str] = None
    skills: list[str] = Field(default_factory=list)
    posted_date: Optional[date] = None
    url: Optional[str] = None
    description: Optional[str] = None
    scraped_at: datetime

    @field_validator("title", mode="before")
    @classmethod
    def _title_not_empty(cls, value: Any) -> str:
        text = str(value).strip() if value is not None else ""
        if not text:
            raise ValueError("title must not be empty")
        return text

    @field_validator("skills", mode="before")
    @classmethod
    def _skills_default(cls, value: Any) -> list[str]:
        return list(value) if value else []

    @field_validator("salary_min", "salary_max", mode="before")
    @classmethod
    def _salary_positive(cls, value: Any) -> Optional[float]:
        if value is None:
            return None
        number = float(value)
        return number if number > 0 else None


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------
def normalize_text(value: Optional[str]) -> Optional[str]:
    """Unicode-normalise (NFKC), drop zero-width chars, collapse all whitespace; empty -> None."""
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value))
    text = _ZERO_WIDTH.sub("", text)
    text = _WHITESPACE.sub(" ", text).strip()
    return text or None


def _fix_word_case(word: str) -> str:
    """Capitalise ALL-CAPS / all-lowercase words; keep short codes (NY, EU) and acronyms intact."""
    core = word.strip("()[]")
    if not core:
        return word
    if core.isupper() and (len(core) <= 3 or core in _ACRONYMS):
        return word
    if core.isupper() or core.islower():
        return word.replace(core, core.capitalize(), 1)
    return word


def normalize_location(value: Optional[str]) -> Optional[str]:
    """Standardise a location (spacing, trailing punctuation, casing of ALL-CAPS/lowercase words)."""
    text = normalize_text(value)
    if text is None:
        return None
    text = re.sub(r"\s*,\s*", ", ", text).strip(" ,;.-")
    if not text:
        return None
    return " ".join(_fix_word_case(word) for word in text.split(" "))


def normalize_url(value: Optional[str]) -> Optional[str]:
    """Return a stripped http(s) URL, or None for anything else."""
    text = normalize_text(value)
    if text and text.lower().startswith(("http://", "https://")):
        return text
    return None


def parse_date(value: Optional[str]) -> Optional[date]:
    """Parse common date formats (ISO 8601 with or without time/zone, 'April 8, 2021', ...)."""
    text = normalize_text(value)
    if text is None:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    logger.debug("Could not parse date: {!r}", text)
    return None


def compute_job_id(
    source: str,
    title: Optional[str],
    company: Optional[str],
    location: Optional[str],
    url: Optional[str],
) -> str:
    """Stable identity hash used for de-duplication across runs."""
    return stable_hash(source, title, company, location, url)


# ---------------------------------------------------------------------------
# Record-level cleaning
# ---------------------------------------------------------------------------
def clean_record(raw: dict[str, Any], scraped_at: Optional[datetime] = None) -> Optional[dict[str, Any]]:
    """Clean one raw record. Returns ``None`` if it has no usable title."""
    title = normalize_text(raw.get("title"))
    if not title:
        return None
    title = truncate(title, MAX_TITLE_CHARS) or title
    source = normalize_text(raw.get("source")) or "unknown"
    company = normalize_text(raw.get("company"))
    location = normalize_location(raw.get("location"))
    url = normalize_url(raw.get("url"))
    return {
        "job_id": compute_job_id(source, title, company, location, url),
        "source": source,
        "title": title,
        "company": company,
        "location": location,
        "posted_date": parse_date(raw.get("posted")),
        "url": url,
        "description": normalize_text(raw.get("description")),
        "scraped_at": scraped_at or utc_now(),
    }


def deduplicate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop records whose ``job_id`` was already seen, keeping the first occurrence."""
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for record in records:
        if record["job_id"] in seen:
            continue
        seen.add(record["job_id"])
        unique.append(record)
    return unique


def clean_records(raw_records: list[dict[str, Any]], scraped_at: Optional[datetime] = None) -> list[dict[str, Any]]:
    """Clean every raw record, discard unusable ones and remove duplicates."""
    cleaned = [rec for rec in (clean_record(raw, scraped_at) for raw in raw_records) if rec]
    unique = deduplicate(cleaned)
    logger.info(
        "Cleaning: {} raw -> {} usable -> {} unique", len(raw_records), len(cleaned), len(unique)
    )
    return unique


def validate_records(records: list[dict[str, Any]]) -> tuple[list[JobRecord], int]:
    """Validate dictionaries against :class:`JobRecord`; returns (valid_records, invalid_count)."""
    valid: list[JobRecord] = []
    invalid = 0
    for record in records:
        try:
            valid.append(JobRecord(**record))
        except (ValidationError, ValueError, TypeError) as exc:
            invalid += 1
            logger.warning("Dropping invalid record {!r}: {}", record.get("title"), exc)
    return valid, invalid
