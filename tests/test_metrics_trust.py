"""Golden cases for the *judgment* layer.

These are ML-style evaluations rather than unit tests: we generate series whose
true behaviour we know and assert that the verdict and trust land where a
careful analyst would put them. They are also the regression net for any
future tuning of the heuristics in trust.py.
"""

import math
import random
from datetime import date

import pytest

from wiki_interest.metrics import compute_metrics
from wiki_interest.series import DailySeries, MonthPoint, analysis_window
from wiki_interest.trust import assess_trust

WIN = analysis_window(24, date(2026, 9, 22))
DAYS = (WIN.end - WIN.start).days + 1


def weekly(i):
    return 1.0 - 0.15 * ((i % 7) >= 5)


def make(fn, seed=1, noise=0.25):
    rnd = random.Random(seed)
    return DailySeries(WIN.start, [max(0, int(fn(i) * (1 + rnd.gauss(0, noise)))) for i in range(DAYS)], DAYS)


def run(fn, **kw):
    m = compute_metrics(
        make(fn, **{k: v for k, v in kw.items() if k in ("seed", "noise")}),
        WIN,
        **{k: v for k, v in kw.items() if k not in ("seed", "noise")},
    )
    return m, assess_trust(m)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_steady_doubling_is_growing_and_trusted(seed):
    m, t = run(lambda i: 800 * 2 ** (i / 730) * weekly(i), seed=seed)
    assert m["verdict"] == "growing" and m["magnitude"] in ("moderate", "large")
    assert m["trend_test"]["months_up"] == 12
    assert t.label == "high"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_pure_seasonality_is_flat_not_declining(seed):
    """Regression for the bug found in calibration: plain Theil–Sen/MK on 24
    monthly points called a zero-trend sinusoid 'declining' with trust 100."""
    m, t = run(lambda i: 800 * (1 + 0.5 * math.sin(2 * math.pi * i / 365)) * weekly(i), seed=seed)
    assert m["verdict"] == "flat"
    assert abs(m["growth"]["annualized_trend"]) < 0.10
    assert m["growth"]["seasonality_controlled"] is True


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_growth_under_seasonality_is_detected(seed):
    m, t = run(lambda i: 800 * 2 ** (i / 730) * (1 + 0.4 * math.sin(2 * math.pi * i / 365)) * weekly(i), seed=seed)
    assert m["verdict"] == "growing" and t.label == "high"


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_spike_driven_yoy_is_not_called_growth(seed):
    m, t = run(lambda i: 800 * weekly(i) + (40000 if i in (500, 501, 502) else 0), seed=seed)
    assert m["growth"]["yoy"] > 0.25  # the naive number looks great
    assert m["verdict"] == "flat"  # the underlying series is not
    assert m["spikes"]["count"] == 3 and m["spikes"]["share_of_total"] > 0.10
    assert t.label != "high"
    assert any("spike" in r.lower() for r in t.reasons)


def test_decline_is_declining():
    m, t = run(lambda i: 800 * 0.6 ** (i / 730) * weekly(i))
    assert m["verdict"] == "declining" and m["trend_test"]["months_down"] == 12


def test_tiny_article_never_gets_high_trust():
    m, t = run(lambda i: 8 * 2 ** (i / 730))
    assert m["verdict"] == "growing"
    assert t.label != "high" and any("volume" in r.lower() for r in t.reasons)


def test_new_article_uses_only_months_after_creation():
    m, t = run(lambda i: 800 * 2 ** (i / 730) * weekly(i), first_revision=date(2025, 5, 15))
    assert m["months_used"] == 15 and m["partial_history"] is True
    assert m["growth"]["seasonality_controlled"] is False
    assert any("existed" in r for r in t.reasons) and any("18 months" in r for r in t.reasons)


def test_too_new_article_is_insufficient():
    m, t = run(lambda i: 800.0, first_revision=date(2026, 5, 1))
    assert m["verdict"] == "insufficient_data" and t.score == 0


def test_share_of_project_normalisation():
    daily = make(lambda i: 1000.0, noise=0.0)
    # project traffic halves over the window -> article share doubles
    proj = [MonthPoint(y, m, 20_000_000 if (y, m) < (2025, 9) else 10_000_000) for (y, m) in WIN.months]
    m = compute_metrics(daily, WIN, project_monthly=proj)
    s = m["share_of_project"]
    assert s["second_half_mean"] == pytest.approx(2 * s["first_half_mean"], rel=0.02)
    assert m["monthly"][0]["per_million"] == pytest.approx(1000 * 30 / 20, rel=0.05)


def test_metrics_are_json_serialisable():
    import json

    m, t = run(lambda i: 800.0)
    json.dumps(m)
    json.dumps(t.as_dict())


# ---------------------------------------------------------------- phase-4 additions
def test_spike_on_first_day_is_caught_when_series_is_padded():
    """A short spike at the window start is caught even without history (the rolling
    window shrinks). A *longer* elevation at the start — a 10-day school-start week
    when the window opens on 1 Sept — is only caught when the baseline has the
    preceding month to compare against. The 31-day pad gives it that."""
    from datetime import timedelta

    pad = 31
    rnd = random.Random(4)
    ext_start = WIN.start - timedelta(days=pad)
    n = DAYS + pad
    views = [max(0, int(100 * (1 + rnd.gauss(0, 0.1)))) for _ in range(n)]
    for i in range(pad, pad + 10):  # first ten days of the window spike 8x
        views[i] = 800
    padded = DailySeries(ext_start, views, n)
    unpadded = DailySeries(WIN.start, views[pad:], DAYS)
    m_pad = compute_metrics(padded, WIN)
    m_raw = compute_metrics(unpadded, WIN)
    assert m_pad["spikes"]["count"] == 10 and m_pad["spikes"]["top"][0]["date"].startswith("2024-09-")
    assert 0 < m_raw["spikes"]["count"] < 10  # without history only the tail of the elevation is caught
    assert len(m_pad["monthly"]) == 24 and m_pad["volume"]["days"] == DAYS  # pad is trimmed away


def test_volatility_is_trend_free():
    from wiki_interest.stats import log_change_std

    smooth_halving = [1000 * 0.97**i for i in range(24)]  # -3%/month, perfectly smooth
    jumpy = [1000 * (1.6 if i % 2 else 0.6) for i in range(24)]
    assert log_change_std(smooth_halving) < 1e-9
    assert log_change_std(jumpy) > 0.4
    m, t = run(lambda i: 800 * 0.5 ** (i / 730) * weekly(i), noise=0.10)  # smooth decline
    assert not any("swings" in r for r in t.reasons)


def test_shape_reports_largest_level_shift():
    m, t = run(lambda i: 2000.0 if i < 400 else 900.0, noise=0.05)
    drop = m["shape"]["largest_monthly_drop"]
    assert drop["from"] == "2025-09" and drop["to"] == "2025-10" and drop["change"] < -0.4
    assert any("level shift" in n for n in t.notes)
