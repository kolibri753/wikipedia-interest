"""MediaWiki Action API and Wikidata helpers.

Why the skill needs these in addition to the pageview API:
- Users ask about *topics*; pageviews are per *title*. Wikidata items are the
  hub that links one concept to its article in every language edition.
- Pageviews on redirect titles are counted separately from the target. Adding
  them in is accepted practice (it is what the Wikimedia "Redirect Views" tool
  does) and it also absorbs page moves: the old title becomes a redirect.
- Trends must not be computed from before an article existed, so we ask for
  the timestamp of its first revision.

All requests go through JsonHttpClient (User-Agent, throttling, cache).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from urllib.parse import urlencode

from .http import JsonHttpClient, ApiError
from .titles import normalize_title, project_host

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
DISAMBIGUATION_ITEM = "Q4167410"
LIST_ITEM = "Q13406463"


@dataclass
class Candidate:
    qid: str
    label: str
    description: str
    sitelinks: dict[str, str] = field(default_factory=dict)  # lang -> title
    is_disambiguation: bool = False
    is_list: bool = False
    instance_of: set = field(default_factory=set)  # P31 class QIDs

    def as_dict(self) -> dict:
        return {
            "qid": self.qid,
            "label": self.label,
            "description": self.description,
            "sitelinks": self.sitelinks,
            "is_disambiguation": self.is_disambiguation,
            "is_list": self.is_list,
            "instance_of": sorted(self.instance_of),
        }


def _action_url(project: str, **params) -> str:
    params.setdefault("format", "json")
    params.setdefault("formatversion", 2)
    return f"https://{project}/w/api.php?" + urlencode(params)


def _check_error(data: dict | None, url: str) -> dict:
    if data is None:
        raise ApiError(f"unexpected 404 from Action API: {url}")
    if "error" in data:
        raise ApiError(f"Action API error {data['error'].get('code')}: {data['error'].get('info')}")
    return data


class MediaWikiClient(JsonHttpClient):
    # ------------------------------------------------------------- Wikidata #
    def search_items(self, query: str, language: str = "en", limit: int = 5) -> list[Candidate]:
        url = (
            WIKIDATA_API
            + "?"
            + urlencode(
                {
                    "action": "wbsearchentities",
                    "search": query,
                    "language": language,
                    "uselang": language,
                    "type": "item",
                    "limit": limit,
                    "format": "json",
                }
            )
        )
        data = _check_error(self.get_json(url), url)
        return [
            Candidate(qid=r["id"], label=r.get("label", ""), description=r.get("description", ""))
            for r in data.get("search", [])
        ]

    def enrich(self, cands: list[Candidate], langs: list[str], label_lang: str = "en") -> list[Candidate]:
        """Attach sitelinks for the requested languages, fill in label/description
        when missing (e.g. a user-pinned --qid), and flag disambiguation/list items."""
        if not cands:
            return cands
        sites = "|".join(f"{lg}wiki" for lg in langs)
        url = (
            WIKIDATA_API
            + "?"
            + urlencode(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(c.qid for c in cands),
                    "props": "sitelinks|claims|labels|descriptions",
                    "languages": label_lang,
                    "sitefilter": sites,
                    "format": "json",
                }
            )
        )
        data = _check_error(self.get_json(url), url)
        ents = data.get("entities", {})
        for c in cands:
            e = ents.get(c.qid, {})
            if not c.label:
                c.label = e.get("labels", {}).get(label_lang, {}).get("value", "")
            if not c.description:
                c.description = e.get("descriptions", {}).get(label_lang, {}).get("value", "")
            c.sitelinks = {site[:-4]: v["title"] for site, v in e.get("sitelinks", {}).items() if site.endswith("wiki")}
            p31 = {
                s.get("mainsnak", {}).get("datavalue", {}).get("value", {}).get("id")
                for s in e.get("claims", {}).get("P31", [])
            } - {None}
            c.instance_of = p31
            c.is_disambiguation = DISAMBIGUATION_ITEM in p31
            c.is_list = LIST_ITEM in p31
        return cands

    def labels(self, qids: list[str], lang: str = "en") -> dict[str, str]:
        """English labels for a few QIDs (used to explain `instance of` classes in stops)."""
        qids = [q for q in qids if q]
        if not qids:
            return {}
        url = (
            WIKIDATA_API
            + "?"
            + urlencode(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(qids[:20]),
                    "props": "labels",
                    "languages": lang,
                    "format": "json",
                }
            )
        )
        data = _check_error(self.get_json(url), url)
        return {q: e.get("labels", {}).get(lang, {}).get("value", q) for q, e in data.get("entities", {}).items()}

    def sitelinks_for(self, qid: str, langs: list[str]) -> dict[str, str]:
        return self.enrich([Candidate(qid, "", "")], langs)[0].sitelinks

    # --------------------------------------------------------- per-language #
    def resolve_title(self, lang: str, title: str) -> dict:
        """Follow normalisation and redirects; report whether the page exists.
        Returns {"title": canonical, "exists": bool, "redirected_from": str|None}."""
        project = project_host(lang)
        url = _action_url(project, action="query", titles=normalize_title(title), redirects=1)
        q = _check_error(self.get_json(url), url).get("query", {})
        redirected_from = None
        for r in q.get("redirects", []):
            redirected_from = r["from"]
        pages = q.get("pages", [])
        page = pages[0] if pages else {}
        return {
            "title": page.get("title", title),
            "exists": not page.get("missing", False),
            "redirected_from": redirected_from,
        }

    def redirects_to(self, lang: str, title: str, cap: int = 50) -> list[str]:
        project = project_host(lang)
        out: list[str] = []
        cont: dict = {}
        while True:
            url = _action_url(
                project,
                action="query",
                prop="redirects",
                titles=normalize_title(title),
                rdnamespace=0,
                rdlimit="max",
                **cont,
            )
            data = _check_error(self.get_json(url), url)
            for p in data.get("query", {}).get("pages", []):
                out.extend(r["title"] for r in p.get("redirects", []))
            if "continue" not in data or len(out) >= cap:
                break
            cont = {"rdcontinue": data["continue"]["rdcontinue"], "continue": data["continue"]["continue"]}
        return out[:cap]

    def first_revision(self, lang: str, title: str) -> date | None:
        project = project_host(lang)
        url = _action_url(
            project,
            action="query",
            prop="revisions",
            titles=normalize_title(title),
            rvlimit=1,
            rvdir="newer",
            rvprop="timestamp",
            redirects=1,
        )
        data = _check_error(self.get_json(url), url)
        for p in data.get("query", {}).get("pages", []):
            for r in p.get("revisions", []):
                return date.fromisoformat(r["timestamp"][:10])
        return None

    def wikidata_item_of(self, lang: str, title: str) -> str | None:
        """Which Wikidata item a page (after redirects) is attached to, if any.
        Used to check whether a model-proposed title is the same concept as the
        articles chosen in other languages."""
        project = project_host(lang)
        url = _action_url(
            project,
            action="query",
            prop="pageprops",
            ppprop="wikibase_item",
            titles=normalize_title(title),
            redirects=1,
        )
        data = _check_error(self.get_json(url), url)
        for p in data.get("query", {}).get("pages", []):
            return p.get("pageprops", {}).get("wikibase_item")
        return None

    def search_articles(self, lang: str, query: str, limit: int = 5, in_title: bool = False) -> list[dict]:
        """Full-text search inside one language edition. `in_title` restricts
        matches to titles, which avoids the live failure we saw where a plain
        search for 'post przerywany' returned 'Charlie Kirk' via 'Post Hill Press'."""
        project = project_host(lang)
        term = f"intitle:{query}" if in_title else query
        url = _action_url(project, action="query", list="search", srsearch=term, srlimit=limit, srnamespace=0)
        data = _check_error(self.get_json(url), url)
        return [
            {"title": r["title"], "snippet": _strip(r.get("snippet", ""))}
            for r in data.get("query", {}).get("search", [])
        ]

    def suggest_titles(self, lang: str, query: str, limit: int = 5) -> list[dict]:
        """Ranked suggestions for a topic in one edition: title matches first,
        then full-text hits (snippets help the model judge scope)."""
        seen, out = set(), []
        for hits in (
            self.search_articles(lang, query, limit, in_title=True),
            self.search_articles(lang, query, limit, in_title=False),
        ):
            for h in hits:
                if h["title"] not in seen:
                    seen.add(h["title"])
                    out.append(h)
        return out[:limit]


def _strip(html: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", html)
