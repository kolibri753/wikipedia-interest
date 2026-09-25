"""Replay the assignment's example queries against recorded real responses.

The fixtures under evals/fixtures/cache are the on-disk cache written by real
runs (WIKI_INTEREST_CACHE=evals/fixtures/cache). They are keyed by URL, so this
test also fails loudly if URL construction changes. Skipped when the fixtures
are not present (fresh clone without them, or the offline sandbox).
"""

import json
from pathlib import Path

import pytest

from wiki_interest.cli import main

FIXTURES = Path(__file__).resolve().parent.parent / "evals" / "fixtures" / "cache"
TODAY = ["--today", "2026-09-23"]  # the date the fixtures were recorded on

pytestmark = pytest.mark.skipif(
    not FIXTURES.exists() or not any(FIXTURES.iterdir()), reason="recorded fixtures not present"
)


def run(args, tmp_path, capsys):
    code = main(args + ["--offline", "--no-charts", "--out", str(tmp_path / "out")] + TODAY, cache_dir=FIXTURES)
    out = json.loads(capsys.readouterr().out)
    if code == 4 and "offline mode" in out.get("message", ""):
        pytest.fail(
            "Recorded fixtures are stale for the current URL construction. "
            "Re-record with: python evals/record_fixtures.py\nMissing: " + out["message"]
        )
    return code, out


def test_intermittent_fasting_pl_cs_en(tmp_path, capsys):
    code, s = run(["--topic", "intermittent fasting", "--langs", "pl", "cs", "en"], tmp_path, capsys)
    assert code == 0 and s["qid"] == "Q1666254"
    assert "pl" in s["missing"] and set(s["languages"]) == {"cs", "en"}
    en, cs = s["languages"]["en"], s["languages"]["cs"]
    assert en["verdict"] == "declining" and en["relative"]["verdict"] == "declining"
    assert en["redirects"]["top"][0]["title"] == "5:2 diet"
    assert cs["trust"]["label"] != "high" and any("volume" in r.lower() for r in cs["trust"]["reasons"])


def test_astronomy_uk(tmp_path, capsys):
    code, s = run(["--topic", "astronomy", "--langs", "uk"], tmp_path, capsys)
    uk = s["languages"]["uk"]
    assert uk["verdict"] == "declining"
    assert uk["agent_mix"]["reclassification_suspected"] is True and uk["agent_mix"]["classification_sensitive"] is True
    assert any("bot classifier" in n for n in uk["trust"]["notes"])
    # school-year spike days in September are now caught in both years
    sept = [t for t in uk["spikes"]["top"] if t["date"][5:7] == "09"]
    assert sept


def test_learning_english_wrong_concept_then_pinned(tmp_path, capsys):
    code, s = run(["--topic", "learning English", "--langs", "pl", "cs", "uk"], tmp_path, capsys)
    assert "Voice of America" in s["description"]
    code, s = run(["--topic", "learning English", "--langs", "pl", "cs", "uk", "--qid", "Q1860"], tmp_path, capsys)
    assert s["qid"] == "Q1860" and s["label"]  # labels are editable text; only the QID is a stable identity
    per_million = {lg: d["relative"]["median_per_million"] for lg, d in s["languages"].items()}
    assert per_million["uk"] > 2 * per_million["pl"]  # the founder-relevant comparison
