"""Article title and project handling.

The pageview API keys data by *title string*, so tiny differences (space vs
underscore, lowercase first letter) silently return a different, usually empty,
series. Normalising before every request is the cheapest correctness win in
the whole project.
"""

from __future__ import annotations

import re
from urllib.parse import quote

_LANG_RE = re.compile(r"^[a-z][a-z0-9-]{1,11}$")


def normalize_title(title: str) -> str:
    """Apply MediaWiki's default normalisation for Wikipedia titles.

    - trim and collapse whitespace, then spaces -> underscores
    - upper-case the first character (all Wikipedias have $wgCapitalLinks on;
      Wiktionary does not, which is one reason this skill is Wikipedia-only)
    - leave the rest of the title untouched: 'IPhone' and 'Iphone' are
      different pages, so we must not lower-case anything.
    """
    t = re.sub(r"\s+", " ", title.strip())
    if not t:
        raise ValueError("empty title")
    t = t.replace(" ", "_")
    first, rest = t[0], t[1:]
    up = first.upper()
    # ucfirst in PHP keeps 'ß' as 'ß'; Python turns it into 'SS'. Only apply
    # the change when it stays a single character.
    if len(up) == 1:
        first = up
    return first + rest


def title_path_segment(title: str) -> str:
    """Percent-encode a normalised title for use as a single URL path segment.

    safe='' also encodes '/', '?', '%', '&' and '+' which all occur in real
    titles (e.g. "AC/DC", "Who?", "100%") and would otherwise break the path.
    """
    return quote(normalize_title(title), safe="")


def display_title(title: str) -> str:
    """Underscores back to spaces for human-facing output."""
    return normalize_title(title).replace("_", " ")


def project_host(lang: str) -> str:
    """'pl' -> 'pl.wikipedia.org'. Also accepts a full host and returns it."""
    lang = lang.strip().lower()
    if lang.endswith(".wikipedia.org"):
        return lang
    if not _LANG_RE.match(lang):
        raise ValueError(f"invalid language code: {lang!r}")
    return f"{lang}.wikipedia.org"


def lang_from_project(project: str) -> str:
    return project.split(".")[0]
