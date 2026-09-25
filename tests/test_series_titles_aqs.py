import json
from datetime import date

import pytest

from wiki_interest import titles
from wiki_interest.aqs import AQSClient, AQSError
from wiki_interest.http import HttpResponse
from wiki_interest.cache import JsonCache
from wiki_interest.series import (
    analysis_window,
    items_to_daily,
    daily_to_monthly,
    monthly_from_items,
    merge_daily,
    parse_timestamp,
)


# ------------------------------------------------------------------ windows
def test_window_is_last_complete_months():
    w = analysis_window(24, date(2026, 9, 22))
    assert (w.start, w.end) == (date(2024, 9, 1), date(2026, 8, 31))
    assert len(w.months) == 24 and w.months[0] == (2024, 9) and w.months[-1] == (2026, 8)
    assert w.aqs() == ("20240901", "20260831")


def test_window_crosses_year_boundary_and_leap_feb():
    w = analysis_window(1, date(2024, 3, 5))
    assert (w.start, w.end) == (date(2024, 2, 1), date(2024, 2, 29))
    w = analysis_window(3, date(2025, 1, 15))
    assert (w.start, w.end) == (date(2024, 10, 1), date(2024, 12, 31))


# ------------------------------------------------------------------ parsing
def test_zero_fill_of_sparse_daily_items():
    w = analysis_window(1, date(2024, 3, 5))  # Feb 2024, 29 days
    items = [
        {"timestamp": "2024020100", "views": 5},
        {"timestamp": "2024021500", "views": 7},
        {"timestamp": "2024030100", "views": 99},
    ]  # out of window -> ignored
    s = items_to_daily(items, w)
    assert len(s) == 29 and s.total == 12 and s.present_days == 2
    assert s.views[0] == 5 and s.views[14] == 7 and s.views[1] == 0


def test_daily_to_monthly_and_merge():
    w = analysis_window(2, date(2024, 3, 5))  # Jan + Feb 2024
    a = items_to_daily([{"timestamp": "2024010100", "views": 10}, {"timestamp": "2024020100", "views": 20}], w)
    b = items_to_daily([{"timestamp": "2024010500", "views": 1}], w)
    m = daily_to_monthly(merge_daily([a, b]).views, w.start)
    assert [(p.label, p.views) for p in m] == [("2024-01", 11), ("2024-02", 20)]


def test_monthly_from_aggregate_items_fills_missing_months():
    w = analysis_window(3, date(2024, 4, 1))
    m = monthly_from_items([{"timestamp": "2024010100", "views": 1000}], w)
    assert [p.views for p in m] == [1000, 0, 0]


def test_parse_timestamp():
    assert parse_timestamp("2024010100") == date(2024, 1, 1)


# ------------------------------------------------------------------ titles
@pytest.mark.parametrize(
    "raw,norm",
    [
        ("intermittent fasting", "Intermittent_fasting"),
        ("  Post   przerywany ", "Post_przerywany"),
        ("iPhone", "IPhone"),
        ("ßeta", "ßeta"),  # PHP ucfirst keeps ß; Python would make 'SS'
        ("Астрономія", "Астрономія"),
    ],
)
def test_normalize_title(raw, norm):
    assert titles.normalize_title(raw) == norm


def test_title_path_segment_encodes_dangerous_chars():
    assert titles.title_path_segment("AC/DC") == "AC%2FDC"
    assert titles.title_path_segment("100%") == "100%25"
    assert titles.title_path_segment("Who?") == "Who%3F"
    assert titles.title_path_segment("Přerušovaný půst") == "P%C5%99eru%C5%A1ovan%C3%BD_p%C5%AFst"


def test_project_host():
    assert titles.project_host("pl") == "pl.wikipedia.org"
    assert titles.project_host("zh-yue") == "zh-yue.wikipedia.org"
    assert titles.project_host("de.wikipedia.org") == "de.wikipedia.org"
    with pytest.raises(ValueError):
        titles.project_host("../evil")


# ------------------------------------------------------------------ AQS client
class FakeHttp:
    """Scripted responses; records calls so tests can assert on retries/caching."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, headers))
        status, body, hdrs = self.script.pop(0)
        return HttpResponse(status, json.dumps(body).encode() if not isinstance(body, bytes) else body, hdrs)


def make_client(script, tmp_path, **kw):
    http = FakeHttp(script)
    sleeps = []
    c = AQSClient(http_get=http, sleep=sleeps.append, cache=JsonCache(tmp_path), min_interval=0, **kw)
    return c, http, sleeps


def test_url_shapes():
    u = AQSClient.per_article_url("pl.wikipedia.org", "Post przerywany", "20240901", "20260831")
    assert u == (
        "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
        "pl.wikipedia.org/all-access/user/Post_przerywany/daily/20240901/20260831"
    )
    a = AQSClient.aggregate_url("pl.wikipedia.org", "20240901", "20260831")
    assert a.endswith("/aggregate/pl.wikipedia.org/all-access/user/monthly/2024090100/2026083100")
    with pytest.raises(ValueError):
        AQSClient.per_article_url("pl.wikipedia.org", "X", "1", "2", agent="robots")


def test_success_sets_user_agent_and_caches(tmp_path):
    payload = {"items": [{"timestamp": "2024090100", "views": 3}]}
    c, http, _ = make_client([(200, payload, {})], tmp_path)
    r1 = c.per_article("pl.wikipedia.org", "X", "20240901", "20240930")
    r2 = c.per_article("pl.wikipedia.org", "X", "20240901", "20240930")  # served from cache
    assert r1 == r2 == payload["items"]
    assert len(http.calls) == 1 and c.requests_made == 1
    assert "wikipedia-interest-skill" in http.calls[0][1]["User-Agent"]
    assert c.cache.hits == 1


def test_404_is_empty_not_error(tmp_path):
    c, http, _ = make_client([(404, {"type": "not_found", "detail": "no data"}, {})], tmp_path)
    assert c.per_article("pl.wikipedia.org", "Nope", "20240901", "20240930") == []


def test_retry_on_429_honours_retry_after_then_succeeds(tmp_path):
    c, http, sleeps = make_client(
        [
            (429, {"detail": "slow down"}, {"Retry-After": "2"}),
            (503, b"upstream", {}),
            (200, {"items": []}, {}),
        ],
        tmp_path,
    )
    assert c.aggregate("pl.wikipedia.org", "20240901", "20260831") == []
    assert len(http.calls) == 3
    assert sleeps[0] == 2.0  # from Retry-After
    assert sleeps[1] == 2.0  # backoff doubles per attempt (1s -> 2s), independent of Retry-After


def test_gives_up_after_max_retries(tmp_path):
    c, http, _ = make_client([(500, b"x", {})] * 3, tmp_path, max_retries=2)
    with pytest.raises(AQSError):
        c.aggregate("pl.wikipedia.org", "20240901", "20260831")
    assert len(http.calls) == 3


def test_400_is_not_retried(tmp_path):
    c, http, _ = make_client([(400, {"detail": "bad timestamp"}, {})], tmp_path)
    with pytest.raises(AQSError, match="bad timestamp"):
        c.aggregate("pl.wikipedia.org", "bad", "bad")
    assert len(http.calls) == 1
