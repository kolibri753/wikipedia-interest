"""Time-series plumbing: analysis windows, AQS item parsing, daily -> monthly.

Two facts about the pageview API drive this module:
1. Days with zero views are simply absent from the response. A naive
   `len(items)` or a sum over items is right, but any per-day calculation
   (spike detection, day counts) needs explicit zero-filling.
2. Data is UTC and the current month is always partial. We therefore analyse
   *complete* months only, and default to windows that are multiples of 12 so
   that seasonal topics start and end at the same phase of the year.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #
def add_months(year: int, month: int, delta: int) -> tuple[int, int]:
    idx = year * 12 + (month - 1) + delta
    return idx // 12, idx % 12 + 1


def last_complete_month(today: date) -> tuple[int, int]:
    return add_months(today.year, today.month, -1)


@dataclass(frozen=True)
class Window:
    start: date  # first day of the first month
    end: date  # last day of the last month

    @property
    def months(self) -> list[tuple[int, int]]:
        out, (y, m) = [], (self.start.year, self.start.month)
        while (y, m) <= (self.end.year, self.end.month):
            out.append((y, m))
            y, m = add_months(y, m, 1)
        return out

    def aqs(self) -> tuple[str, str]:
        """(start, end) in the YYYYMMDD form the API expects."""
        return self.start.strftime("%Y%m%d"), self.end.strftime("%Y%m%d")

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.start.isoformat()}..{self.end.isoformat()}"


def analysis_window(months: int, today: date) -> Window:
    """The last `months` complete calendar months as of `today` (UTC)."""
    if months < 1:
        raise ValueError("months must be >= 1")
    ey, em = last_complete_month(today)
    sy, sm = add_months(ey, em, -(months - 1))
    return Window(start=date(sy, sm, 1), end=date(ey, em, calendar.monthrange(ey, em)[1]))


# --------------------------------------------------------------------------- #
# AQS item parsing
# --------------------------------------------------------------------------- #
def parse_timestamp(ts: str) -> date:
    """AQS timestamps look like '2024010100' (YYYYMMDDHH)."""
    return date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))


@dataclass
class DailySeries:
    start: date
    views: list[int] = field(default_factory=list)  # one entry per day from start
    present_days: int = 0  # days the API actually returned

    @property
    def dates(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(len(self.views))]

    @property
    def total(self) -> int:
        return sum(self.views)

    def __len__(self) -> int:
        return len(self.views)


def items_to_daily(items: Iterable[Mapping], window: Window) -> DailySeries:
    """Zero-fill the API's sparse daily items onto the full window."""
    n = (window.end - window.start).days + 1
    views = [0] * n
    present = 0
    for it in items:
        d = parse_timestamp(str(it["timestamp"]))
        if window.start <= d <= window.end:
            views[(d - window.start).days] += int(it["views"])
            present += 1
    return DailySeries(start=window.start, views=views, present_days=present)


def merge_daily(series: Sequence[DailySeries]) -> DailySeries:
    """Element-wise sum (used to add redirect traffic onto the target article)."""
    if not series:
        raise ValueError("merge_daily() of nothing")
    base = series[0]
    views = list(base.views)
    present = base.present_days
    for s in series[1:]:
        if s.start != base.start or len(s) != len(base):
            raise ValueError("series must share the same window")
        views = [a + b for a, b in zip(views, s.views)]
        present = max(present, s.present_days)
    return DailySeries(start=base.start, views=views, present_days=present)


# --------------------------------------------------------------------------- #
# Daily -> monthly
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MonthPoint:
    year: int
    month: int
    views: float

    @property
    def label(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"


def daily_to_monthly(values: Sequence[float], start: date) -> list[MonthPoint]:
    """Sum a contiguous daily sequence into calendar months. Works for the raw
    integer series and for a despiked float series alike."""
    buckets: dict[tuple[int, int], float] = {}
    order: list[tuple[int, int]] = []
    d = start
    for v in values:
        key = (d.year, d.month)
        if key not in buckets:
            buckets[key] = 0.0
            order.append(key)
        buckets[key] += v
        d += timedelta(days=1)
    return [MonthPoint(y, m, buckets[(y, m)]) for (y, m) in order]


def monthly_from_items(items: Iterable[Mapping], window: Window) -> list[MonthPoint]:
    """For endpoints queried at monthly granularity (project aggregates)."""
    by_month = {(y, m): 0.0 for (y, m) in window.months}
    for it in items:
        d = parse_timestamp(str(it["timestamp"]))
        if (d.year, d.month) in by_month:
            by_month[(d.year, d.month)] += float(it["views"])
    return [MonthPoint(y, m, by_month[(y, m)]) for (y, m) in window.months]
