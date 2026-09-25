"""Statistics tests.

Two layers:
1. Hand-computed values (always run). These pin down the exact definitions.
2. Cross-checks against SciPy as an independent oracle (run only if SciPy
   happens to be installed; it is *not* a dependency of the skill).
"""

import math
import random

import pytest

from wiki_interest import stats

try:  # optional oracle
    from scipy import stats as sp
except Exception:  # pragma: no cover
    sp = None


# ------------------------------------------------------------------ hand values
def test_mad_hand():
    assert stats.mad([1, 2, 3, 4, 100]) == 1


def test_theil_sen_hand():
    # pairwise slopes of [1,3,2,5]: 2, .5, 1.333, -1, 1, 3 -> median = (1 + 1.333)/2
    ts = stats.theil_sen([1, 3, 2, 5])
    assert ts.slope == pytest.approx(7 / 6)


def test_theil_sen_exact_line_and_outlier():
    y = [2 * i + 1 for i in range(20)]
    y[10] = 1000  # one wild point should not move a robust slope
    assert stats.theil_sen(y).slope == pytest.approx(2.0)


def test_mann_kendall_hand_monotone():
    r = stats.mann_kendall([1, 2, 3, 4, 5])
    assert r.s == 10
    # var = 5*4*15/18 = 16.667 ; z = (10-1)/sqrt(16.667)
    assert r.z == pytest.approx(9 / math.sqrt(16.6667), rel=1e-4)
    assert r.p_value == pytest.approx(0.0275, abs=1e-3)
    assert r.direction == "increasing"


def test_mann_kendall_ties_and_constant():
    r = stats.mann_kendall([1, 1, 2, 2, 3])
    assert r.s == 8
    c = stats.mann_kendall([5, 5, 5, 5])
    assert c.p_value == 1.0 and c.direction == "none"


def test_seasonal_mk_two_years_is_sign_test():
    up = [100] * 12 + [110] * 12  # every month up on last year
    r = stats.seasonal_mann_kendall(up)
    assert (r.pairs, r.s, r.months_up, r.months_down) == (12, 12, 12, 0)
    assert r.z == pytest.approx(11 / math.sqrt(12))
    assert r.p_value < 0.01
    mixed = [100] * 12 + [110, 90] * 6  # 6 up, 6 down
    assert stats.seasonal_mann_kendall(mixed).s == 0


def test_trailing_sums():
    assert stats.trailing_sums([1, 2, 3, 4], k=2) == [3, 5, 7]
    assert stats.trailing_sums([1, 2], k=3) == []
    m = list(range(24))
    assert len(stats.trailing_sums(m)) == 13 and stats.trailing_sums(m)[0] == sum(range(12))


def test_rolling_median_edges():
    assert stats.rolling_median([1, 2, 3, 4, 5], 3) == [1.5, 2, 3, 4, 4.5]


def test_detect_spikes_flags_only_real_spikes():
    random.seed(0)
    y = [100 + random.gauss(0, 5) for _ in range(200)]
    y[120] = 1000
    r = stats.detect_spikes(y)
    assert r.spike_indices == [120]
    assert r.excess_total == pytest.approx(900, abs=30)
    assert abs(r.despiked[120] - 100) < 15


def test_detect_spikes_ignores_tiny_series_wobble():
    random.seed(0)
    y = [5 + random.choice([-1, 0, 1]) for _ in range(100)]
    y[50] = 12  # 'doubling' of a 5-view page is not a story
    assert stats.detect_spikes(y).spike_indices == []


def test_growth_helpers():
    monthly = [1000 * 1.1**i for i in range(24)]
    ann, _ = stats.log_trend_annualized(monthly)
    assert ann == pytest.approx(1.1**12 - 1, rel=1e-3)
    assert stats.year_over_year([100] * 24) == 0.0
    assert stats.year_over_year([100] * 12 + [200] * 12) == 1.0
    assert stats.year_over_year([100] * 23) is None
    assert stats.ratio_change([1], [0]) is None


# ------------------------------------------------------------------- scipy oracle
@pytest.mark.skipif(sp is None, reason="scipy not installed (optional oracle)")
def test_theil_sen_matches_scipy_joint():
    random.seed(7)
    for _ in range(20):
        n = random.randint(5, 40)
        y = [0.3 * i + random.gauss(0, 2) for i in range(n)]
        ours = stats.theil_sen(y)
        ref = sp.theilslopes(y, list(range(n)), method="joint")
        assert ours.slope == pytest.approx(ref.slope)
        assert ours.intercept == pytest.approx(ref.intercept)


@pytest.mark.skipif(sp is None, reason="scipy not installed (optional oracle)")
def test_mann_kendall_matches_scipy_s_and_tau():
    random.seed(7)
    for _ in range(20):
        n = random.randint(20, 40)
        y = [random.gauss(0.05 * i, 1) for i in range(n)]
        ours = stats.mann_kendall(y)
        tau, p = sp.kendalltau(list(range(n)), y, method="asymptotic")
        assert ours.s == round(tau * n * (n - 1) / 2)
        assert ours.tau == pytest.approx(tau)
        # Ours applies the continuity correction, scipy does not: small gap only.
        assert abs(ours.p_value - p) < 0.03
