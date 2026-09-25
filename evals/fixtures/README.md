# Recorded fixtures

`cache/` is the skill's response cache (keyed by URL hash) captured from real
runs of the assignment's example queries on 2026-09-23. It lets
`tests/test_examples_offline.py` and `analyze.py --offline --today 2026-09-23`
run against real Wikimedia data with no network.

Because entries are keyed by the exact URL, they go stale whenever URL
construction changes (new parameter, different date range). Re-record with:

    python evals/record_fixtures.py

That deletes the old cache and replays the four example commands online with
`--today 2026-09-23`, so the window matches the tests whichever day you run it.
Offline runs must pass the same `--today`, otherwise the window (and the URLs)
move with the calendar.
