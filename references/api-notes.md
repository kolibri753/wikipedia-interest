# Wikimedia API notes (behaviour verified live in experiments/exp01, exp03)

Read this when a request fails, returns odd data, or the user asks about limits.

## Pageviews (AQS) — https://wikimedia.org/api/rest_v1/metrics/pageviews/
- No API key. A descriptive `User-Agent` with contact info is mandatory
  (`src/wiki_interest/http.py`, `DEFAULT_USER_AGENT`); anonymous clients may be blocked.
- No fixed rate limit; the access policy asks for serial requests. The client
  waits ≥0.25 s between requests, retries 429/5xx with backoff and honours
  `Retry-After`. Measured: median 0.26 s per request, max 0.9 s.
- `per-article/{project}/{access}/{agent}/{title}/{granularity}/{start}/{end}`
  and `aggregate/{project}/{access}/{agent}/{granularity}/{start}00/{end}00`
  (aggregate timestamps need the hour). Data from 2015-07-01.
- **Titles are case-sensitive and keyed by string.** `intermittent_fasting`
  returns 200 with ~1 view; `Intermittent_fasting` returns the real series.
  Always normalise (first letter upper-case, spaces → underscores, percent-
  encode `/ ? % & +`). The client does this.
- 404 = no data for that title/period (misspelt title, page did not exist yet).
  The client returns an empty list; the analysis treats it as zero views.
- Days with zero views are omitted from `items`. Popular articles return all
  730 days for a 24-month window; small ones return fewer.
- Redirect titles have their own series; the target does not include them.
- Agent types: `user`, `spider` (self-declared bots), `automated` (heuristic bot
  detection, since April 2020; revised in 2025). `all-agents` = everything.
- Timestamps are UTC. Previous-day data appears around 05:00 UTC; the analysis
  uses complete months only.

## MediaWiki Action API — https://{lang}.wikipedia.org/w/api.php
- `prop=redirects&rdnamespace=0&rdlimit=max` lists redirects (paginated via
  `rdcontinue`; the client caps at 50).
- `prop=revisions&rvdir=newer&rvlimit=1` gives the first revision (creation date).
- `prop=pageprops&ppprop=wikibase_item` gives the Wikidata item of a page.
- `action=query&titles=X&redirects=1` normalises a title, follows redirects and
  reports `missing`.
- `list=search&srsearch=intitle:X` restricts full-text search to titles; plain
  full-text search is noisy (live: "post przerywany" → "Charlie Kirk").

## Wikidata — https://www.wikidata.org/w/api.php
- `wbsearchentities` returns items by label match, including scholarly articles
  and clinical trials with no Wikipedia articles; `resolve.py` filters those.
- `wbgetentities&props=sitelinks|claims&sitefilter=plwiki|cswiki` returns the
  article title per edition. A missing sitelink usually means no article in
  that language (verified for Polish / intermittent fasting), occasionally a
  data gap.
- Sitelinks may point to redirects (allowed since 2022); the client follows them.

## Caching
- Every successful response and every 404 is cached forever under
  `~/.cache/wikipedia-interest` (override with `WIKI_INTEREST_CACHE`), keyed by
  URL. Historical windows never change, so repeated and related requests cost
  nothing. `--no-cache` bypasses it, `--offline` forbids the network.
