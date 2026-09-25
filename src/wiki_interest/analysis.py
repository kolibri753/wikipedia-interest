"""Orchestrate one analysis: topic -> titles -> series -> metrics -> summary.

Two outputs, deliberately different in size:
- `analysis.json` (full): every monthly row, per-language metrics, resolution,
  parameters. The report script and charts read this.
- the stdout summary (compact): what a small model needs to interpret the
  result. No time series, no raw daily data.

Absolute vs relative (decision D11, revised after exp03):
- absolute  = human ('user') pageviews of the article(s)
- relative  = article views per million views of the whole edition
Both get a verdict with identical rules. The relative one is the honest basis
for comparing languages and for "is this topic gaining share of attention";
the absolute one answers "how many people".

Agent mix: Wikimedia's bot classifier changed in 2025 and pulled traffic out of
'user'. We fetch the article and edition at all-agents too and report how far
the human-only trend depends on classification (uk astronomy: -60% user vs
-44% all-agents; the edition's user share fell 61% -> 45%).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from statistics import median
from typing import Sequence

from . import metrics as M
from .aqs import AQSClient
from .mediawiki import MediaWikiClient
from .resolve import Resolution, resolve
from .series import DailySeries, MonthPoint, Window, analysis_window, items_to_daily, merge_daily, monthly_from_items
from .stats import year_over_year, log_trend_annualized
from .titles import normalize_title
from .trust import assess_trust, Trust

# Thresholds tuned on live data (experiments/exp03): uk.wikipedia's human share fell 61% -> 45%
# and the astronomy article's 82% -> 45%; user YoY -60% vs all-agents -44%. en.wikipedia dipped
# 70% -> 58% mid-2025 and recovered, which correctly does not trip the edition test.
SPIKE_PAD_DAYS = 31  # daily history fetched before the window so the first days have a baseline
LOW_VOLUME_CONCEPT = 100  # views/month; below this in every language, an auto-resolved concept must be confirmed
RECLASS_EDITION_DROP = 0.10  # edition user-share drop (median first 6 vs last 6 months)
RECLASS_ARTICLE_DROP = 0.15  # same, for the article itself
AGENT_GAP = 0.15  # |user YoY - all-agents YoY| beyond which the human-only figure is classification-sensitive


@dataclass
class ConfirmConcept(Exception):
    """Auto-resolved concept whose articles are all tiny: stop before producing an
    analysis a weak model could turn into a polished report about the wrong thing."""

    def __init__(
        self,
        qid: str | None,
        label: str | None,
        description: str | None,
        volumes: dict[str, float],
        instance_of: dict[str, str] | None = None,
    ):
        super().__init__(f"all articles for {qid} have under {LOW_VOLUME_CONCEPT} views/month")
        self.qid, self.label, self.description, self.volumes = qid, label, description, volumes
        self.instance_of = instance_of or {}  # class QID -> label


@dataclass
class Params:
    topic: str
    langs: list[str]
    months: int = 24
    agent: str = "user"
    access: str = "all-access"
    include_redirects: bool = True
    redirect_cap: int = 50
    qid: str | None = None
    articles: dict[str, str] = field(default_factory=dict)
    search_lang: str = "en"
    accept_first: bool = False
    today: date | None = None

    def window(self) -> Window:
        return analysis_window(self.months, self.today or date.today())


def fetch_article_series(
    aqs: AQSClient, mw: MediaWikiClient, lang: str, title: str, win: Window, p: Params
) -> tuple[DailySeries, dict]:
    """Daily series for the article plus its redirects, starting SPIKE_PAD_DAYS
    before the window (one request either way; the pad only feeds the spike
    baseline and is trimmed by compute_metrics). Redirect totals are reported
    for the window itself."""
    project = f"{lang}.wikipedia.org"
    ext = Window(win.start - timedelta(days=SPIKE_PAD_DAYS), win.end)
    start, end = ext.aqs()
    pad = SPIKE_PAD_DAYS
    main = items_to_daily(aqs.per_article(project, title, start, end, agent=p.agent, access=p.access), ext)
    series, redirects, found = [main], [], 0
    if p.include_redirects:
        for rt in mw.redirects_to(lang, title, cap=p.redirect_cap):
            found += 1
            rs = items_to_daily(aqs.per_article(project, rt, start, end, agent=p.agent, access=p.access), ext)
            in_window = sum(rs.views[pad:])
            if in_window:
                redirects.append({"title": rt, "views": in_window})
            series.append(rs)
    merged = merge_daily(series)
    redirects.sort(key=lambda r: -r["views"])
    return merged, {"found": found, "with_views": redirects}


def _relative(
    article_monthly_raw: Sequence[MonthPoint],
    article_monthly_desp: Sequence[float],
    edition_monthly: Sequence[MonthPoint],
    months_used: int,
) -> dict | None:
    ed = {(m.year, m.month): m.views for m in edition_monthly}
    pm_raw, pm_desp = [], []
    for mp, dv in zip(article_monthly_raw, article_monthly_desp):
        denom = ed.get((mp.year, mp.month))
        if not denom:
            return None
        pm_raw.append(mp.views / denom * 1e6)
        pm_desp.append(dv / denom * 1e6)
    pm_raw, pm_desp = pm_raw[-months_used:], pm_desp[-months_used:]
    t = M.series_trend(pm_raw, pm_desp)
    half = len(pm_raw) // 2
    return {
        "unit": "article views per million edition views (same agent filter)",
        **t,
        "median_per_million": median(pm_raw) if pm_raw else None,
        "first_half_mean": sum(pm_raw[:half]) / half if half else None,
        "second_half_mean": sum(pm_raw[half:]) / (len(pm_raw) - half) if pm_raw else None,
        "per_million": [round(v, 3) for v in pm_raw],
    }


def _change(vals: Sequence[float]) -> tuple[float | None, str]:
    """YoY when two full years exist, otherwise the annualized robust trend."""
    if len(vals) >= 24:
        return year_over_year(vals), "yoy"
    if len(vals) >= M.MIN_MONTHS_FOR_TREND:
        return log_trend_annualized(list(vals))[0], "annualized_trend"
    return None, "n/a"


def _agent_mix(
    article_user_m: Sequence[MonthPoint],
    article_all_m: Sequence[MonthPoint],
    edition_user_m: Sequence[MonthPoint],
    edition_all_m: Sequence[MonthPoint],
    months_used: int,
) -> dict:
    def share(u: Sequence[MonthPoint], a: Sequence[MonthPoint]) -> list[float | None]:
        return [(x.views / y.views) if y.views else None for x, y in zip(u, a)]

    def med(vals):
        vals = [v for v in vals if v is not None]
        return median(vals) if vals else None

    # Only the months the article existed for; the edition is always complete.
    art_user = [m.views for m in article_user_m][-months_used:] if months_used else []
    art_all = [m.views for m in article_all_m][-months_used:] if months_used else []
    ed_share = share(edition_user_m, edition_all_m)
    art_share = [(u / a) if a else None for u, a in zip(art_user, art_all)]
    ed_first, ed_last = med(ed_share[:6]), med(ed_share[-6:])
    ar_first, ar_last = med(art_share[:6]), med(art_share[-6:])
    user_yoy, basis = _change(art_user)
    all_yoy, _ = _change(art_all)
    out = {
        "edition_user_share_first6": ed_first,
        "edition_user_share_last6": ed_last,
        "article_user_share_first6": ar_first,
        "article_user_share_last6": ar_last,
        "change_basis": basis,
        "article_user_change": user_yoy,
        "article_all_agents_change": all_yoy,
        "edition_user_yoy": year_over_year([m.views for m in edition_user_m]),
        "edition_all_agents_yoy": year_over_year([m.views for m in edition_all_m]),
        "reclassification_suspected": bool(
            (ed_first is not None and ed_last is not None and ed_first - ed_last >= RECLASS_EDITION_DROP)
            or (ar_first is not None and ar_last is not None and ar_first - ar_last >= RECLASS_ARTICLE_DROP)
        ),
        "classification_sensitive": bool(
            user_yoy is not None
            and all_yoy is not None
            and (abs(user_yoy - all_yoy) >= AGENT_GAP or (user_yoy > 0) != (all_yoy > 0))
        ),
    }
    return out


def _trust_with_context(m: dict, rel: dict | None, mix: dict | None) -> Trust:
    t = assess_trust(m)
    if mix:
        if mix["classification_sensitive"]:
            t.score = max(0, t.score - 10)
            basis = "year-over-year" if mix["change_basis"] == "yoy" else "annualized trend"
            t.reasons.append(
                f"The human-only trend depends on bot classification: {mix['article_user_change']:+.0%} {basis} for 'user' traffic "
                f"vs {mix['article_all_agents_change']:+.0%} for all traffic."
            )
        if mix["reclassification_suspected"]:
            t.notes.append(
                f"Wikimedia's bot classifier changed inside the window: the share of traffic labelled human fell from "
                f"{mix['article_user_share_first6']:.0%} to {mix['article_user_share_last6']:.0%} for this article "
                f"({mix['edition_user_share_first6']:.0%} to {mix['edition_user_share_last6']:.0%} for the whole edition), so part of any "
                f"decline in human-labelled views is reclassification, not lost readers."
            )
    if rel and rel.get("verdict") not in (None, "insufficient_data"):
        if m["verdict"] != rel["verdict"]:
            t.notes.append(
                f"Absolute and relative verdicts differ ({m['verdict']} vs {rel['verdict']} relative to the whole edition): "
                f"edition-wide traffic moved, so judge share-of-attention, not raw views."
            )
        else:
            a, r = m["growth"].get("annualized_trend"), rel["growth"].get("annualized_trend")
            if a is not None and r is not None and abs(a - r) >= 0.10:
                t.notes.append(
                    f"The change relative to the whole edition ({r:+.0%}/yr) is smaller than the absolute change ({a:+.0%}/yr): "
                    f"part of the movement is edition-wide, not specific to this topic."
                )
    t.label = "high" if t.score >= 70 else "medium" if t.score >= 40 else "low"
    return t


def analyze(p: Params, aqs: AQSClient, mw: MediaWikiClient, out_dir: Path) -> dict:
    """Run everything; returns the full analysis dict (also written to out_dir/analysis.json)."""
    win = p.window()
    start, end = win.aqs()
    res: Resolution = resolve(
        mw, p.topic, p.langs, qid=p.qid, user_titles=p.articles, search_lang=p.search_lang, accept_first=p.accept_first
    )
    langs_out: dict[str, dict] = {}
    edition_cache: dict[str, tuple[list[MonthPoint], list[MonthPoint]]] = {}

    for lang, r in res.articles.items():
        project = f"{lang}.wikipedia.org"
        daily, rd = fetch_article_series(aqs, mw, lang, r.title, win, p)
        redirects = rd["with_views"]
        first_rev = mw.first_revision(lang, r.title)
        if project not in edition_cache:
            edition_cache[project] = (
                monthly_from_items(aqs.aggregate(project, start, end, agent=p.agent, access=p.access), win),
                monthly_from_items(aqs.aggregate(project, start, end, agent="all-agents", access=p.access), win),
            )
        ed_user_m, ed_all_m = edition_cache[project]

        m = M.compute_metrics(daily, win, project_monthly=ed_user_m, first_revision=first_rev)
        monthly_raw = [MonthPoint(int(r["month"][:4]), int(r["month"][5:7]), float(r["views"])) for r in m["monthly"]]
        monthly_desp = [float(r["despiked"]) for r in m["monthly"]]
        rel = (
            _relative(monthly_raw, monthly_desp, ed_user_m, m["months_used"])
            if m["months_used"] >= M.MIN_MONTHS_FOR_TREND
            else None
        )

        mix = None
        if p.agent == "user":
            art_all_m = monthly_from_items(
                aqs.per_article(
                    project, r.title, start, end, granularity="monthly", agent="all-agents", access=p.access
                ),
                win,
            )
            mix = _agent_mix(monthly_raw, art_all_m, ed_user_m, ed_all_m, m["months_used"])

        trust = _trust_with_context(m, rel, mix)
        redirect_total = sum(x["views"] for x in redirects)
        window_total = m["volume"]["total_views"]
        langs_out[lang] = {
            "article": r.title,
            "article_url": f"https://{project}/wiki/{normalize_title(r.title)}",
            "resolution": {"source": r.source, "wikidata_item": r.wikidata_item, "redirected_from": r.redirected_from},
            "redirects": {
                "included": p.include_redirects,
                "found": rd["found"],
                "count": len(redirects),
                "views": redirect_total,
                "share": (redirect_total / window_total) if window_total else 0.0,
                "top": redirects[:5],
            },
            **{
                k: m[k]
                for k in (
                    "window",
                    "months_used",
                    "partial_history",
                    "first_revision",
                    "volume",
                    "spikes",
                    "growth",
                    "trend_test",
                    "volatility",
                    "shape",
                    "verdict",
                    "magnitude",
                )
            },
            "relative": rel,
            "agent_mix": mix,
            "trust": trust.as_dict(),
            "monthly": [
                {
                    "month": mp.label,
                    "views": int(mp.views),
                    "despiked": round(d, 1),
                    "edition_views": int(ed.views),
                    "per_million": (round(mp.views / ed.views * 1e6, 3) if ed.views else None),
                }
                for mp, d, ed in zip(monthly_raw, monthly_desp, ed_user_m)
            ],
        }

    comparison = compare_languages(langs_out)
    warnings = list(res.warnings)
    auto_resolved = not p.qid and not p.articles
    if langs_out and all(v["volume"]["median_monthly"] < LOW_VOLUME_CONCEPT for v in langs_out.values()):
        if auto_resolved:
            # Label the item's classes so the stop explains itself and so the specific-entity list can be
            # tuned from evidence (the live VOA case passed the P31 gate: its class was not in the list).
            classes = mw.labels(res.instance_of) if res.instance_of else {}
            raise ConfirmConcept(
                res.qid,
                res.label,
                res.description,
                {lg: v["volume"]["median_monthly"] for lg, v in langs_out.items()},
                classes,
            )
        warnings.append(
            f"All articles have under {LOW_VOLUME_CONCEPT} views/month; the topic is genuinely small on Wikipedia."
        )
    full = {
        "topic": res.topic,
        "qid": res.qid,
        "label": res.label,
        "description": res.description,
        "instance_of": res.instance_of,
        "window": {"start": win.start.isoformat(), "end": win.end.isoformat(), "months": p.months},
        "agent": p.agent,
        "access": p.access,
        "redirects_included": p.include_redirects,
        "languages": langs_out,
        "comparison": comparison,
        "missing": res.missing,
        "alternatives": res.alternatives,
        "warnings": warnings,
        "data_source": "Wikimedia Analytics API (pageviews per-article / aggregate), CC0",
        "requests": aqs.requests_made + mw.requests_made,
        "cache_hits": aqs.cache.hits + mw.cache.hits,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "analysis.json").write_text(json.dumps(full, indent=1, ensure_ascii=False), encoding="utf-8")
    full["files"] = {"analysis": str(out_dir / "analysis.json")}
    return full


def compare_languages(langs_out: dict[str, dict]) -> dict | None:
    """Deterministic cross-language ranking. Weak models misread numbers out of
    nested JSON (a live run reported Ukrainian's 124.5 per million as ~48), and
    ranking is arithmetic, so the code does it and states the result in one
    sentence the model can relay verbatim."""
    if len(langs_out) < 2:
        return None

    def rel(d):
        r = d.get("relative") or {}
        return r.get("median_per_million")

    by_rel = sorted(((lg, rel(d)) for lg, d in langs_out.items() if rel(d) is not None), key=lambda t: -t[1])
    by_vol = sorted(((lg, d["volume"]["median_monthly"]) for lg, d in langs_out.items()), key=lambda t: -t[1])
    by_rel_growth = sorted(
        ((lg, (d.get("relative") or {}).get("growth", {}).get("annualized_trend")) for lg, d in langs_out.items()),
        key=lambda t: -(t[1] if t[1] is not None else -9),
    )
    parts = []
    if len(by_rel) >= 2:
        lo = by_rel[-1][1]
        ratio = f" ({by_rel[0][0]} is {by_rel[0][1] / lo:.1f}× {by_rel[-1][0]})" if lo and lo > 0 else ""
        parts.append(
            "Relative attention (views per million edition views): "
            + " > ".join(f"{lg} {v:.1f}" for lg, v in by_rel)
            + ratio
            + "."
        )
    parts.append("Absolute volume (median views/month): " + " > ".join(f"{lg} {v:,.0f}" for lg, v in by_vol) + ".")
    parts.append(
        "Verdicts: "
        + ", ".join(
            f"{lg} {d['verdict']}" + (f" ({d['magnitude']})" if d.get("magnitude") else "")
            for lg, d in langs_out.items()
        )
        + "."
    )
    parts.append(
        "Trust: "
        + ", ".join(f"{lg} {d['trust']['label']} ({d['trust']['score']})" for lg, d in langs_out.items())
        + "."
    )
    return {
        "by_relative_attention": [{"lang": lg, "median_per_million": round(v, 1)} for lg, v in by_rel],
        "by_absolute_volume": [{"lang": lg, "median_monthly": round(v)} for lg, v in by_vol],
        "by_relative_growth": [
            {"lang": lg, "annualized_trend": (round(v, 3) if v is not None else None)} for lg, v in by_rel_growth
        ],
        "highest_relative_attention": by_rel[0][0] if by_rel else None,
        "statement": " ".join(parts),
    }


def summarize(full: dict) -> dict:
    """The compact view a small model reads. No series, no monthly rows, and
    nothing derivable: free-tier models have request limits as low as 8k
    tokens (Groq gpt-oss-120b), so every field here must earn its place.
    Full detail stays in analysis.json."""
    langs = {}
    for lang, d in full["languages"].items():
        rel = d.get("relative") or {}
        mix = d.get("agent_mix") or {}
        tt = d.get("trend_test") or {}
        entry = {
            "article": d["article"],
            "article_url": d["article_url"],
            "wikidata_item": d["resolution"]["wikidata_item"],
            "verdict": d["verdict"],
            "magnitude": d["magnitude"],
            "trust": {
                "score": d["trust"]["score"],
                "label": d["trust"]["label"],
                "reasons": d["trust"]["reasons"],
                "notes": d["trust"].get("notes", []),
            },
            "growth": {
                k: _r(d["growth"].get(k)) for k in ("yoy", "annualized_trend", "recent_3m_vs_same_3m_last_year")
            },
            "months_up_of": f"{tt.get('months_up')}/{tt.get('pairs')}" if tt.get("pairs") else None,
            "p_value": _r(tt.get("p_value")),
            "volume": {k: _r(d["volume"].get(k)) for k in ("median_monthly", "total_views", "peak", "trough")},
            "shape": d.get("shape"),
            "spikes": {
                "count": d["spikes"]["count"],
                "share_of_total": _r(d["spikes"]["share_of_total"]),
                "top": d["spikes"]["top"][:2],
            }
            if d["spikes"]["count"]
            else {"count": 0},
            "relative": (
                {
                    "verdict": rel.get("verdict"),
                    "magnitude": rel.get("magnitude"),
                    "median_per_million": _r(rel.get("median_per_million")),
                    "yoy": _r(rel.get("growth", {}).get("yoy")),
                    "annualized_trend": _r(rel.get("growth", {}).get("annualized_trend")),
                }
                if rel
                else None
            ),
            "agent_mix": (
                {
                    "reclassification_suspected": mix.get("reclassification_suspected"),
                    "classification_sensitive": mix.get("classification_sensitive"),
                    "article_user_change": _r(mix.get("article_user_change")),
                    "article_all_agents_change": _r(mix.get("article_all_agents_change")),
                }
                if mix
                else None
            ),
            "redirects": (
                {
                    "found": d["redirects"]["found"],
                    "share": _r(d["redirects"]["share"]),
                    "top": d["redirects"]["top"][:2],
                }
                if d["redirects"]["found"]
                else {"found": 0}
            ),
            "months_used": d["months_used"],
            "first_revision": d["first_revision"],
        }
        if d["partial_history"]:
            entry["partial_history"] = True
        langs[lang] = entry
    return {
        "topic": full["topic"],
        "qid": full["qid"],
        "label": full["label"],
        "description": full["description"],
        "window": full["window"],
        "agent": full["agent"],
        "redirects_included": full["redirects_included"],
        "languages": langs,
        "comparison": full.get("comparison"),
        "missing": {lg: [x["title"] for x in sugg[:4]] for lg, sugg in full["missing"].items()},
        "alternatives": [
            {"qid": a["qid"], "label": a["label"], "description": a["description"][:80], "has": a["has"]}
            for a in full["alternatives"][:3]
        ],
        "warnings": full["warnings"],
        "files": full.get("files", {}),
        "note": "Pageviews measure attention on Wikipedia, not willingness to pay. A missing article is no measurement, not zero interest. Spike dates say when, not why.",
    }


def _r(v):
    return round(v, 4) if isinstance(v, float) else v
