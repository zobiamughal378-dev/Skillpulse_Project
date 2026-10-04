"""HTTP layer: rotating user-agents, polite delays, retries with backoff, pagination."""

import random
import time
from typing import Any, Callable, Iterator, Optional
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests
from loguru import logger
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.config_loader import HttpConfig

try:  # fake_useragent ships a UA database; fall back to the config list if it fails
    from fake_useragent import UserAgent
except ImportError:  # pragma: no cover - optional dependency
    UserAgent = None  # type: ignore[assignment,misc]

RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
ACCEPT_LANGUAGES = ["en-US,en;q=0.9", "en-GB,en;q=0.9", "en-US,en;q=0.8,nl;q=0.6"]
MAX_RETRY_AFTER_SECONDS = 60


class RetryableHTTPError(Exception):
    """Raised for HTTP statuses that are worth retrying (429 and 5xx)."""


class RobotsDisallowedError(Exception):
    """Raised when robots.txt forbids fetching a URL."""


class Fetcher:
    """Polite HTTP client with retries, rotating headers and robots.txt support."""

    def __init__(self, http: HttpConfig, sleep: Callable[[float], None] = time.sleep) -> None:
        self.http = http
        self.session = requests.Session()
        self._sleep = sleep
        self._last_request_at: Optional[float] = None
        self._robots_cache: dict[str, RobotFileParser] = {}
        self._ua_source: Any = self._init_user_agent_source()

    # ------------------------------------------------------------------ headers
    def _init_user_agent_source(self) -> Any:
        """Create a fake_useragent generator if the library is available and working."""
        if UserAgent is None:
            return None
        try:
            return UserAgent()
        except Exception as exc:  # noqa: BLE001 - any failure means "use the fallback list"
            logger.warning("fake_useragent unavailable ({}); using fallback user-agents", exc)
            return None

    def _random_user_agent(self) -> str:
        """Pick a random browser User-Agent string."""
        if self._ua_source is not None:
            try:
                return str(self._ua_source.random)
            except Exception:  # noqa: BLE001
                pass
        return random.choice(self.http.fallback_user_agents)

    def build_headers(self) -> dict[str, str]:
        """Return a fresh, browser-like header set for one request."""
        return {
            "User-Agent": self._random_user_agent(),
            "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
            "Accept-Language": random.choice(ACCEPT_LANGUAGES),
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "DNT": "1",
        }

    # ---------------------------------------------------------------- politeness
    def _respect_delay(self) -> None:
        """Sleep long enough to keep a minimum gap (plus jitter) between requests."""
        if self._last_request_at is not None:
            wanted = self.http.delay_seconds + random.uniform(0, self.http.jitter_seconds)
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < wanted:
                self._sleep(wanted - elapsed)
        self._last_request_at = time.monotonic()

    def _robots_allowed(self, url: str) -> bool:
        """Check robots.txt for ``url`` (cached per host). Missing/unreadable robots = allowed."""
        if not self.http.respect_robots_txt:
            return True
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        parser = self._robots_cache.get(origin)
        if parser is None:
            parser = RobotFileParser()
            try:
                response = self.session.get(
                    f"{origin}/robots.txt",
                    headers=self.build_headers(),
                    timeout=self.http.timeout_seconds,
                )
                if response.status_code == 200:
                    parser.parse(response.text.splitlines())
                else:
                    parser.allow_all = True
            except requests.RequestException as exc:
                logger.warning("Could not read robots.txt for {}: {}", origin, exc)
                parser.allow_all = True
            self._robots_cache[origin] = parser
        return parser.can_fetch("*", url)

    # ------------------------------------------------------------------ requests
    def _request(self, url: str, params: Optional[dict[str, Any]]) -> requests.Response:
        """Perform a single GET; raise RetryableHTTPError for 429/5xx so tenacity retries it."""
        self._respect_delay()
        response = self.session.get(
            url,
            params=params,
            headers=self.build_headers(),
            timeout=self.http.timeout_seconds,
        )
        if response.status_code in RETRYABLE_STATUS_CODES:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():  # server told us how long to back off
                self._sleep(min(int(retry_after), MAX_RETRY_AFTER_SECONDS))
            raise RetryableHTTPError(f"HTTP {response.status_code} for {url}")
        response.raise_for_status()
        return response

    def get(
        self,
        url: str,
        params: Optional[dict[str, Any]] = None,
        check_robots: bool = True,
    ) -> requests.Response:
        """GET ``url`` with retries and exponential backoff.

        ``check_robots`` should be True for scraped HTML pages. Documented public JSON
        APIs (e.g. the HN Algolia API) pass False because robots.txt targets crawlers.
        """
        if check_robots and not self._robots_allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows fetching {url}")

        retryer = Retrying(
            stop=stop_after_attempt(max(1, self.http.max_retries)),
            wait=wait_exponential(
                multiplier=self.http.backoff_min_seconds,
                min=self.http.backoff_min_seconds,
                max=self.http.backoff_max_seconds,
            ),
            retry=retry_if_exception_type(
                (requests.ConnectionError, requests.Timeout, RetryableHTTPError)
            ),
            reraise=True,
            before_sleep=self._log_retry,
        )
        return retryer(self._request, url, params)

    @staticmethod
    def _log_retry(retry_state: Any) -> None:
        """Log each retry attempt (called by tenacity before it sleeps)."""
        error = retry_state.outcome.exception() if retry_state.outcome else None
        logger.warning("Request failed (attempt {}): {} - retrying", retry_state.attempt_number, error)

    def get_text(self, url: str, params: Optional[dict[str, Any]] = None, check_robots: bool = True) -> str:
        """Return the response body as text."""
        response = self.get(url, params=params, check_robots=check_robots)
        response.encoding = response.encoding or "utf-8"
        return response.text

    def get_json(self, url: str, params: Optional[dict[str, Any]] = None, check_robots: bool = False) -> Any:
        """Return the decoded JSON body (robots.txt check off by default for APIs)."""
        return self.get(url, params=params, check_robots=check_robots).json()


# ---------------------------------------------------------------------------
# Pagination helpers (one per source type)
# ---------------------------------------------------------------------------
def iter_html_pages(
    fetcher: Fetcher,
    first_url: str,
    page_url_template: Optional[str] = None,
    max_pages: int = 1,
) -> Iterator[str]:
    """Yield the HTML of each listing page.

    The first page is ``first_url``. Further pages use ``page_url_template`` (containing
    ``{page}``) up to ``max_pages``; pagination stops early on a 404.
    """
    yield fetcher.get_text(first_url)
    if not page_url_template:
        return
    for page in range(2, max_pages + 1):
        try:
            yield fetcher.get_text(page_url_template.format(page=page))
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 404:
                logger.info("Pagination ended at page {} (404)", page)
                return
            raise


def find_hn_hiring_threads(fetcher: Fetcher, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Find the latest monthly "Ask HN: Who is hiring?" story threads via the Algolia API."""
    data = fetcher.get_json(
        cfg["search_url"],
        params={
            "query": "Ask HN: Who is hiring?",
            "tags": "story,author_whoishiring",
            "hitsPerPage": 10,
        },
    )
    threads = [
        hit
        for hit in data.get("hits", [])
        if str(hit.get("title", "")).lower().startswith("ask hn: who is hiring")
    ]
    return threads[: int(cfg.get("threads", 1))]


def iter_hn_items(fetcher: Fetcher, cfg: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield top-level job comments (one per posting) from the latest hiring thread(s)."""
    per_page = int(cfg.get("hits_per_page", 100))
    for thread in find_hn_hiring_threads(fetcher, cfg):
        story_id = str(thread.get("objectID"))
        logger.info("Reading HN thread: {}", thread.get("title"))
        for page in range(int(cfg.get("max_pages", 5))):
            data = fetcher.get_json(
                cfg["search_url"],
                params={"tags": f"comment,story_{story_id}", "hitsPerPage": per_page, "page": page},
            )
            hits = data.get("hits", [])
            if not hits:
                break
            for hit in hits:
                if str(hit.get("parent_id")) == story_id:  # top-level comments only
                    hit["thread_title"] = thread.get("title")
                    yield hit
            if page + 1 >= int(data.get("nbPages", 0)):
                break
