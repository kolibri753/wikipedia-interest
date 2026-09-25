"""Turn a daily pageview series into the metrics the report and the agent use.

The output is a plain dict so it can be dumped as JSON, diffed in tests and
read by a small model without parsing anything clever. Every number here is
computed deterministically; the model's job is to interpret, not to calculate.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, timedelta
from statistics import median, mean
from typing import Sequence

from . import stats
from .series import DailySeries, MonthPoint, Window, daily_to_monthly, add_months

MIN_MONTHS_FOR_TREND = 6


def _months_after_creation(window: Window, first_revision: date | None) -> int:
    """How many complete months of the window the article actually existed for."""
    months = window.months
    if first_revision is None or first_revision <= window.start:
        return len(months)
    if first_revision > window.end:
        return 0
    # first complete month is the month after creation
    fy, fm = add_months(first_revision.year, first_revision.month, 1)
    return sum(1 for (y, m) in months if (y, m) >= (fy, fm))


def compute_metrics(
    daily: DailySeries,
    window: Window,
    project_monthly: Sequence[MonthPoint] | None = None,
    first_revision: date | None = None,
    spike_kwargs: dict | None = None,
) -> dict:
    """`daily` may start before `window.start` (a pad used only so the spike
    baseline has history on the first days of the window). Everything reported
    is trimmed to the window."""
    n_months = len(window.months)
    months_used = _months_after_creation(window, first_revision)
    partial_history = months_used < n_months

    pad = (window.start - daily.start).days
    if pad < 0:
        raise ValueError("daily series must start at or before the window")
    spikes_full = stats.detect_spikes(daily.views, **(spike_kwargs or {}))
    views = daily.views[pad:]
    despiked = spikes_full.despiked[pad:]
    spikes = stats.SpikeReport(
        baseline=spikes_full.baseline[pad:],
        spike_indices=[i - pad for i in spikes_full.spike_indices if i >= pad],
        excess=[e for i, e in zip(spikes_full.spike_indices, spikes_full.excess) if i >= pad],
        despiked=despiked,
        threshold=spikes_full.threshold,
    )
    daily = DailySeries(window.start, views, daily.present_days)
    monthly_raw = daily_to_monthly(views, window.start)
    monthly_desp = daily_to_monthly(despiked, window.start)

    # Restrict trend maths to the months the article existed for.
    raw_used = [p.views for p in monthly_raw[-months_used:]] if months_used else []
    desp_used = [p.views for p in monthly_desp[-months_used:]] if months_used else []

    total = daily.total
    out: dict = {
        "window": {"start": window.start.isoformat(), "end": window.end.isoformat(), "months": n_months},
        "months_used": months_used,
        "partial_history": partial_history,
        "first_revision": first_revision.isoformat() if first_revision else None,
        "volume": {
            "total_views": total,
            "median_monthly": median(raw_used) if raw_used else 0.0,
            "median_monthly_despiked": median(desp_used) if desp_used else 0.0,
            "mean_daily": mean(daily.views) if daily.views else 0.0,
            "present_days": daily.present_days,
            "zero_days": sum(1 for v in daily.views if v == 0),
            "days": len(daily),
            "peak": _extreme(monthly_raw[-months_used:], max) if months_used else None,
            "trough": _extreme(monthly_raw[-months_used:], min) if months_used else None,
        },
        "spikes": _spike_summary(spikes, daily, total),
        "growth": {},
        "trend_test": None,
        "volatility": {
            # std of log month-over-month ratios on the despiked series: how jumpy the
            # series is *around* its trend. A smooth halving scores low; school-year
            # swings score high. (Plain CV confused a strong trend with volatility.)
            "mom_log_std": stats.log_change_std(desp_used) if len(desp_used) >= 3 else None,
            "cv_monthly_despiked": stats.coefficient_of_variation(desp_used) if desp_used else None,
        },
        "shape": _shape(monthly_raw[-months_used:]) if months_used >= 2 else None,
        "share_of_project": None,
        "verdict": "insufficient_data",
        "magnitude": None,
        "monthly": [
            {"month": r.label, "views": int(r.views), "despiked": round(d.views, 1)}
            for r, d in zip(monthly_raw, monthly_desp)
        ],
    }

    if months_used >= MIN_MONTHS_FOR_TREND:
        out["growth"], out["trend_test"] = _growth_and_test(raw_used, desp_used)
        out["verdict"] = _verdict(out["growth"]["annualized_trend"], out["trend_test"]["p_value"])
        out["magnitude"] = _magnitude(out["growth"]["annualized_trend"])

    if project_monthly:
        out["share_of_project"] = _share_of_project(monthly_raw, project_monthly, months_used, out["monthly"])

    return out


MIN_MONTHS_FOR_SEASONAL = 18  # >= 6 same-month pairs and >= 7 trailing-12m points


def _shape(points: Sequence[MonthPoint]) -> dict:
    """Largest single month-to-month rise and drop: lets the model say *when*
    a level shift happened ("halved between 2024-10 and 2024-11") without
    seeing the series."""
    best_up = best_down = None
    for a, b in zip(points, points[1:]):
        if a.views <= 0 or b.views <= 0:
            continue
        r = b.views / a.views - 1
        if best_up is None or r > best_up[2]:
            best_up = (a.label, b.label, r)
        if best_down is None or r < best_down[2]:
            best_down = (a.label, b.label, r)
    fmt = lambda t: {"from": t[0], "to": t[1], "change": round(t[2], 3)} if t else None  # noqa: E731
    return {"largest_monthly_rise": fmt(best_up), "largest_monthly_drop": fmt(best_down)}


def _extreme(points: Sequence[MonthPoint], pick) -> dict:
    p = pick(points, key=lambda mp: mp.views)
    return {"month": p.label, "views": int(p.views)}


def series_trend(raw: Sequence[float], despiked: Sequence[float] | None = None) -> dict:
    """Verdict + magnitude + growth + test for any monthly series with >= 6 points.
    Used for the share-of-edition (relative) verdict so absolute and relative
    calls are made with identical rules."""
    if len(raw) < MIN_MONTHS_FOR_TREND:
        return {"verdict": "insufficient_data", "magnitude": None, "growth": {}, "trend_test": None}
    desp = list(despiked) if despiked is not None else list(raw)
    growth, test = _growth_and_test(list(raw), desp)
    return {
        "verdict": _verdict(growth["annualized_trend"], test["p_value"]),
        "magnitude": _magnitude(growth["annualized_trend"]),
        "growth": growth,
        "trend_test": test,
    }


def _growth_and_test(raw: Sequence[float], desp: Sequence[float]) -> tuple[dict, dict]:
    """Seasonality-controlled estimates when the window is long enough.

    Growth: Theil–Sen slope on log(trailing-12-month totals) of the despiked
    series. Significance: seasonal Mann–Kendall (same month vs same month).
    Below 18 months neither is possible, so we fall back to the plain versions
    and say so; trust.py penalises that flag.
    """
    seasonal = len(desp) >= MIN_MONTHS_FOR_SEASONAL
    if seasonal:
        ann_desp, ts = stats.log_trend_annualized(stats.trailing_sums(desp))
        ann_raw, _ = stats.log_trend_annualized(stats.trailing_sums(raw))
        smk = stats.seasonal_mann_kendall(desp)
        test = {
            "method": "seasonal_mann_kendall",
            **asdict(smk),
            "direction": "increasing" if smk.s > 0 else "decreasing" if smk.s < 0 else "none",
        }
    else:
        ann_desp, ts = stats.log_trend_annualized(desp)
        ann_raw, _ = stats.log_trend_annualized(raw)
        mk = stats.mann_kendall(desp)
        test = {"method": "mann_kendall", **asdict(mk), "direction": mk.direction}
    growth = {
        "seasonality_controlled": seasonal,
        "yoy": stats.year_over_year(raw),
        "yoy_despiked": stats.year_over_year(desp),
        "annualized_trend": ann_desp,
        "annualized_trend_raw": ann_raw,
        "monthly_log_slope": ts.slope,
        "recent_3m_vs_same_3m_last_year": _recent_vs_last_year(raw, 3),
    }
    return growth, test


def _recent_vs_last_year(monthly: Sequence[float], k: int) -> float | None:
    if len(monthly) < 12 + k:
        return None
    return stats.ratio_change(monthly[-k:], monthly[-12 - k : -12])


def _verdict(annualized: float, p_value: float, flat_band: float = 0.10, alpha: float = 0.10) -> str:
    """Plain-language trend call. Direction comes from the trend test (is the
    change consistent?), magnitude is reported separately by `_magnitude`.
    alpha=0.10 is lenient on purpose: with 24 points the test has modest power
    and a founder wants 'probably growing', not a publication standard. A
    large estimated change that the test cannot confirm is 'unclear', which is
    the honest answer for spiky or erratic series."""
    if p_value < alpha and annualized != 0:
        return "growing" if annualized > 0 else "declining"
    if abs(annualized) < flat_band:
        return "flat"
    return "unclear"


def _magnitude(annualized: float) -> str:
    a = abs(annualized)
    return "small" if a < 0.10 else "moderate" if a < 0.30 else "large"


def _spike_summary(spikes: stats.SpikeReport, daily: DailySeries, total: int) -> dict:
    top = sorted(zip(spikes.spike_indices, spikes.excess), key=lambda t: -t[1])[:5]
    return {
        "count": len(spikes.spike_indices),
        "excess_views": round(spikes.excess_total),
        "share_of_total": (spikes.excess_total / total) if total else 0.0,
        "top": [
            {"date": (daily.start + timedelta(days=i)).isoformat(), "views": daily.views[i], "excess": round(e)}
            for i, e in top
        ],
    }


def _share_of_project(
    monthly_raw: Sequence[MonthPoint], project_monthly: Sequence[MonthPoint], months_used: int, monthly_rows: list[dict]
) -> dict | None:
    proj = {(p.year, p.month): p.views for p in project_monthly}
    per_million = []
    for row, mp in zip(monthly_rows, monthly_raw):
        denom = proj.get((mp.year, mp.month))
        pm = (mp.views / denom * 1_000_000) if denom else None
        row["per_million"] = round(pm, 2) if pm is not None else None
        per_million.append(pm)
    used = [v for v in per_million[-months_used:] if v is not None] if months_used else []
    if len(used) < MIN_MONTHS_FOR_TREND:
        return None
    ann, _ = stats.log_trend_annualized(used)
    half = len(used) // 2
    return {
        "unit": "article views per million project views (agent=user)",
        "median_per_million": median(used),
        "first_half_mean": mean(used[:half]),
        "second_half_mean": mean(used[half:]),
        "annualized_trend": ann,
        "yoy": stats.year_over_year(used),
    }
