"""MediaWiki/Wikidata client tests with scripted responses (no network)."""

import json
from urllib.parse import urlparse, parse_qs

from wiki_interest.cache import JsonCache
from wiki_interest.http import HttpResponse
from wiki_interest.mediawiki import MediaWikiClient


class Router:
    """Answers by matching the Action API `action`/`prop`/`list` parameters."""

    def __init__(self):
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append(url)
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}

        def ok(d):
            return HttpResponse(200, json.dumps(d).encode(), {})

        if q.get("action") == "wbsearchentities":
            return ok(
                {
                    "search": [
                        {"id": "Q1666254", "label": "intermittent fasting", "description": "a diet"},
                        {"id": "Q63574657", "label": "IF vs daily restriction", "description": "clinical trial"},
                    ]
                }
            )
        if q.get("action") == "wbgetentities":
            assert q["sitefilter"] == "plwiki|cswiki" and "labels" in q["props"]
            return ok(
                {
                    "entities": {
                        "Q1666254": {
                            "sitelinks": {"cswiki": {"title": "Přerušovaný půst"}},
                            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q12345"}}}}]},
                        },
                        "Q63574657": {
                            "sitelinks": {},
                            "claims": {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q13442814"}}}}]},
                        },
                    }
                }
            )
        if q.get("prop") == "redirects":
            if "rdcontinue" not in q:
                return ok(
                    {
                        "query": {"pages": [{"title": "X", "redirects": [{"title": "A"}, {"title": "B"}]}]},
                        "continue": {"rdcontinue": "0|3", "continue": "||"},
                    }
                )
            return ok({"query": {"pages": [{"title": "X", "redirects": [{"title": "C"}]}]}})
        if q.get("prop") == "revisions":
            return ok({"query": {"pages": [{"title": "X", "revisions": [{"timestamp": "2020-10-28T09:00:00Z"}]}]}})
        if q.get("prop") == "pageprops":
            return ok(
                {
                    "query": {
                        "redirects": [{"from": "Post_przerywany", "to": "Post przerywany (dieta)"}],
                        "pages": [{"title": "Post przerywany (dieta)", "pageprops": {"wikibase_item": "Q1666254"}}],
                    }
                }
            )
        if q.get("list") == "search":
            return ok({"query": {"search": [{"title": "Post przerywany", "snippet": "<b>Post</b> przerywany"}]}})
        if q.get("action") == "query" and "titles" in q:
            if q["titles"] == "Missing_page":
                return ok({"query": {"pages": [{"title": "Missing page", "missing": True}]}})
            return ok(
                {
                    "query": {
                        "redirects": [{"from": "IF", "to": "Intermittent fasting"}],
                        "pages": [{"title": "Intermittent fasting"}],
                    }
                }
            )
        return HttpResponse(500, b"unrouted", {})


def make(tmp_path):
    r = Router()
    return MediaWikiClient(http_get=r, sleep=lambda s: None, cache=JsonCache(tmp_path), min_interval=0), r


def test_search_and_enrich_sitelinks_and_flags(tmp_path):
    mw, r = make(tmp_path)
    cands = mw.enrich(mw.search_items("intermittent fasting"), ["pl", "cs"])
    assert [c.qid for c in cands] == ["Q1666254", "Q63574657"]
    assert cands[0].sitelinks == {"cs": "Přerušovaný půst"}  # pl missing, as in the live probe
    assert cands[1].sitelinks == {}
    assert cands[0].is_disambiguation is False


def test_redirects_follow_continuation_and_cap(tmp_path):
    mw, r = make(tmp_path)
    assert mw.redirects_to("en", "X") == ["A", "B", "C"]
    assert mw.redirects_to("en", "X", cap=2) == ["A", "B"]


def test_first_revision_and_item_and_resolve(tmp_path):
    mw, r = make(tmp_path)
    assert str(mw.first_revision("cs", "Přerušovaný půst")) == "2020-10-28"
    assert mw.wikidata_item_of("pl", "post przerywany") == "Q1666254"
    res = mw.resolve_title("en", "IF")
    assert res == {"title": "Intermittent fasting", "exists": True, "redirected_from": "IF"}
    assert mw.resolve_title("en", "Missing page")["exists"] is False


def test_search_articles_strips_html(tmp_path):
    mw, r = make(tmp_path)
    assert mw.search_articles("pl", "post przerywany") == [{"title": "Post przerywany", "snippet": "Post przerywany"}]


def test_user_agent_sent_on_action_api(tmp_path):
    mw, r = make(tmp_path)
    mw.search_articles("pl", "x")
    assert "wikipedia-interest-skill" in mw.user_agent and r.calls[0].startswith("https://pl.wikipedia.org/w/api.php?")
