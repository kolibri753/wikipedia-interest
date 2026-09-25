#!/usr/bin/env python3
"""(Re)record the fixtures that tests/test_examples_offline.py replays.

The fixtures are simply the skill's on-disk response cache, keyed by URL. Any
change to how URLs are built (a new query parameter, a different date pad)
makes the old entries unreachable, so this script deletes the cache and
re-records the assignment's example queries online in one go. `--today` is
pinned so the analysis window - and therefore every URL - is identical to
what the tests ask for, whichever day you run it.

Usage (from the skill directory, network required, ~1-2 minutes):
    python evals/record_fixtures.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wiki_interest.env import load_dotenv  # noqa: E402

FIXTURES = ROOT / "evals" / "fixtures" / "cache"
TODAY = "2026-09-23"  # must match tests/test_examples_offline.py
# (arguments, expected exit code). Structured stops are part of the recorded behaviour:
# "learning English" must stop with confirm_concept (5) — auto-resolved Q2731224 has
# 8–24 views/month — and the pinned Q1860 run must succeed (0). Any other exit code is a failure.
EXAMPLES = [
    (["--topic", "intermittent fasting", "--langs", "pl", "cs", "en"], 0),
    (["--topic", "astronomy", "--langs", "uk"], 0),
    (["--topic", "learning English", "--langs", "pl", "cs", "uk"], 5),
    (["--topic", "learning English", "--langs", "pl", "cs", "uk", "--qid", "Q1860"], 0),
]


def main() -> int:
    load_dotenv()
    if FIXTURES.exists():
        shutil.rmtree(FIXTURES)
    FIXTURES.mkdir(parents=True)
    env = {**os.environ, "WIKI_INTEREST_CACHE": str(FIXTURES)}
    for args, expected in EXAMPLES:
        cmd = [
            sys.executable,
            str(ROOT / "scripts" / "analyze.py"),
            *args,
            "--today",
            TODAY,
            "--no-charts",
            "--out",
            str(ROOT / "out" / "_fixture_recording"),
        ]
        print(">", " ".join(a if " " not in a else f'"{a}"' for a in cmd[2:]), f"(expect exit {expected})")
        res = subprocess.run(cmd, env=env, cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != expected:
            print(f"unexpected exit {res.returncode} (expected {expected})")
            print(res.stdout[:800])
            print(res.stderr[:800])
            return res.returncode or 1
    shutil.rmtree(ROOT / "out" / "_fixture_recording", ignore_errors=True)
    files = list(FIXTURES.glob("*.json"))
    size = sum(f.stat().st_size for f in files) / 1e6
    print(f"recorded {len(files)} responses, {size:.1f} MB in {FIXTURES.relative_to(ROOT)}")
    print("now: pytest -q   (the three tests in tests/test_examples_offline.py replay these)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
