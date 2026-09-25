#!/usr/bin/env python3
"""Experiment 01 — live contact with the Wikimedia APIs.

Run from the skill directory:   python experiments/exp01_live_probe.py
Needs only the standard library and network access. Takes ~1–3 minutes.

What it answers (each maps to a risk in the Phase 1 report):
  R6  keyless AQS, request timing, 404 semantics, title case handling
  R2  can Wikidata map the example topics to pl/cs/uk/... titles? which are missing?
  R5  how much traffic hides on redirect titles? (decides whether redirect
      summing is worth its extra requests)
  R7  what do the real example series look like through our metrics?

Every raw HTTP response is recorded under experiments/recorded/ so tests and
the eval harness can replay them offline later.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from wiki_interest.aqs import AQSClient  # noqa: E402
from wiki_interest.cache import JsonCache  # noqa: E402
from wiki_interest.mediawiki import MediaWikiClient  # noqa: E402
from wiki_interest.metrics import compute_metrics  # noqa: E402
from wiki_interest.series import analysis_window, items_to_daily, merge_daily, monthly_from_items  # noqa: E402
from wiki_interest.trust import assess_trust  # noqa: E402

from _recording import recording_get, timing_summary  # noqa: E402

# Fresh cache directory so this run really hits the network.
cache = JsonCache(HERE / "cache_exp01", enabled=True)
aqs = AQSClient(http_get=recording_get, cache=cache)
mw = MediaWikiClient(http_get=recording_get, cache=cache)

TODAY = date.today()
WIN = analysis_window(24, TODAY)
START, END = WIN.aqs()
summary: dict = {"today": TODAY.isoformat(), "window": str(WIN), "steps": {}}


def step(name):
    def deco(fn):
        print(f"\n=== {name} ===")
        try:
            summary["steps"][name] = fn()
        except Exception as e:  # keep going; a failure is a finding
            traceback.print_exc()
            summary["steps"][name] = {"error": repr(e)}

    return deco


def fetch_article(lang: str, title: str, include_redirects: bool):
    """Article + (optionally) its redirects, returned as one DailySeries plus diagnostics."""
    project = f"{lang}.wikipedia.org"
    items = aqs.per_article(project, title, START, END)
    main = items_to_daily(items, WIN)
    info = {"title": title, "items": len(items), "total": main.total, "redirects": []}
    series = [main]
    if include_redirects:
        for rt in mw.redirects_to(lang, title, cap=40):
            r_items = aqs.per_article(project, rt, START, END)
            rs = items_to_daily(r_items, WIN)
            info["redirects"].append({"title": rt, "total": rs.total})
            series.append(rs)
    merged = merge_daily(series)
    info["total_with_redirects"] = merged.total
    info["redirect_share"] = round(1 - main.total / merged.total, 3) if merged.total else None
    return merged, info


@step("R6 basic AQS behaviour")
def _():
    out = {}
    items = aqs.per_article("en.wikipedia.org", "Intermittent fasting", START, END)
    out["en_items"] = len(items)
    out["en_first_item"] = items[0] if items else None
    out["expected_days"] = (WIN.end - WIN.start).days + 1
    # Title case: does the API auto-capitalise? (our normaliser does; test the raw lower-case form)
    raw_url = AQSClient.per_article_url("en.wikipedia.org", "intermittent fasting", START, END)
    lower_url = raw_url.replace("Intermittent_fasting", "intermittent_fasting")
    out["lowercase_title_status"] = recording_get(lower_url, {"User-Agent": aqs.user_agent}).status
    out["nonsense_title_items"] = len(aqs.per_article("en.wikipedia.org", "Xyzzy_no_such_page_9f8e7d", START, END))
    out["monthly_direct_items"] = len(
        aqs.per_article("en.wikipedia.org", "Intermittent fasting", START, END, granularity="monthly")
    )
    return out


@step("R2 topic -> titles via Wikidata")
def _():
    langs = ["pl", "cs", "uk", "de", "en", "es", "pt", "tr", "vi", "ja", "hu", "ro"]
    out = {}
    for topic in ["intermittent fasting", "astronomy", "English as a second or foreign language", "learning English"]:
        cands = mw.enrich(mw.search_items(topic, "en", limit=5), langs)
        out[topic] = [
            {
                "qid": c.qid,
                "label": c.label,
                "desc": c.description[:60],
                "has": sorted(c.sitelinks),
                "missing": sorted(set(langs) - set(c.sitelinks)),
                "disambig": c.is_disambiguation,
            }
            for c in cands
        ]
        for c in out[topic][:3]:
            print(
                f"  {topic!r} -> {c['qid']} {c['label']!r} ({c['desc']}) has={','.join(c['has'])} "
                f"missing={','.join(c['missing']) or '-'}{' DISAMBIG' if c['disambig'] else ''}"
            )
    return out


@step("R5 redirect share + real metrics (examples)")
def _():
    out = {}
    fasting = mw.enrich(mw.search_items("intermittent fasting", "en", 3), ["pl", "cs", "en"])[0].sitelinks
    astro = mw.enrich(mw.search_items("astronomy", "en", 3), ["uk"])[0].sitelinks
    targets = [("pl", fasting.get("pl")), ("cs", fasting.get("cs")), ("uk", astro.get("uk")), ("en", fasting.get("en"))]
    for lang, title in targets:
        if not title:
            out[lang] = {"error": "no sitelink"}
            continue
        merged, info = fetch_article(lang, title, include_redirects=True)
        proj = monthly_from_items(aqs.aggregate(f"{lang}.wikipedia.org", START, END), WIN)
        first = mw.first_revision(lang, title)
        m = compute_metrics(merged, WIN, project_monthly=proj, first_revision=first)
        t = assess_trust(m)
        info.update(
            {
                "first_revision": first.isoformat() if first else None,
                "verdict": m["verdict"],
                "magnitude": m["magnitude"],
                "trust": t.score,
                "yoy": m["growth"].get("yoy"),
                "annualized": m["growth"].get("annualized_trend"),
                "months_up": (m["trend_test"] or {}).get("months_up"),
                "p": (m["trend_test"] or {}).get("p_value"),
                "spike_share": m["spikes"]["share_of_total"],
                "top_spike": m["spikes"]["top"][:1],
                "per_million_median": (m["share_of_project"] or {}).get("median_per_million"),
                "reasons": t.reasons,
            }
        )
        out[lang] = info
        print(
            f"{lang}:{title}  total={info['total']:,} +redirects={info['total_with_redirects']:,} "
            f"(share {info['redirect_share']})  verdict={m['verdict']} trust={t.score}"
        )
        for r in t.reasons:
            print("   -", r)
    return out


@step("timing")
def _():
    return timing_summary()


(HERE / "exp01_summary.json").write_text(
    json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
)
print("\nSaved experiments/exp01_summary.json; raw responses recorded in experiments/recorded/")
print("Paste exp01_summary.json (or the console output) back into the conversation.")
