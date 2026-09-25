# Methodology

Read this when the user asks how a number was computed or whether a threshold
makes sense. Everything here is implemented in `src/wiki_interest/` and covered
by tests; nothing is estimated by the model.

## Data

- Source: Wikimedia Analytics API, pageviews `per-article` (daily) and
  `aggregate` (monthly, whole edition). Licence CC0. No authentication.
- Default filter: `agent=user` (traffic Wikimedia classifies as human),
  `access=all-access` (desktop + mobile web + apps).
- Window: the last *N complete* UTC months (default 24). The current month is
  never included. Multiples of 12 mean the window starts and ends at the same
  point of the seasonal cycle.
- Redirects: pageviews are keyed by title; views of redirect titles ("5:2 diet"
  → "Intermittent fasting") are counted separately by Wikimedia. We sum them
  by default (up to 50 redirects) and report `redirects.share`. This also
  absorbs page moves inside the window, because the old title becomes a
  redirect. Live measurement: 3.2% of English intermittent-fasting traffic
  arrived via redirects, ~0% for the Czech and Ukrainian examples.
- Days with zero views are absent from the API response and are zero-filled.
- Article creation date = timestamp of the first revision. Months before the
  first complete month after creation are excluded from all trend statistics
  (`months_used`, `partial_history`).

## Cleaning: spike detection

Daily series → rolling median (29-day centred window) → residuals. A day is a
**spike** when its residual exceeds 5 × (1.4826 × MAD of residuals) *and* is at
least 20 views *and* at least 100% of the local baseline. The guards stop a
5-views/day article from producing "spikes" of 12 views and stop a large
article's ordinary weekday bump from being flagged. Spike days are replaced by
the baseline to form the **despiked** series. `spikes.share_of_total` is the
excess views on spike days divided by all views.

A month-long elevation (e.g. every January for diet topics) is *not* a spike;
it is seasonality and is handled below.

## Growth

| Metric | Definition |
|---|---|
| `growth.yoy` | sum of last 12 months ÷ sum of previous 12 − 1 (raw) |
| `growth.yoy_despiked` | same on the despiked series |
| `growth.annualized_trend` | Theil–Sen slope of log(trailing-12-month totals) of the despiked monthly series, expressed as exp(12·slope) − 1. Trailing-12 sums cancel the seasonal cycle; Theil–Sen (median of pairwise slopes) is robust to remaining outliers; the log makes growth multiplicative. |
| `growth.recent_3m_vs_same_3m_last_year` | last 3 months ÷ same 3 months a year earlier − 1 |
| `relative.*` | the same metrics on views per million edition views (article ÷ edition aggregate × 10⁶) |
| `agent_mix.*` | `user` vs `all-agents` for the article and the edition; user share = user ÷ all-agents |

With fewer than 18 months (`seasonality_controlled: false`) the plain Theil–Sen
slope on log(monthly) and the plain Mann–Kendall test are used instead.

## Significance: seasonal Mann–Kendall

Each month is compared only with the same month in the other year(s). With two
years this is a sign test on 12 pairs: S = Σ sign(later − earlier), Var(S) = 12,
z = (S − 1)/√12 with continuity correction, two-sided p from the normal
approximation. Reported as `months_up` / `months_down` of `pairs` and
`p_value`. Assumes independence between years; monthly totals are only mildly
autocorrelated at that lag.

Verified against hand calculations and, where SciPy is installed, against
`scipy.stats.theilslopes` (slope exact, intercept exact with `method="joint"`)
and `scipy.stats.kendalltau` (S and τ exact; p differs only by the continuity
correction, < 0.03 at n ≥ 20).

## Verdict

- `growing` / `declining`: p < 0.10 and the annualized trend is non-zero, in
  that direction. α = 0.10 is lenient on purpose: with 24 points the test has
  modest power and a founder wants "probably growing", not a publication
  standard.
- `flat`: not significant and |annualized trend| < 10%/yr.
- `unclear`: not significant but |annualized trend| ≥ 10%/yr — a sizeable
  estimate that is not consistent enough to trust.
- `insufficient_data`: fewer than 6 usable months.
- `magnitude`: |annualized trend| < 10% `small`, < 30% `moderate`, else `large`.

Absolute and relative verdicts use identical rules.

## Trust score (0–100, then `high` ≥ 70, `medium` ≥ 40, `low`)

Start at 100 and deduct:

| Condition | Deduction |
|---|---|
| median despiked monthly views < 500 | −35, and label capped at `medium` |
| median < 3,000 | −15 |
| spike share ≥ 30% / ≥ 10% | −30 / −15 |
| raw YoY > +20% but despiked YoY < +5% (growth made of spikes) | −20 |
| verdict not `flat` and p ≥ 0.10 / ≥ 0.05 | −20 / −10 |
| YoY and long-run slope disagree in sign (|YoY| > 5%) | −15 |
| article existed for only part of the window | −15 |
| fewer than 18 months (no seasonal control) | −15 |
| monthly CV (despiked) > 0.5 | −10 |
| human-only vs all-traffic change differ ≥ 15 points or in sign | −10 |

Reclassification (edition user-share drop ≥ 10 points or article ≥ 15 points
between the first and last 6 months) and absolute/relative disagreement add a
reason without a deduction. The weights are heuristics calibrated on synthetic
golden cases (`tests/test_metrics_trust.py`) and on the live examples in
`experiments/`; they are meant to be tuned as more real series are reviewed.

## Known limitations

- **Attention ≠ demand.** Pageviews say people looked something up, not that
  they would pay for it.
- **Article ≠ topic.** A topic may be split across several articles (sub-
  concepts live on redirects or separate pages), or have no article in an
  edition (Polish has none for intermittent fasting). Different editions may
  define the concept more broadly or narrowly.
- **Platform shifts.** Measured in this project: human-labelled pageviews of
  whole editions fell 7–25% year-over-year in the 2024–2026 window, and the
  share of traffic labelled human stepped down in mid-2025 (Wikimedia revised
  its bot classification; it had also changed it in April 2020). Attributed,
  not measured: the Wikimedia Foundation (Oct 2025) links part of the wider
  decline to AI answer engines and social media. Relative metrics and the
  agent-mix diagnostic mitigate but do not eliminate these effects.
- **Bots on small pages.** Automated traffic that escapes classification can
  concentrate on single articles and produce spikes; the despiked series and
  spike share expose this but cannot identify the cause.
- **Causes are not in the data.** Spike dates are facts; explanations are
  hypotheses.
- **Not implemented (future):** detection of page moves as such (redirect
  summing covers their effect), Google Trends cross-check, topic-level
  aggregation over many articles, day-of-week normalisation.
