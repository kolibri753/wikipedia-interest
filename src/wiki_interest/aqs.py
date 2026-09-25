"""Client for the Wikimedia Analytics (AQS) pageviews endpoints.

404 means "no data for this title/period" (typical for a misspelt title or a
page that did not exist yet). We return an empty list rather than raising so
callers can distinguish 'zero interest / bad title' from a real failure.
Transport details (UA, throttling, retries, cache) live in http.py.
"""

from __future__ import annotations

from .http import JsonHttpClient, ApiError as AQSError  # noqa: F401  (re-export for callers)
from .titles import title_path_segment

BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews"
ACCESS = ("all-access", "desktop", "mobile-app", "mobile-web")
AGENT = ("all-agents", "user", "spider", "automated")
GRANULARITY = ("hourly", "daily", "monthly")


class AQSClient(JsonHttpClient):
    # ----------------------------------------------------------------- URLs #
    @staticmethod
    def per_article_url(
        project: str,
        title: str,
        start: str,
        end: str,
        granularity: str = "daily",
        access: str = "all-access",
        agent: str = "user",
    ) -> str:
        _check(access, ACCESS, "access")
        _check(agent, AGENT, "agent")
        _check(granularity, GRANULARITY, "granularity")
        return f"{BASE}/per-article/{project}/{access}/{agent}/{title_path_segment(title)}/{granularity}/{start}/{end}"

    @staticmethod
    def aggregate_url(
        project: str,
        start: str,
        end: str,
        granularity: str = "monthly",
        access: str = "all-access",
        agent: str = "user",
    ) -> str:
        _check(access, ACCESS, "access")
        _check(agent, AGENT, "agent")
        _check(granularity, GRANULARITY, "granularity")
        return f"{BASE}/aggregate/{project}/{access}/{agent}/{granularity}/{start}00/{end}00"  # needs the hour

    # ------------------------------------------------------------ requests #
    def per_article(self, project: str, title: str, start: str, end: str, **kw) -> list[dict]:
        data = self.get_json(self.per_article_url(project, title, start, end, **kw))
        return data.get("items", []) if data else []

    def aggregate(self, project: str, start: str, end: str, **kw) -> list[dict]:
        data = self.get_json(self.aggregate_url(project, start, end, **kw))
        return data.get("items", []) if data else []


def _check(value: str, allowed: tuple[str, ...], name: str) -> None:
    if value not in allowed:
        raise ValueError(f"{name} must be one of {allowed}, got {value!r}")
