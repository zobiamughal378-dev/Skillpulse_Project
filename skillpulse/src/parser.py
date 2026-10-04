"""HTML / JSON parsing: turn raw responses into plain dictionaries of job fields.

Every extractor is defensive: a missing field becomes ``None`` rather than an exception.
"""

import re
from typing import Any, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag
from loguru import logger

from src.utils import truncate

HN_ITEM_URL = "https://news.ycombinator.com/item?id={}"
MAX_HEADER_CHARS = 300
MAX_COMPANY_CHARS = 80

ROLE_WORDS = re.compile(
    r"\b(engineer|developer|scientist|analyst|manager|designer|architect|lead|director|"
    r"researcher|programmer|devops|sre|administrator|consultant|specialist|intern|"
    r"head of|cto|founder|product)\b",
    re.IGNORECASE,
)
SALARY_HINT = re.compile(r"[$€£]|\b\d{2,3}\s?k\b|\b(usd|eur|gbp)\b", re.IGNORECASE)
NON_LOCATION = re.compile(
    r"^(full[- ]?time|part[- ]?time|contract|contractor|internship|visa|relocation|equity|hiring|apply)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def select_first_text(node: Tag, selectors: list[str]) -> Optional[str]:
    """Return the text of the first selector that matches a non-empty element (fallback chain)."""
    for selector in selectors:
        element = node.select_one(selector)
        if element is not None:
            text = " ".join(element.get_text(" ", strip=True).split())  # collapse inner whitespace
            if text:
                return text
    return None


def select_date_text(node: Tag, selectors: list[str]) -> Optional[str]:
    """Return a date string, preferring a ``datetime`` attribute over visible text."""
    for selector in selectors:
        element = node.select_one(selector)
        if element is not None:
            value = element.get("datetime") or element.get_text(" ", strip=True)
            if value:
                return str(value)
    return None


def select_link(node: Tag, selectors: list[str], base_url: str) -> Optional[str]:
    """Return the absolute URL of the last matching link (e.g. the 'Apply' button)."""
    for selector in selectors:
        links = [el for el in node.select(selector) if el.get("href")]
        if links:
            return urljoin(base_url, str(links[-1]["href"]))
    return None


def html_to_text(html: str) -> str:
    """Convert an HTML fragment to text, turning <p> and <br> into line breaks."""
    soup = BeautifulSoup(html, "html.parser")
    for line_break in soup.find_all("br"):
        line_break.replace_with("\n")
    for paragraph in soup.find_all("p"):
        paragraph.insert_before("\n")
    return soup.get_text("")


# ---------------------------------------------------------------------------
# Source: fake_jobs (HTML practice site)
# ---------------------------------------------------------------------------
def parse_fake_jobs(html: str, selectors: dict[str, list[str]], base_url: str) -> list[dict[str, Any]]:
    """Parse one listing page of the fake-jobs practice site into raw job dictionaries."""
    soup = BeautifulSoup(html, "html.parser")

    cards: list[Tag] = []
    for card_selector in selectors.get("card", []):
        cards = soup.select(card_selector)
        if cards:
            break
    if not cards:
        logger.warning("No job cards found with selectors {}", selectors.get("card"))
        return []

    records: list[dict[str, Any]] = []
    for card in cards:
        title = select_first_text(card, selectors.get("title", []))
        if not title:
            logger.debug("Skipping a card without a title")
            continue
        records.append(
            {
                "source": "fake_jobs",
                "title": title,
                "company": select_first_text(card, selectors.get("company", [])),
                "location": select_first_text(card, selectors.get("location", [])),
                "posted": select_date_text(card, selectors.get("posted", [])),
                "url": select_link(card, selectors.get("link", []), base_url),
                "description": None,
            }
        )
    logger.info("fake_jobs: parsed {} of {} cards", len(records), len(cards))
    return records


# ---------------------------------------------------------------------------
# Source: hn_hiring (Hacker News "Who is hiring?" comments from the Algolia API)
# ---------------------------------------------------------------------------
def split_hn_header(header: str) -> Optional[dict[str, Optional[str]]]:
    """Split a ``Company | Role | Location | ...`` header line into fields.

    Returns ``None`` when the line does not look like a job header.
    """
    parts = [part.strip() for part in header.split("|") if part.strip()]
    if len(parts) < 2 or len(header) > MAX_HEADER_CHARS or len(parts[0]) > MAX_COMPANY_CHARS:
        return None

    company = parts[0]
    rest = parts[1:]
    title = next((part for part in rest if ROLE_WORDS.search(part)), rest[0])
    location_candidates = [
        part
        for part in rest
        if part is not title and not SALARY_HINT.search(part) and not NON_LOCATION.match(part)
    ]
    return {
        "company": company,
        "title": title,
        "location": location_candidates[0] if location_candidates else None,
    }


def parse_hn_item(item: dict[str, Any], max_chars: int = 1500) -> Optional[dict[str, Any]]:
    """Parse one HN comment into a raw job dictionary (``None`` if it is not a job post)."""
    html = item.get("comment_text") or ""
    if not html:
        return None
    lines = [line.strip() for line in html_to_text(html).split("\n") if line.strip()]
    if not lines:
        return None

    fields = split_hn_header(lines[0])
    if fields is None:
        return None

    object_id = item.get("objectID")
    return {
        "source": "hn_hiring",
        "title": fields["title"],
        "company": fields["company"],
        "location": fields["location"],
        "posted": item.get("created_at"),
        "url": HN_ITEM_URL.format(object_id) if object_id else None,
        "description": truncate("\n".join(lines), max_chars),
    }


def parse_hn_items(items: list[dict[str, Any]], max_chars: int = 1500) -> list[dict[str, Any]]:
    """Parse many HN comments, skipping those that are not job postings."""
    records = [rec for rec in (parse_hn_item(item, max_chars) for item in items) if rec]
    logger.info("hn_hiring: parsed {} job posts from {} comments", len(records), len(items))
    return records
