# wikipedia-interest

Agent Skill for analyzing Wikipedia pageview trends across topics and language editions.

It resolves a concept through Wikidata, fetches Wikimedia pageviews, computes seasonality-aware trends and cross-language metrics, produces charts, and can generate a short PDF report.

Wikipedia pageviews are treated as an **attention proxy**, not as demand or willingness to pay.

## Architecture

Main rule:

**code computes, model interprets.**

```text
user request
    ↓
SKILL.md
    ↓
scripts/analyze.py
    ↓
resolve topic
fetch pageviews + redirects
aggregate / despike
compute trend + trust
normalize across editions
generate charts
    ↓
compact JSON + analysis.json
    ↓
model interpretation
    ↓
scripts/report.py
    ↓
PDF
```

Important calculations, rankings and report numbers are deterministic Python.

## Quick start

Python 3.11+.

```bash
git clone https://github.com/kolibri753/wikipedia-interest.git
cd wikipedia-interest

python -m venv .venv
source .venv/Scripts/activate   # Git Bash / Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

No model API key is required to run the skill.

### Analyze a topic

```bash
python scripts/analyze.py \
  --topic "intermittent fasting" \
  --langs pl cs en \
  --pretty
```

Outputs:

```text
out/intermittent-fasting-pl-cs-en/
├── analysis.json
├── chart_monthly.png
└── chart_share.png
```

Missing articles stay `missing`; they are never converted to `0 views`.

### Generate a report

```bash
python scripts/analyze.py --qid Q1860 --langs pl cs uk

python scripts/report.py \
  --analysis out/q1860-pl-cs-uk-q1860/analysis.json \
  --notes notes.md
```

Default PDF:

- page 1 — summary and comparison;
- page 2 — monthly chart appendix.

Use `--no-appendix` for a strict one-page report.

## Metrics

Per language:

- absolute pageview trend;
- relative attention: article views per million edition views;
- YoY change;
- seasonality-aware robust trend;
- same-month-vs-last-year consistency;
- recent 3-month movement;
- spike share;
- redirects;
- user vs all-agent traffic diagnostics;
- trust score with reasons and context.

Cross-language ranking uses relative attention rather than raw pageviews.

## Important implementation decisions

A few failures directly changed the implementation:

- Plain Theil–Sen / Mann–Kendall on 24 monthly points misclassified a zero-trend seasonal series. Trend estimation now uses trailing-12-month totals plus same-month comparisons.
- Several 2025 declines initially looked like a platform artifact. Edition-level and all-agent diagnostics showed the platform shift was real, but the example topics were also declining relative to their editions.
- `"learning English"` resolved to a Voice of America concept. Prompt instructions were not reliable enough, so semantic safeguards moved into code.
- A model had `124.45` in context and still reported roughly `48`; language rankings are now computed in Python.
- A missing Polish article was occasionally rendered by models as `0 views`; the data contract now distinguishes missing coverage explicitly.

Full history: [DECISIONS.md](DECISIONS.md).

## Verification

```bash
pip install -r requirements-dev.txt

ruff check src tests scripts experiments evals
python -m pytest -q -rs
```

Current result:

```text
All checks passed!
99 passed, 2 skipped
```

The two skipped tests use SciPy as an optional oracle.

Tests cover:

- statistics against hand-computed cases;
- optional SciPy comparisons;
- synthetic trend / seasonality / spike cases;
- Wikimedia clients against a fake API world;
- semantic-resolution stops;
- PDF generation;
- eval-grader regression cases;
- offline replay using recorded Wikimedia responses.

Recorded fixtures are in:

```text
evals/fixtures/cache/
```

## Agent evals

The eval harness runs the real skill with `run_script`, `read_file` and `write_file`.

Observed free-tier results:

| Model            | Provider   |                           Result |
| ---------------- | ---------- | -------------------------------: |
| Nemotron 3 Ultra | OpenRouter |                            43/44 |
| GPT-OSS-120B     | Groq       |          37/44 latest full suite |
| Gemini 3.6 Flash | Gemini     | 20/21 across completed scenarios |

GPT-OSS also showed run-to-run variance: `english_report` scored 11/11 in one run and 5/11 in another.

Trigger eval:

```text
Nemotron: 14/14 should, 14/14 should-not
GPT-OSS:  14/14 should, 14/14 should-not
```

More detail: [evals/README.md](evals/README.md).

## Repository

```text
wikipedia-interest/
├── SKILL.md
├── README.md
├── WALKTHROUGH.md
├── DECISIONS.md
├── scripts/
├── src/wiki_interest/
├── references/
├── tests/
├── evals/
└── experiments/
```

Docs:

- [WALKTHROUGH.md](WALKTHROUGH.md) — architecture, data flow, statistics, eval harness, debugging, extensions
- [DECISIONS.md](DECISIONS.md) — development decisions and revisions
- [evals/README.md](evals/README.md) — eval setup and checks

## Optional configuration

```bash
cp .env.example .env
```

Optional variables:

```text
WIKI_INTEREST_USER_AGENT
GROQ_API_KEY
GEMINI_API_KEY
OPENROUTER_API_KEY
```

Model keys are only used by the eval tools.

## Limitations

- pageviews measure attention, not demand;
- one article is not always equivalent to a broad topic;
- some language editions have no matching article;
- Wikidata resolution can still produce scope mismatches;
- the 2025 Wikimedia traffic shift is surfaced but not removable;
- trust scores are heuristic;
- free-text model interpretation can still overreach.

## License

MIT.

Wikipedia pageview data is CC0.
