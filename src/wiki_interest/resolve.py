"""Resolve a user's topic to one article title per requested language.

Where the intelligence lives, and why (decision D10):
- Code does everything it can do reliably: query Wikidata, drop candidates that
  are obviously not encyclopedic topics, attach sitelinks, verify that a title
  exists, and report which Wikidata item a title belongs to.
- The model does the part code cannot: pick between genuinely different
  concepts, and propose a title in a language it knows when Wikidata has no
  sitelink. Every proposal is verified here, so a hallucinated title cannot
  silently become an empty series.

Live evidence this design responds to (experiments/exp01, exp03):
- Wikidata search for "intermittent fasting" returns the topic plus four
  clinical trials / scholarly articles with no sitelinks anywhere.
- "learning English" resolves first to a VOA-related simplified-English concept.
- Polish Wikipedia has no article on intermittent fasting at all; that absence
  is itself a finding and must be reported, not hidden.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict

from .mediawiki import MediaWikiClient, Candidate
from .titles import display_title

# A live run resolved "learning English" to Q2731224, a VOA-related
# simplified-English concept with very low pageviews. Its P31 classes are
# concept-like, so the ontology gate intentionally allows it; the later
# low-volume confirmation gate catches the case instead.
SPECIFIC_ENTITY_CLASSES = {
    "Q5": "human",
    "Q11424": "film",
    "Q93204": "documentary film",
    "Q5398426": "television series",
    "Q15416": "television program",
    "Q1555508": "radio program",
    "Q14350": "radio station",
    "Q1616075": "television station",
    "Q482994": "album",
    "Q134556": "single",
    "Q7366": "song",
    "Q105543609": "musical work",
    "Q215380": "musical group",
    "Q7725634": "literary work",
    "Q571": "book",
    "Q47461344": "written work",
    "Q13442814": "scholarly article",
    "Q5633421": "scientific journal",
    "Q737498": "academic journal",
    "Q1002697": "periodical",
    "Q41298": "magazine",
    "Q11032": "newspaper",
    "Q35127": "website",
    "Q7889": "video game",
    "Q4830453": "business",
    "Q6881511": "enterprise",
    "Q43229": "organization",
    "Q783794": "company",
    "Q431289": "brand",
    "Q3305213": "painting",
    "Q860861": "sculpture",
    "Q95074": "fictional character",
    "Q15632617": "fictional human",
    "Q24634210": "podcast",
    "Q17558136": "YouTube channel",
    "Q2424752": "product",
    "Q7397": "software",
    "Q620615": "mobile app",
    "Q30612": "clinical trial",
    "Q4167836": "Wikimedia category",
    "Q11266439": "Wikimedia template",
    "Q1371849": "radio series",
    "Q15265344": "broadcaster",
    "Q2088357": "musical ensemble",
    "Q1320047": "book publisher",
    "Q3918": "university",
    "Q2085381": "publisher",
}

JUNK_DESCRIPTION = re.compile(
    r"scientific article|scholarly article|clinical trial|journal article|article published|"
    r"\bpaper\b|\bthesis\b|\balbum\b|\bsong\b|\bsingle\b|\bepisode\b|\bfilm\b|\bpainting\b|"
    r"\btrack\b|\bband\b|\bcompany\b|\bmagazine\b|\bbook\b|\bnovel\b|\bpodcast\b|"
    r"\bprogramme\b|\bprogram\b|\bradio\b|\bwebsite\b|\bjournal\b",
    re.I,
)


@dataclass
class Resolved:
    lang: str
    title: str  # canonical title after normalisation/redirects
    source: str  # "sitelink" | "user" | "user+redirect"
    wikidata_item: str | None
    redirected_from: str | None = None
    note: str | None = None


@dataclass
class Resolution:
    topic: str
    qid: str | None
    label: str | None
    description: str | None
    instance_of: list[str] = field(default_factory=list)  # P31 classes of the chosen item
    articles: dict[str, Resolved] = field(default_factory=dict)
    missing: dict[str, list[dict]] = field(default_factory=dict)  # lang -> suggestions
    alternatives: list[dict] = field(default_factory=list)  # other plausible candidates
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["articles"] = {k: asdict(v) for k, v in self.articles.items()}
        return d


class AmbiguousTopic(Exception):
    def __init__(self, topic: str, candidates: list[dict]):
        super().__init__(f"'{topic}' matches several Wikidata items")
        self.topic, self.candidates = topic, candidates


class NoArticles(Exception):
    def __init__(self, topic: str, missing: dict[str, list[dict]], candidates: list[dict]):
        super().__init__(f"no article found for '{topic}' in any requested language")
        self.topic, self.missing, self.candidates = topic, missing, candidates
        self.excluded = [c for c in candidates if c.get("excluded")]


def _cand_dict(c: Candidate, langs: list[str]) -> dict:
    d = {
        "qid": c.qid,
        "label": c.label,
        "description": c.description,
        "instance_of": sorted(c.instance_of),
        "has": sorted(lg for lg in langs if lg in c.sitelinks),
        "missing": sorted(lg for lg in langs if lg not in c.sitelinks),
        "titles": {lg: c.sitelinks[lg] for lg in langs if lg in c.sitelinks},
    }
    why = _exclusion_reason(c, langs)
    if why:
        d["excluded"] = why
    return d


def _exclusion_reason(c: Candidate, langs: list[str]) -> str | None:
    """Why a candidate is not eligible for automatic resolution. Explicit --qid bypasses this."""
    if c.is_disambiguation:
        return "disambiguation page"
    if c.is_list:
        return "Wikimedia list"
    classes = [SPECIFIC_ENTITY_CLASSES[q] for q in c.instance_of if q in SPECIFIC_ENTITY_CLASSES]
    if classes:
        return f"specific entity ({', '.join(sorted(classes))}), not a general topic"
    if not any(lg in c.sitelinks for lg in langs):
        return "no article in any requested language"
    m = JUNK_DESCRIPTION.search(c.description or "")
    if m:
        return f"description suggests a specific work or entity ('{m.group(0)}')"
    return None


def _is_junk(c: Candidate, langs: list[str]) -> bool:
    return _exclusion_reason(c, langs) is not None


def _search_candidates(mw: MediaWikiClient, topic: str, langs: list[str], search_langs: list[str]) -> list[Candidate]:
    seen: dict[str, Candidate] = {}
    for sl in search_langs:
        for c in mw.enrich(mw.search_items(topic, language=sl, limit=8), langs):
            seen.setdefault(c.qid, c)
        if any(not _is_junk(c, langs) for c in seen.values()):
            break  # found something usable; do not spend more requests
    return list(seen.values())


def resolve(
    mw: MediaWikiClient,
    topic: str,
    langs: list[str],
    qid: str | None = None,
    user_titles: dict[str, str] | None = None,
    search_lang: str = "en",
    accept_first: bool = False,
) -> Resolution:
    """Map topic -> titles. Raises AmbiguousTopic / NoArticles for the two cases
    where proceeding silently would be worse than a round trip to the model."""
    user_titles = dict(user_titles or {})
    res = Resolution(topic=topic, qid=None, label=None, description=None)

    # 1. Anything the user pinned explicitly is verified, never trusted blindly.
    for lang, title in user_titles.items():
        info = mw.resolve_title(lang, title)
        if not info["exists"]:
            res.missing[lang] = mw.suggest_titles(lang, title) or mw.suggest_titles(lang, topic)
            res.warnings.append(f"{lang}: '{title}' does not exist on {lang}.wikipedia.org")
            continue
        res.articles[lang] = Resolved(
            lang,
            display_title(info["title"]),
            "user+redirect" if info["redirected_from"] else "user",
            mw.wikidata_item_of(lang, info["title"]),
            info["redirected_from"],
        )
    remaining = [lg for lg in langs if lg not in res.articles and lg not in res.missing]

    # 2. Concept lookup on Wikidata for the rest.
    candidates: list[Candidate] = []
    primary: Candidate | None = None
    if remaining or qid:
        if qid:
            candidates = mw.enrich([Candidate(qid, "", "")], langs)
            primary = candidates[0]
        else:
            search_langs = [search_lang] + [lg for lg in langs if lg != search_lang]
            candidates = _search_candidates(mw, topic, langs, search_langs)
            usable = [c for c in candidates if not _is_junk(c, langs)]
            if usable:
                exact = [c for c in usable if c.label.strip().lower() == topic.strip().lower()]
                primary = (exact or usable)[0]
                # Several distinct usable concepts and either no exact label match or several
                # exact matches ("Mercury" planet vs element) -> let the model choose.
                if len(usable) > 1 and len(exact) != 1 and not accept_first:
                    raise AmbiguousTopic(topic, [_cand_dict(c, langs) for c in usable[:6]])
                res.alternatives = [_cand_dict(c, langs) for c in usable if c is not primary][:5]
        if primary:
            res.qid, res.label, res.description = primary.qid, primary.label, primary.description
            res.instance_of = sorted(primary.instance_of)
            if res.topic == primary.qid and primary.label:
                res.topic = primary.label  # only --qid was given: name the run after the concept
            for lang in remaining:
                t = primary.sitelinks.get(lang)
                if t:
                    res.articles[lang] = Resolved(lang, display_title(t), "sitelink", primary.qid)
                else:
                    res.missing[lang] = mw.suggest_titles(lang, topic)
        else:
            for lang in remaining:
                res.missing[lang] = mw.suggest_titles(lang, topic)

    # 3. Cross-language consistency: user-pinned titles attached to a different item.
    items = {a.wikidata_item for a in res.articles.values() if a.wikidata_item}
    if len(items) > 1:
        detail = ", ".join(f"{a.lang}={a.wikidata_item}" for a in res.articles.values())
        res.warnings.append(f"articles belong to different Wikidata items ({detail}); they may not be the same concept")
    if not res.articles:
        raise NoArticles(topic, res.missing, [_cand_dict(c, langs) for c in candidates[:6]])
    for lang in res.missing:
        res.warnings.append(
            f"{lang}: no article for this topic on {lang}.wikipedia.org; the absence is itself a signal"
        )
    return res
