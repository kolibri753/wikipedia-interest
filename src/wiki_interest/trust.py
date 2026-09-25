"""How much should a founder trust the trend we just computed?

This is a rule-based score, not a statistical model. The point is that every
deduction comes with a sentence a non-statistician can act on ("40% of the
views came from 3 news days"). The weights are heuristics chosen so that the
synthetic golden cases in tests/test_trust.py land where a careful analyst
would put them; they are documented in references/methodology.md and are
expected to be tuned as we see more real series.

Score bands: >= 70 high, >= 40 medium, else low.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict

VOLUME_VERY_LOW = 500  # median monthly views
VOLUME_LOW = 3000
SPIKE_SHARE_HIGH = 0.30
SPIKE_SHARE_MODERATE = 0.10
VOLATILITY_HIGH = 0.35  # std of log month-over-month change on the despiked series


@dataclass
class Trust:
    score: int
    label: str
    reasons: list[str] = field(default_factory=list)  # deductions, most important first
    strengths: list[str] = field(default_factory=list)  # what supports the trend
    notes: list[str] = field(default_factory=list)  # context that changes the reading but not the score

    def as_dict(self) -> dict:
        return asdict(self)


def assess_trust(m: dict) -> Trust:
    score = 100
    reasons: list[str] = []
    strengths: list[str] = []
    notes: list[str] = []

    if m["verdict"] == "insufficient_data":
        return Trust(
            0,
            "low",
            [f"Only {m['months_used']} complete months of data; at least 6 are needed for any trend statement."],
        )

    vol = m["volume"]
    med = vol["median_monthly_despiked"]
    very_low_volume = med < VOLUME_VERY_LOW
    if very_low_volume:
        score -= 35
        reasons.append(
            f"Very low volume: median ~{med:,.0f} views/month with spike days removed. Percent changes on numbers this small are mostly noise."
        )
    elif med < VOLUME_LOW:
        score -= 15
        reasons.append(
            f"Low volume: median ~{med:,.0f} views/month with spike days removed; treat percentage changes as indicative only."
        )
    else:
        strengths.append(f"Healthy volume (median ~{med:,.0f} views/month).")

    sp = m["spikes"]
    share = sp["share_of_total"]
    if share >= SPIKE_SHARE_HIGH:
        score -= 30
        reasons.append(
            f"Event-driven traffic: {share:.0%} of all views came from {sp['count']} spike day(s) (largest: {_top(sp)})."
        )
    elif share >= SPIKE_SHARE_MODERATE:
        score -= 15
        reasons.append(
            f"Noticeable spikes: {share:.0%} of views came from {sp['count']} spike day(s) (largest: {_top(sp)})."
        )
    else:
        strengths.append("Traffic is not dominated by one-off spikes.")

    g = m["growth"]
    desp = g["annualized_trend"]
    # Theil–Sen barely moves for a few spike days (that is the point of it), so
    # the raw-vs-despiked comparison has to use year-over-year totals, which do.
    yoy_raw, yoy_desp = g.get("yoy"), g.get("yoy_despiked")
    if yoy_raw is not None and yoy_desp is not None and yoy_raw > 0.20 and yoy_desp < 0.05:
        score -= 20
        reasons.append(
            f"Most of the year-over-year growth comes from spike days ({yoy_raw:+.0%} raw vs {yoy_desp:+.0%} with spikes removed)."
        )

    tt = m["trend_test"]
    test_name = "seasonal Mann–Kendall" if tt["method"] == "seasonal_mann_kendall" else "Mann–Kendall"
    if m["verdict"] == "flat":
        # A non-significant test *supports* a flat call; nothing to deduct.
        strengths.append(f"No meaningful trend either way ({test_name} p={tt['p_value']:.2f}).")
    elif tt["p_value"] >= 0.10:
        score -= 20
        reasons.append(f"The direction is not distinguishable from noise ({test_name} p={tt['p_value']:.2f}).")
    elif tt["p_value"] >= 0.05:
        score -= 10
        reasons.append(f"The direction is only weakly significant ({test_name} p={tt['p_value']:.2f}).")
    else:
        detail = (
            f"{tt['months_up']} of {tt['pairs']} months were up on the same month a year earlier"
            if tt["method"] == "seasonal_mann_kendall"
            else f"{test_name} p={tt['p_value']:.3f}"
        )
        strengths.append(f"The direction is statistically clear ({detail}).")

    if not g.get("seasonality_controlled", False):
        score -= 15
        reasons.append("Fewer than 18 months of data, so seasonal swings could not be separated from the trend.")

    yoy = g.get("yoy_despiked")
    if yoy is not None and desp != 0 and (yoy > 0) != (desp > 0) and abs(yoy) > 0.05:
        score -= 15
        reasons.append(
            f"Year-over-year change ({yoy:+.0%}) and the long-run slope ({desp:+.0%}/yr) point in different directions."
        )

    if m["partial_history"]:
        score -= 15
        reasons.append(
            f"The article only existed for {m['months_used']} of the {m['window']['months']} months (created {m['first_revision']}); early growth partly reflects the page being new."
        )

    vol = m["volatility"].get("mom_log_std")
    if vol is not None and vol > VOLATILITY_HIGH:
        score -= 10
        reasons.append(
            f"Month-to-month swings are large even after removing spike days (typical monthly change ±{(math.exp(vol) - 1):.0%}), "
            f"so any single month is a poor guide."
        )

    shape = m.get("shape") or {}
    drop, rise = shape.get("largest_monthly_drop"), shape.get("largest_monthly_rise")
    if drop and drop["change"] <= -0.35:
        notes.append(
            f"Largest single-month drop: {drop['change']:+.0%} from {drop['from']} to {drop['to']} — a level shift rather than a gradual trend; worth checking what changed then."
        )
    if rise and rise["change"] >= 0.50:
        notes.append(f"Largest single-month rise: {rise['change']:+.0%} from {rise['from']} to {rise['to']}.")

    score = max(0, min(100, score))
    if very_low_volume:
        score = min(score, 55)  # tiny series never earn a 'high' label
    label = "high" if score >= 70 else "medium" if score >= 40 else "low"
    return Trust(score, label, reasons, strengths, notes)


def _top(sp: dict) -> str:
    if not sp["top"]:
        return "n/a"
    t = sp["top"][0]
    return f"{t['date']} with {t['views']:,} views"
