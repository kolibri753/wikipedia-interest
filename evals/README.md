# Evals

Two layers, deliberately different:

1. **Deterministic tests** (`tests/`, run by `pytest`) cover the code: statistics
   against hand values and SciPy, the API clients against a fake Wikimedia
   world, the judgement layer against synthetic golden cases, and the
   assignment's example queries against **recorded real responses**
   (`fixtures/cache`, replayed with `--offline --today 2026-09-23`).
2. **Agent evals** (`harness.py` + `evals.json`) cover the skill as an agent
   uses it: SKILL.md is the system prompt, the model has `run_script` and
   `write_file`, the harness really executes `analyze.py` / `report.py`, and
   each transcript is graded with programmatic checks.

## Running the agent evals (zero cost)

```bash
GEMINI_API_KEY=... python evals/harness.py gemini            # Gemini free tier, 5 req/min, daily quota -> ~5 minutes if quota allows
GROQ_API_KEY=...   python evals/harness.py groq
MODEL=<id> RPM=<n> ... python evals/harness.py <provider>    # override model / rate
python evals/harness.py gemini --only english_report          # one scenario
```

Data requests go through `fixtures/cache`; anything not yet recorded is fetched
once and cached, so the first run may touch Wikimedia, later runs do not.
`--today 2026-09-23` is added to every `analyze.py` call so windows and URLs
stay stable.

Results land in `results/<provider>-<model>-<timestamp>/`: `summary.md`
(table + failed checks), `summary.json`, and one `transcript_<id>.json` per
scenario with every message, tool call and tool output.

Each scenario has a `status`: `completed` (graded); `provider_error` (5xx or
per-minute 429s still failing after retries); `context_limit` (one request
exceeded the provider's per-request cap — Groq's free tier: 8,000 tokens);
`quota_exhausted` (a daily/billing quota — the run stops, remaining scenarios
are `not_run`). Aborted scenarios keep their partial transcript, are **not**
counted as failures, and can be re-run with `--only <id>`.

Pacing understands the two limits free tiers enforce: requests per minute
(`RPM=`) and tokens per minute (`TPM=`, a sliding 60-second budget; Groq
defaults to 8,000). Per-minute 429s are retried after the delay the provider
asks for. `MAX_REQUEST_TOKENS=` overrides the per-request cap.

The harness manages context the way a real agent runtime does: the last two
script outputs are sent in full, older ones become small valid-JSON stubs that
keep the pointers (files, concept, per-language verdicts); if a provider cap
is still exceeded, older outputs are stubbed and the latest truncated inside
its JSON envelope. The model also has `read_file` (capped at 6 KB) — a live
Gemini run tried `python -c "json.load(...)"` to read analysis.json back.

## What the checks catch — and what they do not

The checks encode failure modes observed in the live probes:

- `no_unsupported_spike_causes`: an _external_ cause asserted for a spike or
  drop ("likely tied to the school year") without hedging it as something to
  verify. Causes the scripts measure (bot reclassification, redirects, spike
  days, edition-wide shifts) are allowed; so are "worth checking what changed".
- `no_unsupported_market_inference`: demand, receptiveness or willingness to
  pay read off pageviews as a finding ("indicating an audience receptive to a
  product"). Saying what to validate and how is fine.
- `numbers_grounded`: every percentage or large number in the answer appears
  in some tool output (within rounding). Catches invented figures.
- wrong-concept handling (`english_report`): the first resolution is a Voice of
  America programme; the model must notice and pin `--qid Q1860`.
- missing-language handling (`pl_cs_fasting`): Polish has no article; that
  must be reported, not turned into a growth claim.
- the structured `ambiguous_topic` stop (`ambiguous_mercury`) and a refinement
  turn (`astronomy_refinement`).

They do **not** judge writing quality, tone or whether the recommendation is
wise. Read the transcripts; the checks are a regression net, not a verdict.

## Trigger eval

`python evals/trigger_eval.py <provider>` checks whether a cheap model picks this
skill from its `description` alone, against three plausible distractor skills, for
14 requests that should trigger it and 14 that should not. Tune the description
on the misses it reports.

## Recording fixtures

`python evals/record_fixtures.py` deletes `fixtures/cache` and re-records the
example queries online. Do this after any change that alters URL construction;
the offline tests tell you when it is needed.

## Deterministic test count

`pytest -q` reports **99 passed, 2 skipped** on a machine without SciPy: the two
skipped tests compare our stdlib Theil–Sen and Mann–Kendall against SciPy as an
independent oracle and run only where SciPy happens to be installed. SciPy is
deliberately not a dependency of the skill. On a fresh clone without recorded
fixtures, the three offline replay tests skip as well.

## Results observed so far (real runs, Sept 2026)

| model                              | scenario                 | result                                                       | what it showed                                                                                                                                                                                                                                                                                               |
| ---------------------------------- | ------------------------ | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Gemini 3.6 Flash (free)            | pl_cs_fasting            | 9/10 → 11/11 after grader fix                                | reported the missing Polish article as "0 views" (now a check + SKILL.md rule)                                                                                                                                                                                                                               |
| Gemini 3.6 Flash                   | uk_astronomy             | 6/8                                                          | invented a school-term cause for a Sept→Oct drop (caught)                                                                                                                                                                                                                                                    |
| GPT-OSS-120B (Groq free)           | english_report           | 7/10 → 8/10 → 9/11                                           | accepted the VOA-related Q2731224 concept as "learning English" in two of three runs, once _after_ naming it in its own notes; misreported 124.5 per million as ~48 once → the `comparison` block computes rankings; the P31/volume gates now make the wrong concept unreachable without an explicit `--qid` |
| GPT-OSS-120B                       | ambiguous_mercury        | 4/5 → 5/5 after grader fix                                   | handled the ambiguity well; exposed `--qid` needing `--topic` (fixed)                                                                                                                                                                                                                                        |
| GPT-OSS-120B                       | astronomy_refinement     | aborted at 8,850 tokens → 6/7 (grader false positives fixed) | compaction + smaller summaries brought the refinement flow to ~5.4–6k tokens; relayed the computed ranking correctly                                                                                                                                                                                         |
| Gemini 3.6 Flash                   | english_report (phase 8) | 11/11                                                        | corrected VOA → Q1860, hit `no_articles` for ESL correctly, used `read_file`, quoted the comparison statement verbatim                                                                                                                                                                                       |
| GPT-OSS-120B                       | english_report (phase 9) | 11/11, then 5/11                                             | after the `confirm_concept` stop it chose `--qid Q1860` unprompted; a later run looped on `read_file` until the step limit — variance                                                                                                                                                                        |
| GPT-OSS-120B                       | trigger eval             | 14/14, 14/14                                                 | description triggers on all should-cases and none of the should-not cases (author-written set; see caveat in README)                                                                                                                                                                                         |
| Nemotron 3 Ultra (OpenRouter free) | full suite               | 43/44                                                        | third model; the single miss was the missing Polish article rendered as "0 views"                                                                                                                                                                                                                            |
| Nemotron 3 Ultra                   | trigger eval             | 14/14, 14/14                                                 |                                                                                                                                                                                                                                                                                                              |
| Gemini 3.6 Flash                   | full suite (phase 9)     | 20/21 over 2 completed; 3 aborted by daily quota             | the miss: "spikes tied to the school/academic calendar"                                                                                                                                                                                                                                                      |
