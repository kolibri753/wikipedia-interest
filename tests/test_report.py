"""PDF report: builds from a real analysis.json produced by the CLI against the fake world,
renders non-Latin titles (Czech diacritics, Cyrillic), and includes the analyst notes."""

import json
import subprocess
import sys
from pathlib import Path


from wiki_interest.cli import main
from wiki_interest.report import build_report
from tests.fake_wikimedia import fake_get

try:
    from pypdf import PdfReader
except Exception:  # pragma: no cover
    PdfReader = None

ROOT = Path(__file__).resolve().parent.parent


def make_analysis(tmp_path, capsys):
    code = main(
        [
            "--topic",
            "intermittent fasting",
            "--langs",
            "pl",
            "cs",
            "en",
            "uk",
            "--out",
            str(tmp_path / "out"),
            "--today",
            "2026-09-23",
        ],
        http_get=fake_get,
        cache_dir=tmp_path / "cache",
    )
    capsys.readouterr()
    assert code == 0
    return tmp_path / "out"


def test_report_builds_with_notes_and_unicode(tmp_path, capsys):
    out = make_analysis(tmp_path, capsys)
    full = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    notes = "# What we compared\nCzech vs English **interest**.\n\n- Validate with a landing page in Czech.\n- Polish has no article: treat as an open question.\n"
    pdf = build_report(full, out / "report.pdf", notes_md=notes)
    assert pdf.exists() and pdf.stat().st_size > 20_000
    if PdfReader:
        r = PdfReader(str(pdf))
        assert len(r.pages) == 2
        text = "".join(p.extract_text() for p in r.pages)
        assert (
            "Přerušovaný půst" in text and "Інтервальне" in text
        )  # DejaVu font renders diacritics + Cyrillic (cells may wrap)
        assert "Analyst notes" in text and "landing page in Czech" in text
        assert "no article for this topic" in text  # Polish absence is on the page
        assert "Limitations" in text and "not demand" in text


def test_report_cli_one_page(tmp_path, capsys):
    out = make_analysis(tmp_path, capsys)
    res = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "report.py"),
            "--analysis",
            str(out / "analysis.json"),
            "--no-appendix",
            "--out",
            str(out / "one.pdf"),
        ],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert res.returncode == 0, res.stderr
    info = json.loads(res.stdout)
    assert info["pages"] == 1 and Path(info["report"]).exists()
    if PdfReader:
        assert len(PdfReader(info["report"]).pages) == 1


def test_report_cli_missing_analysis(tmp_path):
    res = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "report.py"), "--analysis", str(tmp_path / "nope.json")],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert res.returncode == 1 and json.loads(res.stdout)["error"] == "bad_arguments"


def test_report_orders_rows_by_relative_attention_and_prints_comparison(tmp_path, capsys):
    out = make_analysis(tmp_path, capsys)
    full = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    pdf = build_report(full, out / "ranked.pdf", notes_md="Analyst notes\n1. First point.\n2. Second point.")
    if PdfReader:
        text = PdfReader(str(pdf)).pages[0].extract_text()
        first = full["comparison"]["highest_relative_attention"]
        body = text[text.index("Trust\n") + 6 :]  # after the header row
        assert body.split("\n")[0] == first  # highest share is the first row
        assert "Comparison (computed)" in text and text.count("Analyst notes") == 1  # duplicate heading stripped
        assert "First point" in text
