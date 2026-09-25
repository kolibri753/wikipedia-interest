#!/usr/bin/env python3
"""Analyze Wikipedia pageview interest for a topic across language editions.

Usage (from the skill directory):
  python scripts/analyze.py --topic "intermittent fasting" --langs pl cs
  python scripts/analyze.py --help
Prints one JSON object. Exit codes: 0 ok, 2 ambiguous topic, 3 no articles, 4 API error, 1 bad arguments.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wiki_interest.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
