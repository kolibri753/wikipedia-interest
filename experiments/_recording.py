"""Shared transport for experiments: records every raw HTTP response as a
replayable fixture and keeps per-request timing. Looks up the real GET at call
time (via the module attribute) so dry-runs can substitute a fake."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import wiki_interest.http as _http
from wiki_interest.http import HttpResponse

HERE = Path(__file__).resolve().parent
REC_DIR = HERE / "recorded"
MANIFEST = REC_DIR / "manifest.json"
timings: list[dict] = []


def _load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    return {}


def recording_get(url: str, headers) -> HttpResponse:
    REC_DIR.mkdir(exist_ok=True)
    manifest = _load_manifest()
    t0 = time.perf_counter()
    resp = _http.urllib_get(url, headers)
    timings.append(
        {"url": url, "status": resp.status, "seconds": round(time.perf_counter() - t0, 3), "bytes": len(resp.body)}
    )
    name = manifest.get(url) or f"{len(manifest):03d}.json"
    (REC_DIR / name).write_text(
        json.dumps(
            {
                "url": url,
                "status": resp.status,
                "headers": dict(resp.headers),
                "body": resp.body.decode("utf-8", "replace"),
                "fetched_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    manifest[url] = name
    MANIFEST.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return resp


def timing_summary() -> dict:
    ok = [t for t in timings if t["status"] == 200]
    return {
        "requests": len(timings),
        "non_200": [t for t in timings if t["status"] != 200][:10],
        "median_seconds": sorted(t["seconds"] for t in ok)[len(ok) // 2] if ok else None,
        "max_seconds": max((t["seconds"] for t in ok), default=None),
        "total_seconds": round(sum(t["seconds"] for t in timings), 1),
    }
