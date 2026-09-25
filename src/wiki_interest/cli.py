"""Command-line entry point behind scripts/analyze.py.

Contract with the agent (documented in SKILL.md):
- exit 0: stdout is one JSON object, the compact summary
- exit 2: ambiguous_topic  -> JSON with candidates; re-run with --qid
- exit 3: no_articles      -> JSON with per-language suggestions; use --article
- exit 4: api_error        -> Wikimedia did not answer after retries
- exit 1: bad_arguments
- exit 5: confirm_concept   -> auto-resolved concept with < 100 views/month everywhere; re-run with --qid
Structured stops instead of prose errors so a small model can act on them
without guessing.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from .analysis import ConfirmConcept, Params, analyze, summarize
from .aqs import AQSClient
from .cache import JsonCache
from .charts import make_charts
from .env import load_dotenv
from .http import ApiError, HttpResponse
from .mediawiki import MediaWikiClient
from .resolve import AmbiguousTopic, NoArticles
from .titles import project_host


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "analysis"


def _parse_articles(items: list[str]) -> dict[str, str]:
    out = {}
    for it in items or []:
        if "=" not in it:
            raise argparse.ArgumentTypeError(f"--article expects lang=Title, got {it!r}")
        lang, title = it.split("=", 1)
        out[lang.strip().lower()] = title.strip().strip('"').strip("'")
    return out


class _ArgError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    """argparse normally prints to stderr and exits; the agent only sees stdout, so
    surface the message in the structured error instead."""

    def error(self, message):  # noqa: D401
        raise _ArgError(message)


def build_parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="analyze.py", description="Analyze Wikipedia pageview interest for a topic across language editions."
    )
    ap.add_argument("--topic", help='Topic in plain words, e.g. "intermittent fasting" (optional when --qid is given)')
    ap.add_argument("--langs", nargs="+", required=True, help="Language codes, e.g. pl cs uk")
    ap.add_argument(
        "--months",
        type=int,
        default=24,
        help="Complete months to analyze (default 24; multiples of 12 cancel seasonality)",
    )
    ap.add_argument("--agent", default="user", choices=["user", "all-agents", "spider", "automated"])
    ap.add_argument("--access", default="all-access", choices=["all-access", "desktop", "mobile-web", "mobile-app"])
    ap.add_argument("--qid", help="Pin the Wikidata item, e.g. Q1666254 (from an ambiguous_topic stop)")
    ap.add_argument(
        "--article", nargs="*", default=[], help='Pin a title in one language: --article pl="Głodówka lecznicza"'
    )
    ap.add_argument("--search-lang", default="en", help="Language of the --topic text for Wikidata search (default en)")
    ap.add_argument("--accept-first", action="store_true", help="Do not stop on ambiguity; take the best candidate")
    ap.add_argument("--no-redirects", action="store_true", help="Count the canonical title only")
    ap.add_argument("--out", help="Output folder (default out/<topic-slug>)")
    ap.add_argument("--no-cache", action="store_true", help="Bypass the on-disk response cache")
    ap.add_argument(
        "--offline", action="store_true", help="Serve only from cache; fail instead of touching the network"
    )
    ap.add_argument("--no-charts", action="store_true")
    ap.add_argument("--full", action="store_true", help="Print the full analysis instead of the compact summary")
    ap.add_argument(
        "--pretty", action="store_true", help="Indent the JSON output (for humans; the compact form is for agents)"
    )
    ap.add_argument("--today", help="Override today's date (YYYY-MM-DD) for reproducible windows")
    return ap


def _offline_get(url, headers) -> HttpResponse:
    raise ApiError(f"offline mode: not in cache: {url}")


def main(argv: list[str] | None = None, http_get=None, cache_dir: Path | None = None) -> int:
    """`http_get` and `cache_dir` exist for tests and the eval harness (injected transport)."""
    load_dotenv()
    ap = build_parser()
    try:
        a = ap.parse_args(argv)
        if not a.topic and not a.qid:
            raise _ArgError("give --topic (plain words) or --qid (Wikidata item), or both")
        articles = _parse_articles(a.article)
        langs = [project_host(lg).split(".")[0] for lg in a.langs]
    except SystemExit as e:  # --help
        return 0 if e.code == 0 else 1
    except (_ArgError, argparse.ArgumentTypeError, ValueError) as e:
        _emit({"error": "bad_arguments", "message": str(e), "hint": "python scripts/analyze.py --help"})
        return 1
    global _PRETTY
    _PRETTY = a.pretty

    cache = JsonCache(cache_dir, enabled=not a.no_cache)
    kw = {"http_get": _offline_get} if a.offline else ({"http_get": http_get} if http_get else {})
    if http_get:
        kw["sleep"] = lambda s: None
    aqs = AQSClient(cache=cache, **kw)
    mw = MediaWikiClient(cache=cache, **kw)
    run_name = "-".join(
        [_slug(a.topic or a.qid)]
        + langs
        + ([a.qid.lower()] if a.qid else [])
        + ([a.agent] if a.agent != "user" else [])
        + ([f"{a.months}m"] if a.months != 24 else [])
    )
    out_dir = Path(a.out) if a.out else Path("out") / run_name
    p = Params(
        topic=a.topic or a.qid,
        langs=langs,
        months=a.months,
        agent=a.agent,
        access=a.access,
        include_redirects=not a.no_redirects,
        qid=a.qid,
        articles=articles,
        search_lang=a.search_lang,
        accept_first=a.accept_first,
        today=date.fromisoformat(a.today) if a.today else None,
    )
    try:
        full = analyze(p, aqs, mw, out_dir)
    except AmbiguousTopic as e:
        _emit(
            {
                "error": "ambiguous_topic",
                "message": f"'{e.topic}' matches several Wikidata items; re-run with --qid <QID> "
                f"for the one the user means, or --accept-first.",
                "candidates": e.candidates,
            }
        )
        return 2
    except NoArticles as e:
        msg = f"No general-topic article for '{e.topic}' in any requested language."
        if e.excluded:
            msg += (
                " Wikidata matches were excluded because they are specific entities, not topics: "
                + "; ".join(f"{c['qid']} '{c['label']}' ({c['excluded']})" for c in e.excluded[:3])
                + ". If the user means a general concept, re-run with --qid of that concept (you may know it) or a more "
                "specific --topic; only pass one of these QIDs if the user really means that specific thing."
            )
        else:
            msg += (
                ' Use --article lang="Title" with one of the suggestions only if it is the same concept (check its '
                "scope), or tell the user the topic has no article there."
            )
        _emit(
            {
                "error": "no_articles",
                "message": msg,
                "suggestions": {lg: sg[:5] for lg, sg in e.missing.items()},
                "candidates": e.candidates,
            }
        )
        return 3
    except ConfirmConcept as e:
        _emit(
            {
                "error": "confirm_concept",
                "message": f"Every article resolved for '{a.topic}' has under 100 views/month "
                f"({', '.join(f'{lg} {v:.0f}' for lg, v in e.volumes.items())}). The Wikidata item chosen was "
                f"{e.qid} '{e.label}' — {e.description}"
                + (f" (instance of: {', '.join(e.instance_of.values())})" if e.instance_of else "")
                + ". Either this is not the concept the user means (then re-run "
                f"with the right --qid or a clearer --topic), or the topic is genuinely tiny on Wikipedia (then re-run "
                f"with --qid {e.qid} to confirm and the analysis will proceed with a low-volume warning).",
                "qid": e.qid,
                "label": e.label,
                "description": e.description,
                "instance_of": [f"{lbl} ({q})" for q, lbl in e.instance_of.items()],
                "volumes": e.volumes,
            }
        )
        return 5
    except ApiError as e:
        _emit(
            {
                "error": "api_error",
                "message": str(e),
                "hint": "Re-run once; if it persists, Wikimedia may be having trouble.",
            }
        )
        return 4

    if not a.no_charts:
        try:
            full["files"]["charts"] = make_charts(full, out_dir)
        except Exception as e:  # charts must never sink the analysis
            full["warnings"].append(f"charts failed: {e!r}")
    _emit(full if a.full else summarize(full))
    return 0


def ensure_utf8_stdout() -> None:
    """Windows consoles default to cp1252; article titles are Unicode. Found on a
    real Windows run (UnicodeEncodeError from print). Fix it in the CLI rather
    than asking users to set PYTHONUTF8."""
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover
            pass


_PRETTY = False


def _emit(obj: dict) -> None:
    ensure_utf8_stdout()
    sys.stdout.write(
        json.dumps(obj, ensure_ascii=False, indent=1 if _PRETTY else None, separators=None if _PRETTY else (",", ":"))
        + "\n"
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
