"""Tests for src/fetcher.py using a fake HTTP session (no network access needed)."""

import requests

from src.config_loader import HttpConfig
from src.fetcher import Fetcher, RetryableHTTPError, RobotsDisallowedError, iter_html_pages


class FakeResponse:
    def __init__(self, status_code=200, text="", headers=None, payload=None):
        self.status_code, self.text, self.headers = status_code, text, headers or {}
        self.encoding, self._payload = "utf-8", payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self):
        return self._payload


class FakeSession:
    """Returns queued responses in order; robots.txt requests are answered separately."""

    def __init__(self, responses, robots_text=None):
        self.responses, self.robots_text, self.calls = list(responses), robots_text, []

    def get(self, url, params=None, headers=None, timeout=None):
        if url.endswith("/robots.txt"):
            return FakeResponse(200, self.robots_text) if self.robots_text is not None else FakeResponse(404)
        self.calls.append((url, params, headers))
        return self.responses.pop(0)


def make_fetcher(session, robots=False, retries=3):
    http = HttpConfig(max_retries=retries, delay_seconds=0, jitter_seconds=0, respect_robots_txt=robots)
    fetcher = Fetcher(http, sleep=lambda seconds: None)
    fetcher.session = session
    return fetcher


def test_retries_on_server_errors_then_succeeds():
    session = FakeSession([FakeResponse(503), FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200, "ok")])
    assert make_fetcher(session).get_text("https://example.org/page") == "ok"
    assert len(session.calls) == 3


def test_gives_up_after_max_retries():
    session = FakeSession([FakeResponse(500)] * 5)
    try:
        make_fetcher(session, retries=3).get_text("https://example.org/page")
        raise AssertionError("expected RetryableHTTPError")
    except RetryableHTTPError:
        assert len(session.calls) == 3


def test_client_errors_are_not_retried():
    session = FakeSession([FakeResponse(404)])
    try:
        make_fetcher(session).get_text("https://example.org/missing")
        raise AssertionError("expected HTTPError")
    except requests.HTTPError:
        assert len(session.calls) == 1


def test_robots_txt_is_respected():
    session = FakeSession([FakeResponse(200, "allowed")], robots_text="User-agent: *\nDisallow: /private")
    fetcher = make_fetcher(session, robots=True)
    try:
        fetcher.get_text("https://example.org/private/data")
        raise AssertionError("expected RobotsDisallowedError")
    except RobotsDisallowedError:
        assert session.calls == []
    assert fetcher.get_text("https://example.org/public") == "allowed"


def test_json_apis_skip_robots_check_and_headers_are_browser_like():
    session = FakeSession([FakeResponse(200, payload={"hits": []})], robots_text="User-agent: *\nDisallow: /")
    fetcher = make_fetcher(session, robots=True)
    assert fetcher.get_json("https://api.example.org/search") == {"hits": []}
    headers = session.calls[0][2]
    assert headers["User-Agent"].startswith("Mozilla/5.0") and "Accept-Language" in headers


def test_pagination_stops_at_first_404():
    session = FakeSession([FakeResponse(200, "p1"), FakeResponse(200, "p2"), FakeResponse(404)])
    pages = list(iter_html_pages(make_fetcher(session), "https://example.org/", "https://example.org/p{page}", max_pages=5))
    assert pages == ["p1", "p2"]
