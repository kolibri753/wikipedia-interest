"""Robust statistics for short, noisy time series. Standard library only.

Why these particular tools:
- Pageview series are dominated by a few event-driven spikes (news, TV, Google
  Doodles, bot bursts). Ordinary least squares and the mean are pulled around
  by those days; medians and rank-based tests are not.
- With ~24 monthly points we cannot afford methods that need long histories.

Everything here is a pure function on plain Python lists so it can be unit
tested against hand calculations and against SciPy without SciPy being a
runtime dependency of the skill.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from statistics import median, mean, pstdev
from typing import Sequence


# --------------------------------------------------------------------------- #
# Central tendency / dispersion
# --------------------------------------------------------------------------- #
def mad(values: Sequence[float]) -> float:
    """Median absolute deviation (unscaled)."""
    if not values:
        raise ValueError("mad() of empty sequence")
    m = median(values)
    return median(abs(v - m) for v in values)


MAD_TO_SIGMA = 1.4826  # scales MAD to the std-dev of a normal distribution


def coefficient_of_variation(values: Sequence[float]) -> float | None:
    """Population std-dev / mean. None when the mean is zero."""
    if not values:
        return None
    mu = mean(values)
    if mu == 0:
        return None
    return pstdev(values) / mu


def log_change_std(values: Sequence[float]) -> float | None:
    """Population std-dev of log(v[t]/v[t-1]) over consecutive positive values.
    Trend-free measure of month-to-month jumpiness: a smooth 5%/month decline
    contributes a constant, hence ~0; alternating +50%/-33% contributes a lot."""
    ratios = [math.log(b / a) for a, b in zip(values, values[1:]) if a > 0 and b > 0]
    if len(ratios) < 2:
        return None
    return pstdev(ratios)


# --------------------------------------------------------------------------- #
# Theil–Sen robust slope
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class TheilSen:
    slope: float
    intercept: float


def theil_sen(y: Sequence[float], x: Sequence[float] | None = None) -> TheilSen:
    """Median of all pairwise slopes; intercept = median(y - slope*x).

    O(n^2) pairs is fine for n <= a few hundred (24 months -> 276 pairs).
    """
    n = len(y)
    if n < 2:
        raise ValueError("theil_sen() needs at least 2 points")
    xs = list(x) if x is not None else list(range(n))
    if len(xs) != n:
        raise ValueError("x and y must have equal length")
    slopes = []
    for i in range(n - 1):
        for j in range(i + 1, n):
            dx = xs[j] - xs[i]
            if dx != 0:
                slopes.append((y[j] - y[i]) / dx)
    if not slopes:
        raise ValueError("all x values identical")
    slope = median(slopes)
    intercept = median(yi - slope * xi for xi, yi in zip(xs, y))
    return TheilSen(slope=slope, intercept=intercept)


# --------------------------------------------------------------------------- #
# Mann–Kendall monotonic trend test
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MannKendall:
    s: int  # sum of sign(y_j - y_i) over i<j
    tau: float  # s / (n(n-1)/2)
    z: float  # normal approximation with continuity correction
    p_value: float  # two-sided
    n: int

    @property
    def direction(self) -> str:
        return "increasing" if self.s > 0 else "decreasing" if self.s < 0 else "none"


def mann_kendall(y: Sequence[float]) -> MannKendall:
    """Two-sided Mann–Kendall test (normal approximation, tie-corrected variance,
    continuity correction). Assumes independent observations; monthly pageview
    totals are only mildly autocorrelated, and with 24 points the approximation
    is the standard choice (it is what pymannkendall's `original_test` does).
    """
    n = len(y)
    if n < 4:
        raise ValueError("mann_kendall() needs at least 4 points")
    s = 0
    for i in range(n - 1):
        yi = y[i]
        for j in range(i + 1, n):
            d = y[j] - yi
            s += (d > 0) - (d < 0)
    ties = Counter(y)
    tie_term = sum(t * (t - 1) * (2 * t + 5) for t in ties.values() if t > 1)
    var_s = (n * (n - 1) * (2 * n + 5) - tie_term) / 18.0
    if var_s <= 0:  # all values identical
        return MannKendall(s=0, tau=0.0, z=0.0, p_value=1.0, n=n)
    if s > 0:
        z = (s - 1) / math.sqrt(var_s)
    elif s < 0:
        z = (s + 1) / math.sqrt(var_s)
    else:
        z = 0.0
    p = math.erfc(abs(z) / math.sqrt(2))  # two-sided
    tau = s / (n * (n - 1) / 2)
    return MannKendall(s=s, tau=tau, z=z, p_value=p, n=n)


# --------------------------------------------------------------------------- #
# Spike detection (robust z-score against a rolling median baseline)
# --------------------------------------------------------------------------- #
def rolling_median(y: Sequence[float], window: int) -> list[float]:
    """Centered rolling median; the window shrinks near the edges."""
    if window < 1:
        raise ValueError("window must be >= 1")
    n = len(y)
    half = window // 2
    out = []
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out.append(median(y[lo:hi]))
    return out


@dataclass(frozen=True)
class SpikeReport:
    baseline: list[float]  # rolling-median baseline, same length as input
    spike_indices: list[int]  # positions flagged as spikes
    excess: list[float]  # excess views on spike days (y - baseline), aligned with spike_indices
    despiked: list[float]  # input with spike days replaced by baseline
    threshold: float | None  # residual threshold used (None = no scale, nothing flagged)

    @property
    def excess_total(self) -> float:
        return sum(self.excess)


def detect_spikes(
    y: Sequence[float],
    window: int = 29,
    k: float = 5.0,
    min_relative: float = 1.0,
    min_absolute: float = 20.0,
) -> SpikeReport:
    """Flag days whose excess over the rolling-median baseline is extreme.

    A day is a spike when residual > k * (1.4826 * MAD of residuals) AND the
    residual is at least `min_relative` times the local baseline AND at least
    `min_absolute` views. The last two guards stop tiny articles (5 views/day)
    from producing 'spikes' of 12 views, and stop a huge, steady article's
    ordinary Monday bump from being flagged. The constants are heuristics and
    are documented in references/methodology.md.
    """
    n = len(y)
    if n == 0:
        return SpikeReport([], [], [], [], None)
    base = rolling_median(y, window)
    resid = [yi - bi for yi, bi in zip(y, base)]
    scale = MAD_TO_SIGMA * mad(resid)
    if scale == 0:
        # Series is almost flat: fall back to the absolute/relative guards only.
        threshold = None
    else:
        threshold = k * scale
    idx, excess, despiked = [], [], list(y)
    for i, r in enumerate(resid):
        over_scale = threshold is not None and r > threshold
        over_guards = r >= min_absolute and r >= min_relative * max(base[i], 0.0)
        if (over_scale or threshold is None) and over_guards:
            idx.append(i)
            excess.append(r)
            despiked[i] = base[i]
    return SpikeReport(baseline=base, spike_indices=idx, excess=excess, despiked=despiked, threshold=threshold)


# --------------------------------------------------------------------------- #
# Growth measures
# --------------------------------------------------------------------------- #
def ratio_change(recent: Sequence[float], earlier: Sequence[float]) -> float | None:
    """(sum(recent) / sum(earlier)) - 1, or None if earlier sums to zero."""
    e = sum(earlier)
    if e <= 0:
        return None
    return sum(recent) / e - 1.0


def year_over_year(monthly: Sequence[float]) -> float | None:
    """Last 12 months vs the 12 before. Needs >= 24 points; uses the last 24."""
    if len(monthly) < 24:
        return None
    tail = list(monthly)[-24:]
    return ratio_change(tail[12:], tail[:12])


def trailing_sums(monthly: Sequence[float], k: int = 12) -> list[float]:
    """Rolling k-month totals. With k=12 every point covers a full year, so the
    seasonal cycle cancels out and what remains is the underlying level.
    24 monthly points -> 13 trailing-12-month points."""
    if len(monthly) < k:
        return []
    return [sum(monthly[i - k + 1 : i + 1]) for i in range(k - 1, len(monthly))]


@dataclass(frozen=True)
class SeasonalMK:
    pairs: int  # number of same-month comparisons (12 for a 24-month window)
    s: int  # sum of sign(later - earlier)
    z: float
    p_value: float
    months_up: int
    months_down: int


def seasonal_mann_kendall(monthly: Sequence[float], period: int = 12) -> SeasonalMK:
    """Hirsch–Slack seasonal Mann–Kendall specialised to the case we have:
    each month compared only with the same month in other years. With exactly
    two years this is a sign test over 12 pairs, which is the most a 24-month
    window can honestly support. var(S) = sum over seasons of
    n_g(n_g-1)(2n_g+5)/18 with n_g = 2 -> 1 per season.
    """
    n = len(monthly)
    if n < period + 1:
        raise ValueError("seasonal_mann_kendall() needs more than one period of data")
    s = up = down = 0
    pairs = 0
    var_s = 0.0
    for g in range(period):
        vals = monthly[g::period]
        ng = len(vals)
        if ng < 2:
            continue
        for i in range(ng - 1):
            for j in range(i + 1, ng):
                d = vals[j] - vals[i]
                sg = (d > 0) - (d < 0)
                s += sg
                up += d > 0
                down += d < 0
                pairs += 1
        var_s += ng * (ng - 1) * (2 * ng + 5) / 18.0
    if var_s == 0:
        return SeasonalMK(pairs, 0, 0.0, 1.0, up, down)
    z = (s - 1) / math.sqrt(var_s) if s > 0 else (s + 1) / math.sqrt(var_s) if s < 0 else 0.0
    p = math.erfc(abs(z) / math.sqrt(2))
    return SeasonalMK(pairs=pairs, s=s, z=z, p_value=p, months_up=up, months_down=down)


def log_trend_annualized(monthly: Sequence[float]) -> tuple[float, TheilSen]:
    """Theil–Sen slope on log(views + 1) per month, expressed as annualized
    multiplicative change: exp(12 * slope) - 1.

    The log makes growth multiplicative (a 10%/month rise looks the same at
    1k and 100k views) and tames residual spikes. The +1 keeps zero months
    finite; it biases very small series, which is why low volume is a separate
    trust penalty rather than something we try to model here.
    """
    logs = [math.log(v + 1.0) for v in monthly]
    ts = theil_sen(logs)
    return math.exp(12.0 * ts.slope) - 1.0, ts
