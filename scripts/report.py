#!/usr/bin/env python3
"""Build the PDF report from an analysis.json produced by analyze.py.

  python scripts/report.py --analysis out/<run>/analysis.json --out out/<run>/report.pdf [--notes notes.md] [--title "..."] [--no-appendix]

Numbers, table and charts come from analysis.json; --notes (Markdown) is rendered under "Analyst notes".
Prints one JSON object: {"report": path, "pages": n}. Exit 1 on bad arguments.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from wiki_interest.cli import ensure_utf8_stdout  # noqa: E402
from wiki_interest.env import load_dotenv  # noqa: E402
from wiki_interest.report import build_report  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="report.py")
    ap.add_argument("--analysis", required=True, help="analysis.json from analyze.py")
    ap.add_argument("--out", help="PDF path (default: report.pdf next to analysis.json)")
    ap.add_argument("--notes", help="Markdown file with the analyst's interpretation")
    ap.add_argument("--title")
    ap.add_argument("--no-appendix", action="store_true", help="One page only; omit the monthly charts page")
    a = ap.parse_args(argv)
    ensure_utf8_stdout()
    load_dotenv()
    analysis = Path(a.analysis)
    if not analysis.exists():
        print(json.dumps({"error": "bad_arguments", "message": f"{analysis} not found; run analyze.py first"}))
        return 1
    full = json.loads(analysis.read_text(encoding="utf-8"))
    notes = Path(a.notes).read_text(encoding="utf-8") if a.notes else None
    out = Path(a.out) if a.out else analysis.parent / "report.pdf"
    build_report(full, out, notes_md=notes, title=a.title, charts_dir=analysis.parent, appendix=not a.no_appendix)
    pages = 2 if (not a.no_appendix and (analysis.parent / "chart_monthly.png").exists()) else 1
    print(
        json.dumps(
            {
                "report": str(out),
                "pages": pages,
                "note": "Analyst notes appear in a labelled section; all figures come from analysis.json.",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
