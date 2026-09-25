"""Shared HTTP plumbing for the Wikimedia REST (AQS) and Action API clients.

- Standard-library urllib: a few dozen GETs do not justify a dependency.
- Serial requests with a small pause. Wikimedia asks clients to wait for each
  response before sending the next; the AQS access policy has no fixed quota.
- A descriptive User-Agent with contact info is required by Wikimedia's
  User-Agent policy; anonymous clients may be blocked without notice.
- One injectable `http_get` so tests and the eval harness replay recorded
  responses without network access.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping

from .cache import JsonCache

import os

DEFAULT_USER_AGENT = (
    "wikipedia-interest-skill/0.1 "
    "(https://github.com/kolibri753/wikipedia-interest; open an issue for contact) "
    "python-urllib"
)


def default_user_agent() -> str:
    """Contactable User-Agent; the environment (or .env) wins over the built-in default."""
    return os.environ.get("WIKI_INTEREST_USER_AGENT") or DEFAULT_USER_AGENT


RETRY_STATUSES = {429, 500, 502, 503, 504}


@dataclass
class HttpResponse:
    status: int
    body: bytes
    headers: Mapping[str, str]


HttpGet = Callable[[str, Mapping[str, str]], HttpResponse]


def urllib_get(url: str, headers: Mapping[str, str], timeout: float = 30.0) -> HttpResponse:
    req = urllib.request.Request(url, headers=dict(headers), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return HttpResponse(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as e:  # 4xx/5xx still carry a body
        return HttpResponse(e.code, e.read() if e.fp else b"", dict(e.headers or {}))


class ApiError(RuntimeError):
    """Non-recoverable API failure (after retries)."""


class JsonHttpClient:
    def __init__(
        self,
        user_agent: str | None = None,
        cache: JsonCache | None = None,
        http_get: HttpGet = urllib_get,
        sleep: Callable[[float], None] = time.sleep,
        min_interval: float = 0.25,
        max_retries: int = 4,
    ):
        self.user_agent = user_agent or default_user_agent()
        self.cache = cache if cache is not None else JsonCache()
        self._http_get = http_get
        self._sleep = sleep
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.requests_made = 0
        self._last_request_at = 0.0

    def get_json(self, url: str, cacheable: bool = True) -> dict | None:
        """GET a JSON document. Returns None on 404 (callers decide what that
        means), raises ApiError on other failures after retries."""
        if cacheable:
            cached = self.cache.get(url)
            if cached is not None:
                return cached if cached != {"__404__": True} else None
        data = self._fetch(url)
        if cacheable:
            self.cache.set(url, data if data is not None else {"__404__": True})
        return data

    def _fetch(self, url: str) -> dict | None:
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        delay = 1.0
        for attempt in range(self.max_retries + 1):
            self._throttle()
            resp = self._http_get(url, headers)
            self.requests_made += 1
            if resp.status == 200:
                return json.loads(resp.body.decode("utf-8"))
            if resp.status == 404:
                return None
            if resp.status in RETRY_STATUSES and attempt < self.max_retries:
                retry_after = _retry_after(resp.headers)
                self._sleep(retry_after if retry_after is not None else delay)
                delay = min(delay * 2, 30.0)
                continue
            raise ApiError(f"HTTP {resp.status} for {url}: {_detail(resp.body)}")
        raise ApiError(f"gave up after {self.max_retries} retries: {url}")

    def _throttle(self) -> None:
        now = time.monotonic()
        wait = self.min_interval - (now - self._last_request_at)
        if wait > 0:
            self._sleep(wait)
        self._last_request_at = time.monotonic()


def _retry_after(headers: Mapping[str, str]) -> float | None:
    for k, v in headers.items():
        if k.lower() == "retry-after":
            try:
                return float(v)
            except ValueError:
                return None
    return None


def _detail(body: bytes) -> str:
    try:
        d = json.loads(body.decode("utf-8"))
        return str(d.get("detail") or d.get("title") or d)[:200]
    except Exception:
        return body[:200].decode("utf-8", "replace")
