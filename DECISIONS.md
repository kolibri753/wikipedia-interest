# Decision trail

This is the development history of the project: what I tried, what failed, what the data or model runs showed, and what I changed because of it.

Some early decisions were later revised. I kept those parts because the wrong assumptions are often the most useful explanation for why the final design looks the way it does.

## D1. Python

TypeScript is my strongest language, but Python was a better fit here: matplotlib and reportlab work without extra infrastructure, the statistics could stay mostly in the standard library, and Python is common in existing Agent Skill examples.

It was slower for me at first, but the code stayed small and well tested.

## D2. Code computes, the model interprets

I considered exposing lots of small tools and letting the model sequence them.

I decided against that. Weak models are much better at interpreting a finished result than doing multi-step data work reliably.

So the skill has two main commands:

- `analyze.py` resolves the topic, fetches data, calculates metrics and creates charts;
- `report.py` turns the deterministic result plus short analyst notes into a PDF.

The model never needs raw daily data and never calculates the important numbers itself.

## D3. The first trend method was wrong

My first plan was Theil–Sen + Mann–Kendall directly on 24 monthly points.

A synthetic series with strong seasonality but **zero actual trend** came back as `declining` with trust 100.

So I changed the methodology:

- Theil–Sen runs on trailing-12-month totals;
- significance compares the same months across years;
- raw and despiked YoY are both kept;
- short histories fall back to a simpler method with lower confidence.

The failed seasonal case became a regression test.

## D4. Redirects count too

Wikipedia pageviews belong to titles, so redirects can have their own traffic.

I sum the main article and its redirects. This also helps when an article was renamed during the analysis window, because the old title normally becomes a redirect.

The output keeps `redirect_share` so I can see when redirects matter.

## D5. Wikidata maps concepts across languages

I use Wikidata sitelinks instead of independently searching every Wikipedia edition.

That gives me one concept and its corresponding article titles across languages.

Search is still useful as a fallback when an article is missing, but I do not silently substitute a nearby concept.

## D6. Raw pageviews are not comparable across languages

English Wikipedia and Ukrainian Wikipedia have completely different total traffic.

So language comparisons use article views per million views of the whole edition, while raw views are still shown separately.

This became especially important once I discovered the 2025 platform-wide traffic shift.

## D7. Model testing had to stay free

The project had a zero-cost constraint, so I built a small eval harness around free model APIs instead of relying on a paid evaluation platform.

The final harness supports Groq, Gemini and OpenRouter.

## D8. I dropped the text-as-image idea

I considered compressing instructions into images to save context.

It did not make sense: image tokens were not clearly cheaper, OCR-style compression depends on specialized models, and not every cheap model supports vision.

Plain text plus progressive disclosure was simpler.

## D9. Everything lives inside the skill

The repository contains the skill, scripts, implementation, references, tests, evals and experiments together.

That makes it possible to clone the repository and understand or reproduce the work without another project around it.

## D10. Topic resolution cannot be left entirely to search

The first live probes showed several problems:

- Polish Wikipedia has no intermittent-fasting article;
- `"learning English"` resolves to a Voice of America concept;
- Wikidata search returns clinical trials and scholarly articles alongside general topics.

So the resolver filters obviously unsuitable candidates, exposes ambiguity instead of guessing, and lets the model pin a Wikidata QID or explicit article when needed.

## D11. I initially blamed the 2025 decline on the platform

Several unrelated examples were all falling by roughly 50%, so my first hypothesis was that a Wikimedia traffic-classification change was dominating the signal.

I added:

- whole-edition traffic;
- user vs all-agent traffic;
- absolute vs relative trends.

The hypothesis turned out to be only partly right.

Edition-wide human traffic did fall, and the human share changed significantly in some editions. But the example articles were also declining strongly relative to their editions and in all-agent traffic.

So the final design keeps both absolute and relative trends plus the agent-mix diagnostic.

My first explanation was too strong. The useful part was having a deterministic way to test it.

## D12. Provider messages must be replayed exactly

Gemini rejected reconstructed assistant tool-call messages because provider-specific metadata such as `thought_signature` was missing.

The harness now stores and sends back the exact assistant message object returned by the provider.

## D13. UTF-8 must be explicit

Windows exposed several assumptions that did not show up elsewhere.

File writes using the system encoding failed on Unicode titles, so all file I/O now explicitly uses UTF-8.

## D14. The data says when, not why

Gemini once explained a spike as probably caused by New Year health coverage or social media.

The pageview data did not contain that information.

So the skill explicitly separates measured facts from possible explanations, and the evals check for unsupported causal claims.

## D15. Missing is not zero

Polish Wikipedia has no article for intermittent fasting.

That is not the same thing as an article receiving zero views.

The analysis continues with the languages that do exist and reports missing editions separately, with search suggestions.

## D16. Free-tier limits became part of the harness design

Gemini started returning 429s after the free-tier request limit, and transient provider errors also appeared.

The harness therefore runs scenarios serially, respects rate limits and retries temporary provider failures.

## D17. My first volatility metric measured trend

English intermittent fasting was declining smoothly but got labelled highly volatile.

The coefficient of variation was reacting to the overall decline instead of actual month-to-month instability.

I replaced it with the standard deviation of log month-over-month changes on the despiked series.

## D18. Spike detection needs data before the window

A spike near the very start of the analysis period can become part of its own rolling baseline.

I added a 31-day fetch pad before the requested window and trim it away after spike detection.

The real Ukrainian September case that led me there turned out to be a longer seasonal elevation, not a spike, so my first diagnosis was wrong — but the edge-case bug itself was real.

## D19. Trust reasons and context are different things

Originally too many observations were mixed into the trust deductions.

I split them into:

- `reasons` — actually reduce the score;
- `notes` — important context;
- `strengths` — evidence supporting confidence.

For example, a platform-wide traffic shift is useful context but should not automatically reduce trust.

## D20. Output folders include the pinned concept

Two `"learning English"` analyses using different Wikidata concepts overwrote each other.

Output paths now include the QID when a concept is pinned.

## D21. PDF fonts come from matplotlib

ReportLab's default fonts could not render Czech and Cyrillic correctly.

Matplotlib already ships DejaVu Sans, so the report uses that instead of bundling another font dependency.

## D22. Recorded API responses became fixtures

I use the same URL-keyed cache mechanism for deterministic offline fixtures.

The assignment examples are recorded with a fixed date and replayed with `--offline`.

Later, changes to API URLs immediately broke the replay. That was actually useful: it proved that the fixture corresponds to the exact request the current implementation makes.

`record_fixtures.py` became the one-command way to refresh them.

## D23. Tests cannot depend on PYTHONPATH accidents

A test helper import worked in one environment because `tests/` happened to be on `PYTHONPATH`, but failed in the real project layout.

The tests now import through the `tests` package explicitly.

## D24. Console output must also be UTF-8

`analyze.py` crashed on Windows while printing Czech titles under cp1252.

The CLI entry points now reconfigure stdout to UTF-8 instead of requiring a special shell setting.

## D25. Wikidata labels can change; QIDs are identity

A re-recorded response changed Q1860's label from `"English language"` to `"English"`.

The concept had not changed — only the text had.

Tests now rely on the QID for identity and only require a label to exist.

## D26. Measured and attributed claims stay separate

An early report footer stated that AI answer engines reduced Wikipedia traffic.

The project can measure the decline and the traffic-classification change, but it cannot prove the cause.

So measured facts are stated directly; outside explanations are explicitly attributed.

## D27. Agent evals execute the real skill

I did not want an eval that only checked prompt text.

The harness gives the model the actual skill instructions and tools, runs the real analysis/report scripts, and grades the resulting transcript.

The scenarios cover the assignment examples, ambiguity and a refinement turn.

## D28. The grader needed its own tests

The first grader produced false positives:

- `40/100` made `100` look invented;
- `16 366` was parsed incorrectly;
- other real transcript phrasing exposed more edge cases.

Every grader bug became a regression test.

The grader is only a safety net for known failure modes, not proof that an answer is good.

## D29. Context size is part of the product

A refinement request pushed GPT-OSS beyond its 8k-token limit.

I reduced the compact analysis output from roughly 2.3k to 1.4k tokens for three languages.

The harness also replaces older script outputs with small JSON stubs while keeping the recent results and file pointers available.

## D30. Provider failures are not model failures

A Gemini 503 and a Groq context error initially looked like failed model scenarios.

That distorted the eval result.

The harness now records separate statuses for provider errors, context limits and quota exhaustion, preserves partial transcripts, and does not count aborted scenarios as model failures.

## D31. The wrong-concept failure was more serious than expected

GPT-OSS accepted:

`Learning English — simplified English in Voice of America`

and produced a polished report from it.

Other runs also showed models occasionally:

- dropping the attention-vs-demand caveat;
- turning a missing article into `0 views`.

At first I responded with stronger warnings and SKILL.md instructions.

That helped, but did not solve the underlying semantic problem.

## D32. Model mistakes improved the CLI

GPT-OSS tried using `--qid` without `--topic` and got an unhelpful argparse error.

That was a reasonable thing for an agent to try.

So `--topic` became optional when a QID is already provided, and CLI argument errors became structured and readable.

## D33. Ranking languages belongs in code

One model had Ukrainian relative attention of `124.45` in its context but reported roughly `48`.

The numbers were correct; the interpretation was wrong.

So the analysis now computes the rankings and a comparison sentence itself. The model only needs to explain it.

## D34. Text grading needs careful parsing

More real transcripts exposed grader mistakes:

- `median` matched `media`;
- sentence splitting swallowed unrelated text;
- Markdown changed token boundaries.

The grader now uses word boundaries and better sentence splitting, with the real failures kept as tests.

## D35. Market inference needed its own check

A model wrote that a Wikipedia audience appeared `"receptive"` to a language-learning product.

That was not a causal error, but it was still a claim the pageview data could not support.

So I added a separate check for unsupported demand, willingness-to-pay and market-receptiveness claims.

## D36. Tokens per minute mattered more than requests per minute

Groq runs were still failing even when requests were far below the RPM limit.

The real bottleneck was the 8k tokens-per-minute limit.

The client now tracks a sliding token budget, waits before exceeding it, and distinguishes TPM limits from true context-size failures and daily quota exhaustion.

## D37. Context compaction can remove information the agent still needs

My first compaction logic removed an analysis output too aggressively.

Gemini then tried to execute Python to read `analysis.json`, failed, and re-ran the analysis just to recover numbers it had already produced.

The harness now keeps the last two script outputs when possible and exposes a controlled `read_file` tool.

## D38. SciPy stays optional

Test counts differed between environments because SciPy was installed in one and not another.

The two SciPy tests only verify my statistics against an independent implementation.

They stay optional instead of becoming a runtime dependency just to make the test count identical everywhere.

## D39. Prompt wording was not enough, so semantic safety moved into code

The strongest example was still `"learning English"`.

GPT-OSS could name the wrong VOA concept in its own notes and then continue building the report anyway.

I added two deterministic safeguards:

1. known specific-entity classes are excluded from automatic resolution;
2. if an automatically resolved concept has under 100 views/month in every available language, analysis stops with `confirm_concept` before producing a result.

An explicit `--qid` still lets the user intentionally analyze a tiny concept.

## D40. The grader needed another round

More transcript examples found more edge cases:

- `"short-term"` matched the bare word `term`;
- rounded values such as `1 200` were considered ungrounded;
- useful bounds such as `<650` were rejected.

The fixes became tests too.

At this point the grader itself had an evaluation loop, which turned out to be necessary.

## D41. The skill description needs an eval too

Agents decide whether to load a skill from its name and description before they read the body.

So `trigger_eval.py` tests the description against:

- 14 requests that should trigger the skill;
- 14 that should not;
- three plausible distractor skills.

The current tested models got all 28 decisions right, although the set is author-written and therefore only a basic precision check.

## D42. I needed a definition of done

The project could keep growing indefinitely.

For this scope, I consider it done when:

- the assignment examples work end to end;
- arbitrary topics/languages use the same path;
- deterministic calculations are tested;
- real-data examples replay offline;
- the skill has been exercised through several cheap models;
- known model failures are documented rather than hidden.

Further ideas stay as future work.

## D43. The real `"learning English"` case taught me why both gates matter

On real Wikidata, Q2731224 is classified as:

- `controlled language`;
- `plain English`.

Those are concept-like classes, not obviously a radio programme.

So the P31-specific-entity gate did **not** catch the real case.

The volume gate did: the articles were only around 8–24 views/month, so analysis stopped with `confirm_concept`.

After that stop, GPT-OSS selected Q1860 itself and completed the intended report.

I deliberately did not blacklist `controlled language` or `plain English`, because that would also block legitimate topics.

## D44. A clean clone should reproduce the project

The final repository includes:

- `.env.example`;
- a tiny built-in `.env` loader;
- recorded Wikimedia fixtures;
- selected eval evidence;
- experiment evidence;
- deterministic tests.

The actual skill needs no model API key.

Groq, Gemini and OpenRouter keys are only needed if someone wants to run the optional agent evals.

`.env`, generated reports and local caches stay outside Git.
