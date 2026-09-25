"""File-based response cache.

Why a cache at all: the assignment asks the skill to "efficiently handle
repeated and related requests", and users iterate ("now add German", "use
desktop only"). Every historical pageview URL is immutable in practice (the
window is part of the URL), so caching forever is safe; the `--no-cache`
flag exists for the rare late correction. It also makes the eval suite cheap
and makes runs reproducible offline once fixtures are recorded.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any


def default_cache_dir() -> Path:
    env = os.environ.get("WIKI_INTEREST_CACHE")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "wikipedia-interest"


class JsonCache:
    def __init__(self, directory: Path | None = None, enabled: bool = True):
        self.dir = Path(directory) if directory else default_cache_dir()
        self.enabled = enabled
        self.hits = 0
        self.misses = 0

    def _path(self, key: str) -> Path:
        return self.dir / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".json")

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            return None
        p = self._path(key)
        if not p.exists():
            self.misses += 1
            return None
        try:
            with p.open("r", encoding="utf-8") as f:
                self.hits += 1
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            self.misses += 1
            return None

    def set(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self._path(key).with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(value, f)
        os.replace(tmp, self._path(key))  # atomic on POSIX and Windows
