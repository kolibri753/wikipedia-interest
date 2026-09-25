"""A small fake Wikimedia world served through the injectable HTTP layer.

Modelled on what the live probes returned (experiments/exp01, exp03):
- Wikidata search mixes the real topic with junk (clinical trials, songs).
- Polish has no article on intermittent fasting; suggestions are noisy.
- English has redirects carrying real traffic ("5:2 diet").
- Ukrainian astronomy spikes every September (school year).
- 'user' traffic is a shrinking share of 'all-agents' from May 2025 (reclassification).
- Titles are case-sensitive: the lower-case form is a different, empty page.
"""

from __future__ import annotations

import json
import random
import re
import zlib
from datetime import date, timedelta
from urllib.parse import parse_qs, unquote, urlparse

from wiki_interest.http import HttpResponse

ITEMS = {
    "Q1666254": {
        "label": "intermittent fasting",
        "description": "a diet that cycles between fasting and non-fasting",
        "sitelinks": {
            "cs": "Přerušovaný půst",
            "en": "Intermittent fasting",
            "uk": "Інтервальне голодування",
            "de": "Intervallfasten",
        },
    },
    "Q63574657": {
        "label": "Intermittent Fasting Versus Daily Caloric Restriction",
        "description": "clinical trial",
        "sitelinks": {},
    },
    "Q333": {
        "label": "astronomy",
        "description": "natural science studying celestial objects",
        "sitelinks": {
            "cs": "Astronomie",
            "en": "Astronomy",
            "uk": "Астрономія",
            "pl": "Astronomia",
            "de": "Astronomie",
        },
    },
    "Q752075": {
        "label": "Astronomy and Astrophysics",
        "description": "scientific journal",
        "sitelinks": {"en": "Astronomy and Astrophysics", "pl": "Astronomy and Astrophysics"},
    },
    "Q1": {"label": "Mercury", "description": "planet", "sitelinks": {"en": "Mercury (planet)", "pl": "Merkury"}},
    "Q2": {
        "label": "Mercury",
        "description": "chemical element",
        "sitelinks": {"en": "Mercury (element)", "pl": "Rtęć"},
    },
    "Q2731224": {
        "label": "Learning English",
        "description": "simplified English in Voice of America",
        "sitelinks": {"en": "VOA Learning English", "pl": "VOA Learning English"},
        "p31": ["Q1575129", "Q2920188"],
    },  # controlled language; plain English
    "Q9001": {
        "label": "Learning English Radio",
        "description": "radio programme about learning English",
        "sitelinks": {"en": "Learning English Radio"},
        "p31": ["Q1555508"],
    },
    "Q77": {
        "label": "obscure fasting variant",
        "description": "a little-known dietary pattern",
        "sitelinks": {"pl": "Obscure fasting variant"},
        "p31": ["Q9999"],
    },
    # Wikidata's real label for Q1860 is "English", not "English language" (labels are editable text)
    "Q1860": {
        "label": "English",
        "description": "West Germanic language",
        "sitelinks": {"en": "English language", "pl": "Język angielski", "cs": "Angličtina"},
    },
}
SEARCH = {
    "intermittent fasting": ["Q1666254", "Q63574657"],
    "astronomy": ["Q333", "Q752075"],
    "mercury": ["Q1", "Q2"],
    "learning english": ["Q2731224"],
    "learning english radio": ["Q9001"],
    "obscure fasting variant": ["Q77"],
}
REDIRECTS = {("en", "Intermittent_fasting"): ["5:2 diet", "OMAD", "Intermittant fasting"]}
FIRST_REV = {
    ("cs", "Přerušovaný_půst"): "2020-10-28",
    ("en", "Intermittent_fasting"): "2008-11-07",
    ("uk", "Астрономія"): "2004-02-26",
    ("uk", "Інтервальне_голодування"): "2025-05-15",
}
TITLE_ITEM = {
    ("cs", "Přerušovaný_půst"): "Q1666254",
    ("en", "Intermittent_fasting"): "Q1666254",
    ("pl", "Głodówka_lecznicza"): "Q999",
    ("uk", "Астрономія"): "Q333",
}
EXISTING = set(FIRST_REV) | set(TITLE_ITEM) | {("en", "5:2_diet"), ("en", "OMAD"), ("en", "Intermittant_fasting")}
PL_SEARCH = [
    {"title": "Charlie Kirk", "snippet": "Post Hill Press"},
    {"title": "Głodówka lecznicza", "snippet": "intermittent fasting"},
]


def _daily_level(lang: str, title: str, d: date, agent: str) -> float:
    base = {"en": 900.0, "cs": 12.0, "uk": 40.0, "pl": 200.0, "de": 300.0}.get(lang, 100.0)
    if "VOA" in title or "Obscure" in title:
        base = 0.4  # the real VOA articles get 8-24 views/month
    elif "5:2" in title:
        base = 12.0
    elif "OMAD" in title:
        base = 1.5
    elif "Intermittant" in title:
        base = 0.2
    t = (d - date(2024, 9, 1)).days
    level = base * (0.5 ** (t / 730))  # halves over the window (the live pattern)
    if lang == "uk" and d.month == 9 and d.day <= 7:
        level *= 6  # school-year spike
    if lang == "en" and d.month == 1:
        level *= 1.4  # New-Year diet peak
    if agent == "all-agents":
        level *= 1.25 if d < date(2025, 5, 1) else 1.8  # user share 80% -> 56% after reclassification
    return level


def fake_get(url: str, headers) -> HttpResponse:
    u = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    rnd = random.Random(zlib.crc32(url.encode()))  # deterministic across processes (hash() is salted)

    def ok(d):
        return HttpResponse(200, json.dumps(d).encode("utf-8"), {})

    if u.netloc == "www.wikidata.org":
        if q.get("action") == "wbsearchentities":
            ids = SEARCH.get(q["search"].strip().lower(), [])
            return ok(
                {"search": [{"id": i, "label": ITEMS[i]["label"], "description": ITEMS[i]["description"]} for i in ids]}
            )
        if q.get("action") == "wbgetentities" and q.get("props") == "labels":
            names = {
                "Q1555508": "radio program",
                "Q1575129": "controlled language",
                "Q2920188": "plain English",
                "Q9999": "dietary pattern"
            }
            return ok({"entities": {i: {"labels": {"en": {"value": names.get(i, i)}}} for i in q["ids"].split("|")}})
        if q.get("action") == "wbgetentities":
            sites = [s[:-4] for s in q["sitefilter"].split("|")]
            ents = {}
            for i in q["ids"].split("|"):
                it = ITEMS.get(i, {"sitelinks": {}})
                ents[i] = {
                    "sitelinks": {f"{lg}wiki": {"title": t} for lg, t in it["sitelinks"].items() if lg in sites},
                    "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": q}}}} for q in it.get("p31", [])]},
                    "labels": {"en": {"value": it.get("label", "")}},
                    "descriptions": {"en": {"value": it.get("description", "")}},
                }
            return ok({"entities": ents})

    if u.path == "/w/api.php":
        lang = u.netloc.split(".")[0]
        title = q.get("titles", "").replace(" ", "_")
        if q.get("prop") == "redirects":
            return ok(
                {
                    "query": {
                        "pages": [
                            {"title": title, "redirects": [{"title": r} for r in REDIRECTS.get((lang, title), [])]}
                        ]
                    }
                }
            )
        if q.get("prop") == "revisions":
            ts = FIRST_REV.get((lang, title))
            return ok(
                {"query": {"pages": [{"title": title, "revisions": [{"timestamp": ts + "T00:00:00Z"}] if ts else []}]}}
            )
        if q.get("prop") == "pageprops":
            item = TITLE_ITEM.get((lang, title))
            return ok(
                {"query": {"pages": [{"title": title, **({"pageprops": {"wikibase_item": item}} if item else {})}]}}
            )
        if q.get("list") == "search":
            return ok({"query": {"search": PL_SEARCH if lang == "pl" and "nonexistent" not in q["srsearch"] else []}})
        if q.get("action") == "query" and "titles" in q:
            exists = (lang, title) in EXISTING
            return ok(
                {"query": {"pages": [{"title": title.replace("_", " "), **({} if exists else {"missing": True})}]}}
            )

    if u.netloc == "wikimedia.org":
        m = re.match(
            r"/api/rest_v1/metrics/pageviews/(per-article|aggregate)/([a-z-]+)\.wikipedia\.org/([a-z-]+)/([a-z-]+)/(.+)$",
            u.path,
        )
        kind, lang, access, agent, rest = m.groups()
        if kind == "per-article":
            title, gran, start, end = rest.split("/")
            title = unquote(title)
            if title[0].islower():
                return HttpResponse(404, b'{"detail":"no data"}', {})
        else:
            gran, start, end = rest.split("/")
            title = "__project__"
        s = date(int(start[:4]), int(start[4:6]), int(start[6:8]))
        e = date(int(end[:4]), int(end[4:6]), int(end[6:8]))
        items, d = [], s
        while d <= e:
            if kind == "aggregate":
                v = (
                    {"en": 7e9, "cs": 6e7, "uk": 7e7, "pl": 2e8}.get(lang, 1e8)
                    / 30
                    * (1.0 if agent == "user" else 1.45)
                )
                v *= 1.0 if d < date(2025, 5, 1) else (0.88 if agent == "user" else 1.0)
            else:
                v = _daily_level(lang, title, d, agent) * (1 + rnd.gauss(0, 0.15))
            if gran == "monthly":
                nd = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
                days = (nd - d).days
                items.append(
                    {
                        "project": f"{lang}.wikipedia",
                        "article": title,
                        "granularity": gran,
                        "timestamp": d.strftime("%Y%m%d00"),
                        "access": access,
                        "agent": agent,
                        "views": int(v * days),
                    }
                )
                d = nd
            else:
                items.append(
                    {
                        "project": f"{lang}.wikipedia",
                        "article": title,
                        "granularity": gran,
                        "timestamp": d.strftime("%Y%m%d00"),
                        "access": access,
                        "agent": agent,
                        "views": max(0, int(v)),
                    }
                )
                d += timedelta(days=1)
        return ok({"items": items})
    return HttpResponse(500, b"unrouted: " + url.encode(), {})
