#!/usr/bin/env python3
"""Experiment 03 — why does everything look like it fell by half?

exp01 showed -53%..-60% YoY with 0/12 months up for three unrelated articles
in three editions. Hypothesis: a platform-level regime change in 2025 (bot
reclassification out of `user`, AI answer boxes) rather than topical decline.
This probe separates the two with data:

  S1  monthly `user` vs `all-agents` per article  -> if all-agents is flat while
      user halves, it is reclassification, not humans leaving
  S2  monthly project totals (user, all-agents) per edition -> edition-wide shift
  S3  our share-of-project metric on the same articles -> does relative interest
      tell a different story than absolute views?
  S4  Polish resolution fallback: full-text search + which Wikidata item the
      Polish article is attached to (exp01: no plwiki sitelink on Q1666254)
  S5  does the API treat a lower-case title as the same page?
  S6  candidate items for 'learning English'

Run from the skill dir:  python experiments/exp03_regime_shift.py
Reuses experiments/cache_exp01 so exp01's data is not re-downloaded.
"""

from __future__ import annotations

import json
import sys
import traceback
from datetime import date
from pathlib import Path
from statistics import median

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from wiki_interest.aqs import AQSClient  # noqa: E402
from wiki_interest.cache import JsonCache  # noqa: E402
from wiki_interest.mediawiki import MediaWikiClient  # noqa: E402
from wiki_interest.metrics import compute_metrics  # noqa: E402
from wiki_interest.series import analysis_window, items_to_daily, monthly_from_items, daily_to_monthly  # noqa: E402
from wiki_interest.stats import year_over_year, trailing_sums, log_trend_annualized  # noqa: E402
from _recording import recording_get, timing_summary  # noqa: E402

cache = JsonCache(HERE / "cache_exp01")
aqs = AQSClient(cache=cache, http_get=recording_get)
mw = MediaWikiClient(cache=cache, http_get=recording_get)
WIN = analysis_window(24, date.today())
START, END = WIN.aqs()
ARTICLES = [("en", "Intermittent fasting"), ("uk", "Астрономія"), ("cs", "Přerušovaný půst")]
summary: dict = {"window": str(WIN), "steps": {}}


def step(name):
    def deco(fn):
        print(f"\n=== {name} ===")
        try:
            summary["steps"][name] = fn()
        except Exception as e:
            traceback.print_exc()
            summary["steps"][name] = {"error": repr(e)}

    return deco


def monthly_user_daily(lang, title):
    items = aqs.per_article(f"{lang}.wikipedia.org", title, START, END)  # cached from exp01
    return items_to_daily(items, WIN)


def table(label, rows):
    """rows: list of (month, *values). Prints compactly, 12 months per line."""
    print(f"  {label}")
    for half in (rows[:12], rows[12:]):
        print(
            "   "
            + "  ".join(
                f"{m[5:]}:{'/'.join(f'{v:,.0f}' if isinstance(v, (int, float)) else str(v) for v in vals)}"
                for m, *vals in half
            )
        )


def yoy_and_trend(vals):
    yoy = year_over_year(vals)
    ann = log_trend_annualized(trailing_sums(vals))[0] if len(vals) >= 13 else None
    return {"yoy": None if yoy is None else round(yoy, 3), "annualized_trend": None if ann is None else round(ann, 3)}


@step("S1 article: user vs all-agents by month")
def _():
    out = {}
    for lang, title in ARTICLES:
        user_m = daily_to_monthly(monthly_user_daily(lang, title).views, WIN.start)
        all_items = aqs.per_article(
            f"{lang}.wikipedia.org", title, START, END, granularity="monthly", agent="all-agents"
        )
        all_m = monthly_from_items(all_items, WIN)
        rows = []
        for u, a in zip(user_m, all_m):
            share = (u.views / a.views) if a.views else None
            rows.append((u.label, u.views, a.views, f"{share:.0%}" if share is not None else "-"))
        table(f"{lang}:{title}  (user / all-agents / user-share)", rows)
        out[f"{lang}:{title}"] = {
            "user": yoy_and_trend([u.views for u in user_m]),
            "all_agents": yoy_and_trend([a.views for a in all_m]),
            "user_share_first6_median": round(
                median(u.views / a.views for u, a in list(zip(user_m, all_m))[:6] if a.views), 3
            ),
            "user_share_last6_median": round(
                median(u.views / a.views for u, a in list(zip(user_m, all_m))[-6:] if a.views), 3
            ),
            "rows": rows,
        }
        print(f"   user YoY {out[f'{lang}:{title}']['user']}  all-agents YoY {out[f'{lang}:{title}']['all_agents']}")
    return out


@step("S2 edition totals: user vs all-agents by month")
def _():
    out = {}
    for lang in ("en", "uk", "cs", "pl"):
        proj = f"{lang}.wikipedia.org"
        user_m = monthly_from_items(aqs.aggregate(proj, START, END), WIN)
        all_m = monthly_from_items(aqs.aggregate(proj, START, END, agent="all-agents"), WIN)
        rows = [
            (u.label, u.views / 1e6, a.views / 1e6, f"{u.views / a.views:.0%}" if a.views else "-")
            for u, a in zip(user_m, all_m)
        ]
        table(f"{proj}  (user M / all-agents M / user-share)", rows)
        out[lang] = {
            "user": yoy_and_trend([u.views for u in user_m]),
            "all_agents": yoy_and_trend([a.views for a in all_m]),
            "rows": rows,
        }
        print(f"   user YoY {out[lang]['user']}  all-agents YoY {out[lang]['all_agents']}")
    return out


@step("S3 share-of-project vs absolute for the same articles")
def _():
    out = {}
    for lang, title in ARTICLES:
        daily = monthly_user_daily(lang, title)
        proj = monthly_from_items(aqs.aggregate(f"{lang}.wikipedia.org", START, END), WIN)
        m = compute_metrics(daily, WIN, project_monthly=proj, first_revision=None)
        s = m["share_of_project"] or {}
        out[f"{lang}:{title}"] = {
            "absolute": {
                "verdict": m["verdict"],
                "yoy": round(m["growth"]["yoy"], 3),
                "annualized": round(m["growth"]["annualized_trend"], 3),
            },
            "relative": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.items()},
            "per_million_by_month": [(r["month"], r.get("per_million")) for r in m["monthly"]],
        }
        print(
            f"  {lang}:{title}: absolute verdict={m['verdict']} yoy={m['growth']['yoy']:+.0%} | "
            f"relative yoy={s.get('yoy', float('nan')):+.0%} annualized={s.get('annualized_trend', float('nan')):+.0%} "
            f"first-half {s.get('first_half_mean', 0):.1f} -> second-half {s.get('second_half_mean', 0):.1f} per million"
        )
    return out


@step("S4 Polish resolution fallback")
def _():
    out = {
        "search_pl": mw.search_articles("pl", "post przerywany", limit=5),
        "search_pl_en_term": mw.search_articles("pl", "intermittent fasting", limit=5),
    }
    print("  search 'post przerywany' ->", [r["title"] for r in out["search_pl"]])
    print("  search 'intermittent fasting' ->", [r["title"] for r in out["search_pl_en_term"]])
    for guess in ["Post przerywany", "Dieta IF", "Intermittent fasting"]:
        res = mw.resolve_title("pl", guess)
        item = mw.wikidata_item_of("pl", guess) if res["exists"] else None
        out[guess] = {**res, "wikidata_item": item}
        print(
            f"  {guess!r}: exists={res['exists']} canonical={res['title']!r} redirected_from={res['redirected_from']} item={item}"
        )
        if res["exists"]:
            d = monthly_user_daily("pl", res["title"])
            first = mw.first_revision("pl", res["title"])
            m = compute_metrics(
                d,
                WIN,
                project_monthly=monthly_from_items(aqs.aggregate("pl.wikipedia.org", START, END), WIN),
                first_revision=first,
            )
            out[guess]["metrics"] = {
                "total": d.total,
                "first_revision": str(first),
                "verdict": m["verdict"],
                "yoy": m["growth"].get("yoy"),
                "months_used": m["months_used"],
                "median_monthly": m["volume"]["median_monthly"],
            }
            print(f"     total={d.total:,} created={first} verdict={m['verdict']} yoy={m['growth'].get('yoy')}")
    return out


@step("S5 lower-case title handling")
def _():
    norm = aqs.per_article("en.wikipedia.org", "Intermittent fasting", START, END)
    url = AQSClient.per_article_url("en.wikipedia.org", "Intermittent fasting", START, END).replace(
        "Intermittent_fasting", "intermittent_fasting"
    )
    lower = aqs.get_json(url) or {}
    li = lower.get("items", [])
    out = {
        "normalized_total": sum(i["views"] for i in norm),
        "lowercase_total": sum(i["views"] for i in li),
        "lowercase_items": len(li),
        "lowercase_article_field": li[0].get("article") if li else None,
    }
    print(" ", out)
    return out


@step("S6 'learning English' candidate concepts")
def _():
    langs = ["pl", "cs", "uk", "de", "es", "pt", "tr", "vi", "ja", "hu", "ro", "en"]
    qids = {
        "Q1860": "English language",
        "Q130192": "English as a second or foreign language",
        "Q1046541": "Teaching English as a second language (check)",
        "Q186640": "language acquisition (check)",
    }
    out = {}
    ents = mw.enrich(
        [
            __import__("wiki_interest.mediawiki", fromlist=["Candidate"]).Candidate(q, lbl, "")
            for q, lbl in qids.items()
        ],
        langs,
    )
    for c in ents:
        out[c.qid] = {"label": c.label, "has": sorted(c.sitelinks), "titles": c.sitelinks}
        print(f"  {c.qid} {c.label}: has {','.join(sorted(c.sitelinks)) or '-'}")
    return out


summary["timing"] = timing_summary()
(HERE / "exp03_summary.json").write_text(
    json.dumps(summary, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
)
print(f"\nSaved experiments/exp03_summary.json  (network requests this run: {summary['timing']['requests']})")
