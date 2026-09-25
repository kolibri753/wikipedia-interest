# WALKTHROUGH

Technical walkthrough of `wikipedia-interest`.

The README covers the public interface, commands and current verification status.
`DECISIONS.md` keeps the chronological engineering log. This document starts below that level: data flow, module boundaries, statistics, semantic resolution, model-facing contracts, caching and eval infrastructure.

## 1. System boundary

The main boundary is:

**code computes, the model interprets.**

`analyze.py` performs topic resolution, retrieval, normalization, statistics, trust assessment, cross-language comparison and chart generation. `report.py` renders deterministic analysis output plus optional model-written notes.

```text
user request
    │
    ▼
SKILL.md
    │
    ▼
scripts/analyze.py
    │
    ├── resolve concept
    ├── fetch article + redirect traffic
    ├── fetch edition totals
    ├── build daily/monthly series
    ├── compute absolute metrics
    ├── compute relative metrics
    ├── inspect agent mix
    ├── assess trust
    ├── compare languages
    └── render charts
    │
    ▼
compact JSON + analysis.json + PNG charts
    │
    ▼
model interpretation
    │
    ▼
scripts/report.py
    │
    ▼
PDF summary + chart appendix
```

The model does not receive the raw daily series and is not expected to calculate important metrics or rank languages itself. `analysis.json` is the numeric source of truth. Model-generated prose is kept in the labelled `Analyst notes` section of the report.

This boundary matters because the model-facing result is deliberately much smaller than the full analysis. A normal three-language run is roughly 1.4k tokens in compact form, while `analysis.json` keeps the detailed series and diagnostics needed for debugging or reporting.

## 2. Data flow

`analysis.analyze(Params, aqs, mw, out_dir)` is the main orchestration function.

### Analysis window

`series.analysis_window(months, today)` produces the last `N` complete UTC months. The default is 24.

Only complete months are used, so a run made halfway through September does not compare a partial September with complete historical months. Multiples of 12 also keep the window aligned to the same seasonal phase.

### Topic resolution

`resolve.resolve(...)` maps the requested topic to a Wikidata concept and then to one Wikipedia article title per requested language.

The priority is:

```text
explicit --article
        ↓
explicit --qid
        ↓
automatic Wikidata resolution
```

Resolution can stop before data retrieval with structured states such as `ambiguous_topic` or `no_articles`. The low-volume semantic confirmation gate runs later, after traffic is available.

### Article traffic

`analysis.fetch_article_series(...)` requests daily `agent=user`, `all-access` pageviews for the canonical article and each redirect title.

The fetch begins 31 days before the analysis window. Those extra days are used only to establish the rolling baseline for spike detection and are removed before the final metrics are produced.

Redirect traffic is summed into the canonical series. Redirects have independent pageview series, and summing them also makes page moves less disruptive because an old title generally becomes a redirect to the new one.

The result is a `DailySeries`.

### Edition totals

For every language, the analysis also retrieves monthly traffic for the entire Wikipedia edition.

Both `user` and `all-agents` aggregates are used.

Edition totals serve two purposes:

1. normalizing an article against the size of its edition;
2. checking whether apparent article movement is sensitive to Wikimedia's human/bot traffic classification.

### Metrics

`metrics.compute_metrics(...)` converts the daily article series into the main absolute metrics.

It handles spike detection, despiking, monthly aggregation, YoY change, robust trend, seasonal consistency, recent movement, volume, volatility, shape, verdict and magnitude.

The statistics are described in §5.

### Relative metrics

`analysis._relative(...)` creates the cross-language series:

```text
article_views
────────────── × 1,000,000
edition_views
```

This is not just a final ratio. The resulting monthly `per_million` series is passed through the same trend machinery as the absolute article series.

Each language therefore has both:

```text
absolute trend
relative trend
```

The relative version answers a different question: whether the topic is gaining or losing share of attention inside that edition.

### Agent mix

`analysis._agent_mix(...)` compares `user` and `all-agents` traffic for both the article and the whole edition.

The output is used to detect large classification shifts and cases where the human-only trend is substantially different from the all-traffic trend.

This does not attempt to mathematically remove the platform shift. It exposes whether the interpretation is sensitive to it.

### Trust

`trust.assess_trust(...)` evaluates the statistical series. `analysis._trust_with_context(...)` adds diagnostics that depend on the edition and agent-mix results.

The output separates deductions from explanatory context; see §6.

### Comparison

`analysis.compare_languages(...)` computes cross-language rankings and a compact comparison statement.

This exists because ranking nested numeric output is deterministic arithmetic and therefore should not be delegated to the model.

### Semantic confirmation

After metrics are available, `analysis.py` applies the low-volume concept gate.

If the concept was resolved automatically and every available article has fewer than 100 median monthly views, the run raises `ConfirmConcept` and returns exit code 5 before writing the normal analysis artifacts.

Explicit `--qid` bypasses this confirmation because it represents an intentional concept choice.

### Output

A successful run writes:

```text
analysis.json
chart_monthly.png
chart_share.png
```

and prints `analysis.summarize(full)` as compact model-facing JSON.

`report.build_report(...)` later reads `analysis.json`; it does not recalculate the analysis.

## 3. Module map

The implementation lives under `src/wiki_interest/`.

| Module         | Responsibility                                                                                                             |
| -------------- | -------------------------------------------------------------------------------------------------------------------------- |
| `analysis.py`  | Orchestration, relative metrics, agent mix, comparison, compact output, low-volume gate                                    |
| `resolve.py`   | Topic → Wikidata concept → per-language titles; exclusions, ambiguity, missing languages                                   |
| `metrics.py`   | Daily series → metric dictionary; verdict/magnitude rules; generic monthly-series trend                                    |
| `stats.py`     | MAD, rolling median, spike detection, Theil–Sen, Mann–Kendall, seasonal Mann–Kendall, trailing sums, log-change volatility |
| `trust.py`     | Rule-based trust score plus reasons, notes and strengths                                                                   |
| `series.py`    | Analysis windows, AQS parsing, zero filling, daily → monthly conversion                                                    |
| `aqs.py`       | Wikimedia AQS per-article and aggregate requests                                                                           |
| `mediawiki.py` | Wikidata search/enrichment plus MediaWiki redirects, revision metadata, title/QID mapping and search                       |
| `titles.py`    | Title normalization, encoding and language-code validation                                                                 |
| `http.py`      | User-Agent, throttling, retries, `Retry-After`, cache integration and injectable HTTP                                      |
| `cache.py`     | URL-keyed JSON cache with atomic writes                                                                                    |
| `charts.py`    | Matplotlib monthly and relative-attention charts                                                                           |
| `report.py`    | ReportLab PDF generation                                                                                                   |
| `cli.py`       | CLI arguments, structured errors, exit codes, UTF-8 stdout                                                                 |
| `env.py`       | Minimal `.env` loader that does not overwrite real environment variables                                                   |

`scripts/analyze.py` and `scripts/report.py` are thin entry points that make `src/` importable and call the package code. The skill does not need to be installed as a Python package before use.

## 4. Wikimedia data semantics

Several parts of the implementation exist because Wikimedia's APIs have semantics that are easy to misread.

### Titles are identifiers for pageview requests

AQS pageview requests are keyed by title strings.

The normalization layer converts spaces to underscores, normalizes the first letter, preserves characters such as `ß`, and percent-encodes the path correctly.

A malformed title is especially dangerous because it does not always fail. During probing, a lower-case variant returned HTTP 200 with almost no views instead of producing an obvious error.

That is why title normalization happens before URL construction rather than after suspicious data appears.

### Redirects have their own traffic

A view to a redirect title is not automatically part of the canonical article's AQS series.

`mediawiki.py` retrieves redirects and `fetch_article_series` requests their traffic separately.

The merged series therefore represents traffic arriving through both the canonical title and known redirect titles.

### Missing dates are zero

AQS daily responses can omit dates with zero views.

`series.py` reconstructs the requested calendar and fills absent dates with zero before monthly aggregation or statistical processing.

Otherwise, calculations such as rolling baselines and monthly totals would operate on an irregular time axis.

### Labels are not identity

Wikidata labels are editable text.

Q1860, for example, can be returned with the label `English` rather than `English language`. Tests that depended on the exact label broke when responses were re-recorded even though the underlying concept was unchanged.

The QID is therefore treated as identity. Labels are display text.

### Sitelinks determine language coverage

A Wikidata item may have articles in some requested editions and not others.

A missing sitelink is not converted into a zero-valued article. It appears under `missing`, optionally with title-restricted search suggestions.

That distinction propagates all the way to the model-facing contract: missing coverage means no measurement, not `0 views`.

### Human and all-agent traffic are different signals

During the 2025 window, the share of traffic classified as human changed materially in some editions.

The implementation therefore keeps `user` and `all-agents` series separate rather than assuming `user` traffic is stable over time.

The relative series also helps distinguish article-specific movement from edition-wide movement.

## 5. Time-series implementation

The statistical code is in `stats.py` and `metrics.py`.

The main constraint is a short history: normally 24 monthly observations, with potentially strong seasonality, isolated spikes, and a platform-wide traffic regime change.

### Spike detection

Spike detection runs on daily traffic before monthly aggregation.

A centered 29-day rolling median provides a local baseline. Residuals are measured against that baseline and their scale is estimated with MAD.

A day is considered a spike only when all three conditions are satisfied:

```text
residual > 5 × (1.4826 × MAD)
absolute increase >= 20 views
increase >= 100% of the local baseline
```

The absolute and relative guards prevent tiny series from producing meaningless "spikes".

Detected spike days are replaced with the local baseline in the despiked series.

This deliberately distinguishes a short isolated event from a sustained seasonal elevation. A month-long September increase should remain part of the series; it is not treated as a one-day anomaly.

The 31-day pre-window pad exists because spike detection near the left boundary otherwise has no established baseline. The pad is removed before final monthly metrics are produced.

### Monthly aggregation

Both raw and despiked daily series are converted to monthly totals.

The raw series is preserved because spike influence is itself useful information. The despiked series is used where the goal is to estimate sustained movement.

### Year-over-year change

YoY is:

```text
sum(last 12 months)
───────────────────── - 1
sum(previous 12 months)
```

It is calculated on both raw and despiked monthly totals.

A large difference between the two is evidence that isolated events contribute substantially to the apparent growth.

### Robust trend

The long-run trend uses Theil–Sen on the logarithm of trailing-12-month totals of the despiked series.

For month `t`:

```text
T_t = sum of the previous 12 monthly values
x_t = log(T_t)
```

Theil–Sen estimates the median pairwise slope of `x_t`.

The monthly log slope is converted into an annualized percentage:

```text
annualized_trend = exp(12 × slope) - 1
```

Trailing-12-month totals suppress the annual seasonal cycle before the robust slope is fitted. The log transform makes the trend multiplicative.

The first implementation applied plain Theil–Sen and Mann–Kendall directly to the 24 monthly observations. A synthetic zero-trend seasonal series was then classified as declining with maximum trust. That case remains a regression test because it demonstrates why the seasonal transformation is part of correctness rather than presentation.

### Seasonal consistency

With approximately two years of data, each calendar month is compared with the same month one year earlier.

The implementation exposes both the statistical result and a simpler count:

```text
k of 12 months up
```

This count is the number of same-month pairs whose second-year value is higher.

Below 18 months, there is not enough history for the normal seasonal path. The implementation falls back to the plain methods and returns:

```json
"seasonality_controlled": false
```

which also affects trust.

### Recent movement

`recent_3m_vs_same_3m_last_year` compares the latest three complete months with the same three months one year earlier.

This is kept separately from the long-run trend so a series can be described as historically declining while showing a recent reversal, or vice versa.

### Verdict and magnitude

The high-level verdict is one of:

```text
growing
declining
flat
unclear
```

`growing` or `declining` requires the trend test to support a non-zero direction. A sufficiently small trend becomes `flat`; a larger estimate that is not supported strongly enough becomes `unclear`.

Magnitude is independent:

```text
small
moderate
large
```

The exact thresholds live in `metrics.py`, so changing them is a behavior change rather than a presentation-only change.

### Volatility

Volatility is the standard deviation of log month-over-month changes on the despiked series.

The earlier coefficient-of-variation implementation was sensitive to trend itself: a smooth decline could be labelled highly volatile simply because the level changed substantially over the window.

Using log changes makes the measure respond to irregular movement rather than the absolute trend.

### Independent verification

The pure statistical functions have hand-computed test cases.

Where SciPy is installed, optional oracle tests compare the implementation against SciPy: Theil–Sen slope matches directly, and Mann–Kendall components are checked against the corresponding rank statistic with the documented continuity-correction difference.

SciPy is not a runtime dependency.

## 6. Trust model

`trust.assess_trust` starts from 100 and applies deterministic deductions.

| Condition                                                            | Deduction |
| -------------------------------------------------------------------- | --------: |
| Median despiked monthly views < 500 / < 3,000                        |   35 / 15 |
| Spike share >= 30% / >= 10%                                          |   30 / 15 |
| Raw YoY > +20% but despiked YoY < +5%                                |        20 |
| Non-flat verdict with weak significance                              |   20 / 10 |
| YoY and robust slope disagree in sign                                |        15 |
| Article existed for only part of the window                          |        15 |
| < 18 months, so seasonal control is unavailable                      |        15 |
| Month-over-month log std > 0.35                                      |        10 |
| Human-only and all-traffic changes differ by >= 15 points or in sign |        10 |

The result deliberately has three explanatory channels:

```text
reasons
notes
strengths
```

`reasons` correspond to actual deductions. `notes` contain context that changes how the result should be read without reducing the score, such as a platform-wide traffic shift or a large absolute-vs-relative gap. `strengths` record supporting evidence.

Labels are:

```text
70–100  high
40–69   medium
<40     low
```

Low-volume cases can also cap the label.

The score is a heuristic summary, not a calibrated probability. The individual explanations are more important than the number.

One subtle rule is that a non-significant result is not automatically bad. If the estimated trend is small and the verdict is `flat`, lack of significance is consistent with that verdict and should not create a deduction.

## 7. Semantic resolution

Resolution is the other major correctness boundary.

`resolve.resolve(...)` accepts three levels of specificity.

An explicit article mapping has highest priority:

```bash
--article pl="..."
```

The article is checked for existence, and its Wikidata item is retrieved so a cross-language mismatch can be surfaced rather than silently accepted.

An explicit QID is next:

```bash
--qid Q1860
```

This pins concept identity while still using Wikidata sitelinks to locate the requested language articles.

Without either, automatic resolution uses `wbsearchentities`.

Candidates are enriched with labels, descriptions, requested-language sitelinks, and `instance of` (`P31`) classes.

### Candidate exclusion

`_exclusion_reason(...)` rejects candidates that should not be selected automatically.

This includes disambiguation/list items, candidates with no usable article in any requested edition, known specific-entity classes such as programmes, films, albums, journals, scholarly articles, organizations, companies, people and products, and some description patterns that clearly describe a specific work.

Among usable candidates, an exact label match is preferred. If several plausible candidates remain and no single exact match resolves the ambiguity, the resolver returns `ambiguous_topic` with exit code 2 instead of guessing.

If no usable candidate has an article in the requested editions, it returns `no_articles` with exit code 3.

A missing individual language does not stop an otherwise valid run.

### Why resolution has two deterministic gates

`"learning English"` exposed a gap between semantic instructions and actual model reliability.

The automatic search can select Q2731224. Its traffic is only around 8–24 monthly views in the tested editions, but some models still continued with it even after seeing that it was not the intended general concept.

The first safeguard is ontology-based.

If the candidate's P31 class is in `SPECIFIC_ENTITY_CLASSES`, it cannot be selected automatically.

This gate is deliberately conservative. On real Wikidata data, Q2731224 is classified as:

```text
controlled language
plain English
```

Those are concept-like classes. Blacklisting them would also reject legitimate topics such as plain language.

The second safeguard therefore uses observed traffic.

If automatic resolution succeeded but every available article has fewer than 100 median monthly views, `analyze.py` raises `ConfirmConcept` before producing the normal outputs.

The stop includes enough evidence for the caller to decide what to do next:

```text
qid
label
description
instance_of
per-language volumes
```

Re-running with an explicit `--qid` confirms that the selected tiny concept is intentional. Supplying a different QID chooses another concept.

The real `"learning English"` case is important because it demonstrates why these gates are complementary: the ontology gate does not fire, while the volume gate does.

## 8. Caching and offline replay

All network traffic goes through `JsonHttpClient`.

`JsonCache` stores successful JSON responses and 404 sentinels using a hash of the complete request URL:

```text
URL
 │
 ▼
SHA-1
 │
 ▼
cache file
```

The normal runtime location is:

```text
~/.cache/wikipedia-interest
```

or the path configured with `WIKI_INTEREST_CACHE`.

Writes are atomic. `--no-cache` bypasses reads/writes, while `--offline` forbids network access and requires every requested URL to exist in the selected cache.

The eval fixture store deliberately uses the same representation.

`evals/record_fixtures.py` clears `evals/fixtures/cache/` and records the canonical examples with `--today 2026-09-23` pinned so their analysis windows and resulting URLs stay stable.

The recorder also knows the expected exit status of each example. In particular, the unpinned `"learning English"` example is expected to stop with exit code 5 rather than complete.

This design makes request construction part of the reproducibility contract.

Changing a query parameter, date pad, Wikidata enrichment request, or any other URL component produces a different hash. Offline replay then fails immediately instead of accidentally returning a response recorded for an older request.

When request construction intentionally changes, the fixtures are re-recorded with:

```bash
python evals/record_fixtures.py
```

## 9. Charts and PDF

`charts.chart_monthly(...)` renders one panel per language. It shows monthly traffic, the despiked series, article creation time where relevant, and the resulting verdict/trust context.

`charts.chart_share(...)` puts all requested editions on one axis using `views_per_million`.

The PDF layer is intentionally downstream of `analysis.json`.

`report.build_report(...)` reads the analysis, orders the language table using the deterministic relative-attention comparison, renders the cross-language chart, confidence notes, computed comparison and optional analyst notes.

The default output is:

```text
page 1  summary / comparison / confidence / analyst notes
page 2  monthly chart appendix
```

`--no-appendix` suppresses the second page.

ReportLab's built-in fonts do not cover all Czech and Cyrillic characters used by the examples. The renderer therefore registers DejaVu Sans from matplotlib's installed package data rather than adding another font dependency.

## 10. Agent Skill contract

The model-facing entry point is `SKILL.md`.

An Agent Skill uses progressive disclosure. At selection time, the runtime initially needs only the skill name and description. The body is loaded after the skill triggers; referenced material can be loaded later as needed.

For this project, the body acts as an interface contract between the model and deterministic code.

It defines the expected workflow, the meaning of compact JSON fields, structured stops, and the interpretation rules that cannot be enforced completely in code.

Important examples are:

```text
missing article != 0 views
pageviews measure attention, not demand
data identifies when a movement occurred, not its external cause
use the computed comparison rather than independently ranking numbers
```

The distinction is important: anything that must be guaranteed is moved into Python when possible. `SKILL.md` still governs model interpretation, but it is not used as a substitute for deterministic validation.

The compact JSON contract is behavior-sensitive. Its keys are consumed by the skill instructions, eval checks and grounding logic, so changing that contract has a larger blast radius than refactoring an internal Python function.

## 11. Context management

Free-tier model limits affected both the skill output and the eval runtime.

Groq's tested GPT-OSS endpoint has an 8,000-token request limit and an 8,000-token-per-minute budget. A refinement flow containing multiple analysis outputs exceeded that limit even though each individual analysis was reasonable.

The skill-side fix was to make the default stdout summary compact JSON. Fields that are derivable from other values were removed, `strengths` were omitted from the compact form, and the `agent_mix` block was collapsed to the parts the model actually needs.

A three-language result is roughly 1.4k tokens, down from about 2.3k.

The harness also performs context compaction.

The most recent two script outputs stay in full while the budget allows. Older outputs are replaced with small valid-JSON stubs rather than plain truncation. Those stubs retain information such as the concept, per-language verdicts and output-file paths.

Valid JSON matters because the model may continue treating an old tool result as structured data.

The first compaction version discarded outputs purely by recency and removed `files.analysis`, which the later report step needed. The current strategy preserves those pointers.

`read_file` exists for cases where the agent genuinely needs a persisted artifact, but its output is capped at 6 KB. A full `analysis.json` can be around 40 KB, which would crowd the useful compact summary out of an 8k context window.

### Provider pacing

The eval client keeps a sliding 60-second token window.

Before a request, it estimates whether the next call would exceed the configured TPM budget. After completion, real provider usage is folded back into the window.

Provider errors are classified separately:

```text
413 / explicit request-size overflow  → context_limit
per-minute 429                       → wait/retry
daily or billing quota               → quota_exhausted
transient provider failure           → provider_error / retry where appropriate
```

This distinction matters for eval accounting: a provider limit is not evidence that the model failed the scenario.

## 12. Eval harness

The main agent eval is implemented by:

```text
evals/harness.py
evals/evals.json
```

The harness approximates a skill-aware runtime rather than testing only static prompts.

The model receives a short system preamble plus the `SKILL.md` body and can call three tools:

```text
run_script
read_file
write_file
```

`run_script` is allow-listed to the skill's scripts. Analysis dates are pinned where required, and Wikimedia data comes from the fixture cache during reproducible runs.

Each user turn allows at most 10 model/tool steps.

Provider-returned assistant messages are stored and replayed verbatim. This is required for providers such as Gemini whose tool-call message objects can contain metadata such as `thought_signature`; reconstructing the visible fields alone is not equivalent to replaying the original message.

### Scenarios

The harness covers the assignment examples plus additional interaction patterns:

```text
intermittent fasting
Ukrainian astronomy
learning English
ambiguous Mercury
two-turn refinement
```

The point is not just final-answer quality. The transcript shows whether the agent chose the correct scripts, handled structured stops, preserved the selected concept across turns and generated the expected artifacts.

### Grading

The grader combines scenario-specific checks with cross-cutting checks derived from observed model failures.

`numbers_grounded` verifies that percentages and sufficiently large numeric claims in the final answer correspond to values exposed by tool output, within the supported rounding rules.

`no_unsupported_spike_causes` catches external explanations for movements when the analysis only establishes timing or shape.

`no_unsupported_market_inference` checks for claims about demand, receptiveness or willingness to buy that are inferred directly from pageviews.

Scenario checks also verify things such as:

```text
correct topic/languages
expected --qid after semantic confirmation
missing Polish article reported as missing
no "0 views" substitution
expected PDF exists
step limit not exceeded
```

The grader is intentionally a regression net rather than a general proof of answer quality.

It also has its own tests. Real transcripts exposed false positives involving values such as `40/100`, thousands separators such as `16 366`, Markdown boundaries, `median` matching `media`, and `term` inside `short-term`.

Those cases live in `tests/test_harness_checks.py`.

One known limitation is also explicit: if an invented percentage happens to equal some unrelated numeric value present in tool output, basic numeric grounding may accept it. Grounding verifies presence, not semantic attribution.

### Scenario status

A scenario result has an execution status independent of its grade.

Typical states are:

```text
completed
provider_error
context_limit
quota_exhausted
not_run
```

Aborted scenarios preserve their partial transcript but are excluded from the normal pass denominator.

Every run writes a machine-readable summary, a Markdown summary and one transcript per scenario.

## 13. Trigger eval

`evals/trigger_eval.py` tests a different boundary from the main harness.

Before an agent can follow `SKILL.md`, it has to decide that this skill is relevant from the skill metadata.

The trigger eval therefore gives a model the `wikipedia-interest` description plus three plausible distractors:

```text
web research
CSV/chart analysis
Wikipedia article summary
```

and asks it to choose the appropriate skill for 14 positive and 14 negative requests.

It records recall, precision, individual misses and partial results if the provider aborts.

The dataset is author-written, so it is best treated as a regression test for the description rather than an estimate of real-world routing precision.

## 14. Testing strategy

The deterministic suite has five layers.

### Statistics / oracle tests

`tests/test_stats.py` protects the numerical primitives.

It uses hand-computed values and, where SciPy is installed, independent oracle comparisons. The SciPy checks are optional and skip when the dependency is absent.

### Component and integration tests

The API, resolver, CLI and report path are tested using files such as:

```text
test_series_titles_aqs.py
test_mediawiki.py
test_cli_end_to_end.py
test_report.py
test_env.py
```

`tests/fake_wikimedia.py` provides deterministic responses modelled on the real API shapes.

The fake world includes cases such as junk Wikidata candidates, missing language coverage, redirects, September seasonality, traffic-classification shifts and tiny wrong-concept articles.

The generated noise is deterministically seeded. Python's built-in `hash()` is not used for this because hash randomization once made the test world vary between processes.

### Golden judgement tests

`tests/test_metrics_trust.py` feeds synthetic series with known intended behavior into the metric/trust layer.

Cases include steady growth, decline, flat series, pure seasonality, isolated spikes, tiny volume and short article histories.

The pure-seasonal series that broke the original trend method is kept permanently as a regression case.

### Offline real-data replay

`tests/test_examples_offline.py` executes the example queries against `evals/fixtures/cache`.

These tests are deterministic while still exercising responses captured from the live Wikimedia APIs.

If request construction changes, the URL-keyed lookup fails and explicitly asks for fixture re-recording.

### Eval-grader tests

`tests/test_harness_checks.py` protects the programmatic grader and context-compaction behavior using examples taken from real model transcripts.

The current deterministic run with fixtures present and SciPy absent is:

```text
99 passed, 2 skipped
```

The stochastic model eval and trigger eval sit above this deterministic suite rather than replacing it.

## 15. Debugging

The fastest debugging path depends on which boundary looks wrong.

### Wrong numeric result

Start with `analysis.json`, not the model transcript.

For the affected language inspect the monthly rows:

```text
views
despiked
edition views
per_million
```

If the transformation looks wrong, move down into `metrics.py` or `stats.py`.

If the source values look wrong, inspect the cached API response for the corresponding URL or rerun with `--no-cache`.

### Wrong concept

Inspect the resolver output or structured stop:

```text
qid
label
description
instance_of
alternatives
candidates[].excluded
```

Pin the intended item with `--qid` to separate resolver behavior from downstream analysis behavior.

### Unexpected model behavior

Use the saved eval transcript.

It contains the exact messages, tool calls and tool outputs the model saw, including the compact JSON. This is usually more useful than trying to infer model behavior from the final answer alone.

### Offline fixture failure

A stale/missing fixture normally means request construction changed.

If the change is intentional:

```bash
python evals/record_fixtures.py
```

and then rerun the offline suite.

### Wikimedia behavior

The live-probe evidence is kept in:

```text
experiments/
experiments/recorded/
```

Use those before changing code based on an assumption about the API.

### Windows text problems

File writes and normal script output are explicitly UTF-8.

If a new path produces mojibake or `UnicodeEncodeError`, check whether that code path bypasses the existing UTF-8 helpers or writes without `encoding="utf-8"`.

## 16. Change impact and extensions

Not every file has the same compatibility cost.

The highest-impact interfaces are:

| Change                              | What can depend on it                                |
| ----------------------------------- | ---------------------------------------------------- |
| Compact JSON keys                   | `SKILL.md`, eval scenarios, numeric grounding, tests |
| Structured error names / exit codes | Agent workflow, eval checks, CLI tests               |
| CLI flags / script names            | `SKILL.md`, harness commands, documentation          |
| Request URL construction            | Runtime cache and recorded fixtures                  |
| Resolution behavior                 | Semantic-stop tests and agent evals                  |
| Metrics/trust thresholds            | Golden cases, report output, eval expectations       |
| `SKILL.md` behavior rules           | Trigger/runtime model behavior and eval scores       |

Internal module refactors are lower risk when these interfaces remain stable.

### Multi-article topics

The current unit of analysis is one article per language.

A topic-level extension could resolve several relevant articles, fetch each daily series, sum them before `compute_metrics`, and retain per-article contributions separately.

Most of `stats.py`, `metrics.py` and the trust logic can remain unchanged because they operate on series rather than Wikimedia objects.

### Cross-language spike coincidence

Daily data is already available before monthly aggregation.

A useful deterministic extension would compare spike dates across language editions. A spike on the same date in several editions is stronger evidence of a shared external event than a spike isolated to one edition.

This still would not identify the cause, but it would provide a measured feature for the model to interpret.

### Bulk analysis

AQS per-article requests are appropriate for a small number of topics.

For hundreds or thousands of articles, the retrieval layer should move to Wikimedia pageview dumps:

```text
AQS per-article requests
        ↓
pageview dumps
```

and the JSON response cache could become a columnar store such as Parquet/DuckDB.

The statistics layer can remain pure functions over daily/monthly series.

### Evaluation

The deterministic checks should remain even if a model-based judge is added.

Useful next additions are harder trigger negatives, more refinement paths, non-Latin topic resolution, more weak-model runs and an LLM-as-judge rubric for qualities that cannot be expressed reliably with regex/programmatic checks.
