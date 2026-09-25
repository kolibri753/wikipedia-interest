---
name: wikipedia-interest
description: Measure and compare how interest in a topic is changing over time across Wikipedia language editions, using Wikimedia pageview data, and turn it into charts and a short shareable PDF report. Use this whenever someone wants evidence about audience interest, demand, popularity or growth for a topic, course, feature or product idea in one or more languages, countries or markets — e.g. "is interest in X growing in Ukrainian?", "compare Polish vs Czech interest in Y", "which languages should we launch in next?", "is this growth real?" — even if they do not mention Wikipedia or pageviews. Also use it to refine or re-run an earlier interest analysis with different languages, time ranges or assumptions.
license: MIT
compatibility: Requires Python 3.11+ with matplotlib and reportlab (see requirements.txt) and network access to wikimedia.org, wikipedia.org and wikidata.org.
---

# Wikipedia interest analysis

Wikipedia pageviews are a **proxy for public attention**, not for willingness to
pay. They help choose which direction to validate next and compare audiences;
they are not market sizing. Keep that framing in every answer and report.

All calculation happens in the bundled scripts. Never fetch data, do arithmetic
or write analysis code yourself: run a script, read its JSON, interpret it.

## Workflow

1. **Analyze** — one command per topic. It maps the topic to the matching
   article in each language (via Wikidata), fetches two years of daily human
   pageviews including redirect titles, computes trend and reliability metrics
   for the article _and_ for its share of the whole edition, and draws charts.

   ```bash
   python scripts/analyze.py --topic "intermittent fasting" --langs pl cs
   ```

   Options: `--months 24` (default; keep multiples of 12 so seasons cancel),
   `--qid Q1860` to pin the Wikidata concept, `--article pl="Title"` to pin a
   title in one language, `--agent all-agents` only if the user explicitly wants
   bot traffic included, `--no-redirects`, `--out out/my-run`, `--offline`
   (cache only). Run `--help` for the rest.

2. **Check what was analyzed — before reading any number.** The summary's
   `label` and `description` say which Wikidata concept was used, and each
   language's `article` which page. Wikidata search matches _labels_, so it can
   return a specific thing that merely shares the name. The script refuses to
   resolve those automatically: items that are a programme, journal, film,
   album, organisation, person or similar (by Wikidata's _instance of_) are
   excluded, and an auto-resolved concept whose articles all have under
   100 views/month must be confirmed. In a real run, `"learning English"`
   matched Q2731224, `Learning English — simplified English in Voice of
America`. Its _instance of_ classes are `controlled language` and `plain
English`, so it is intentionally not excluded by the specific-entity gate.
   Instead, its very low pageview volume triggers `confirm_concept`; for the
   intended broad concept in this example, the correct item is the English
   language, Q1860. When the script stops for either reason, choose the general
   concept the user means and re-run with `--qid` (you usually know the item;
   `--topic` may then be omitted) — only pass an excluded item's QID if the user
   really means that specific thing. Wikidata labels are editable text
   (`"English"` for Q1860); the QID is the stable identity. Always tell the user
   which articles you compared; the same topic can be broader or narrower in
   another edition, and `warnings` flags when pinned titles belong to different
   Wikidata items.

3. **Read the summary** (one JSON object). Per language:
   - `verdict` (`growing` / `declining` / `flat` / `unclear` / `insufficient_data`)
     and `magnitude` (`small` <10%/yr, `moderate` <30%, `large`) — this is the
     **absolute** trend of human pageviews.
   - `relative.verdict` — the same call for the article's **share of the whole
     edition** (`median_per_million` = views per million edition views). Use
     `relative` to compare languages and to say whether a topic is gaining or
     losing attention; use absolute numbers to say how many people.
   - `trust.score` / `trust.label` with `trust.reasons` (each deduction as a
     plain sentence), `trust.notes` (context that changes the reading without
     lowering the score: bot reclassification, absolute vs relative gap, a
     level shift) and `trust.strengths`. Relay reasons and notes; together
     they answer "how much can this be trusted".
   - `growth.yoy` (last 12 months vs the 12 before), `growth.annualized_trend`
     (robust, seasonality-controlled, spikes removed), `months_up_of` (how many
     months beat the same month a year earlier, e.g. `2/12`) with `p_value`,
     and `growth.recent_3m_vs_same_3m_last_year` — whether the trend is still
     running or has levelled off recently.
   - `shape.largest_monthly_drop` / `largest_monthly_rise` — when the biggest
     single-month change happened. A decline concentrated in one step is a
     different story from a steady slide; say which it is.
   - `spikes` — how much traffic came from a few days, with the dates.
   - `agent_mix` — how the human-only trend compares with all traffic. When
     `reclassification_suspected` is true, Wikimedia's 2025 bot-classifier
     change is inflating the apparent decline; `trust.reasons` says by how much.
   - `volume.peak` / `volume.trough` months, `redirects` (how much of the
     traffic arrived via alternative titles), `partial_history` (article newer
     than the window).
   - Top level: `comparison` — when two or more languages were analyzed, the
     ranking by relative attention, by volume and by relative growth, plus a
     `statement` sentence. **Quote the statement for any "which language is
     strongest / most promising" question instead of ranking numbers yourself**;
     `highest_relative_attention` names the language with the largest share of
     its edition's attention. Also `missing` (languages with **no article** for
     the topic, with search suggestions), `alternatives` (other Wikidata items
     that matched), `warnings`, `files` (analysis.json and the PNG charts).

4. **Answer** in the user's terms. Lead with the verdict and trust label, give
   the two or three numbers that matter, then the caveats from `trust.reasons`
   and `trust.notes`. Never report a growth percentage without its trust label.
   When languages differ, explain _why_ (spikes, volume, article age,
   classification) rather than only ranking them. A language in `missing` is a
   result in itself: **there is no measurement, so never write "0 views"** —
   no article usually means a small audience or an editorial gap; say which is
   plausible, and offer the suggested titles only if their scope fits (a
   broader article, e.g. "therapeutic fasting" for "intermittent fasting", is a
   different signal and must be labelled as such). Use only numbers that appear
   in the script output; do not compute new ones. Recommendations may say
   _what to validate and how_ (a survey, a landing page, search-volume data);
   they may not assert what an audience wants, will pay for or is "receptive
   to" — pageviews cannot show that. End every answer with one sentence stating
   that Wikipedia pageviews measure attention, not demand or willingness to
   pay, and naming one way to validate the signal.

5. **Report** only when the user wants something shareable. Write your
   interpretation (3–6 short paragraphs: what was compared, what the data
   shows, what to validate next) to a Markdown file, then:

   ```bash
   python scripts/report.py --analysis out/<run>/analysis.json --notes notes.md
   ```

   Page 1 is the shareable one-pager (parameters, per-language table, the
   cross-language share chart, the caveats, your notes, a limitations footer);
   page 2 shows the monthly charts (`--no-appendix` for a strict one page).
   Every figure comes from `analysis.json`; your notes appear only in the
   labelled "Analyst notes" section, so readers can tell measurement from
   interpretation. `analyze.py` prints the `analysis.json` path under `files`;
   the output folder is `out/<topic>-<langs>[-<qid>]`.

## The data says _when_, not _why_

Spike dates, peaks and troughs are facts; their causes are not in the data. Do
not attribute a spike to news, TV, holidays or social media unless the user
told you or you verified it. If a cause seems likely, phrase it as a hypothesis
to check ("worth checking what happened around 2026-01-07"), not as a finding.
The same applies to declines. Two things are _measured_: human-labelled
pageviews fell across editions in 2024–2026 (`agent_mix.edition_user_yoy`),
and Wikimedia revised its bot classification in 2025
(`agent_mix.reclassification_suspected`). The Wikimedia Foundation _attributes_
part of the wider decline to AI answer engines; that is their interpretation,
not something this data shows — quote it as theirs if you mention it. So an
absolute decline may not mean waning interest; `relative` and `agent_mix`
exist to separate these, and the report should say so.

## When the script stops early

The scripts exit non-zero with a JSON object on stdout, so you can act without
guessing:

- `ambiguous_topic` (exit 2): several distinct Wikidata items match, with
  `candidates` (label, description, which languages have articles). Pick the
  one the user means — ask if genuinely unclear — and re-run with `--qid`.
- `no_articles` (exit 3): no general-topic article in _any_ requested
  language. `candidates` may list matches that were **excluded** as specific
  entities (with the reason); `suggestions` lists per-edition search hits. If
  the user means a general concept, re-run with its `--qid`; use
  `--article xx="Title"` only if a suggestion is really the same concept;
  otherwise report the absence.
- `confirm_concept` (exit 5): the auto-resolved concept has under 100
  views/month in every language. Decide: wrong concept → re-run with the right
  `--qid`; genuinely tiny topic → re-run with the same `--qid` to confirm.
- `api_error` (exit 4): Wikimedia did not respond after retries. Re-run once;
  if it persists tell the user.

A single missing language does **not** stop the run; it appears in `missing`.

## Refinements

Users iterate: "add German", "desktop only", "last 12 months", "use the
narrower article". Re-run `analyze.py` with the new flags — responses are
cached locally, so repeated and related requests are fast and do not re-hit
Wikimedia. Never adjust numbers by hand. For "learning English"-type topics,
analyze the broad concept (English language) and the narrow one (English as a
second language, where it exists) as two runs and compare.

## Interpretation rules of thumb

- `unclear` means the estimated change is sizeable but not consistent enough
  to distinguish from noise. Say that; do not round it to "growing".
- Fewer than ~3,000 views/month is thin evidence for any percentage claim.
- An article created inside the window looks like it is growing because it is
  new; `partial_history` flags this and the statistics use only complete
  months after creation.
- Two years is the default because it gives one full year-over-year
  comparison; with fewer than 18 months the seasonal controls are off and
  `trust.reasons` says so.

For how each metric is computed and its thresholds, read
`references/methodology.md`. For API behaviour and data caveats, read
`references/api-notes.md`.
