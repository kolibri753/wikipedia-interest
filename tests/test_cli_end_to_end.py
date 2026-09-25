"""End-to-end tests of scripts/analyze.py's logic through the real CLI, against
the fake Wikimedia world in fake_wikimedia.py. These are the closest thing to
integration tests we can run offline, and they double as the regression net
for the structured stops a small model relies on."""

import json
from pathlib import Path

from wiki_interest.cli import main
from tests.fake_wikimedia import fake_get

TODAY = ["--today", "2026-09-23"]


def run(args, capsys, tmp_path, **kw):
    code = main(args + ["--out", str(tmp_path / "out")] + TODAY, http_get=fake_get, cache_dir=tmp_path / "cache", **kw)
    out = capsys.readouterr().out
    return code, json.loads(out)


def test_happy_path_with_missing_language(tmp_path, capsys):
    code, s = run(["--topic", "intermittent fasting", "--langs", "pl", "cs", "en"], capsys, tmp_path)
    assert code == 0
    assert set(s["languages"]) == {"cs", "en"}
    assert s["qid"] == "Q1666254" and s["label"] == "intermittent fasting"
    # Polish absence is reported as a finding with suggestions, not hidden
    assert s["missing"]["pl"][:2] == ["Charlie Kirk", "Głodówka lecznicza"]
    assert any("pl: no article" in w for w in s["warnings"])
    en = s["languages"]["en"]
    assert en["verdict"] == "declining" and en["trust"]["label"] in ("high", "medium")
    # 3 redirects exist; the typo one has no traffic so only two appear in the (top-2) list
    assert en["redirects"]["found"] == 3 and len(en["redirects"]["top"]) == 2
    assert en["redirects"]["top"][0]["title"] == "5:2 diet" and en["redirects"]["share"] > 0.005
    full = json.loads((tmp_path / "out" / "analysis.json").read_text(encoding="utf-8"))
    assert full["languages"]["en"]["redirects"]["count"] == 2  # full detail lives in analysis.json
    assert en["relative"]["verdict"] in ("declining", "unclear", "flat")
    assert en["agent_mix"]["reclassification_suspected"] is True
    assert en["agent_mix"]["classification_sensitive"] is True
    assert any("bot classifier" in n for n in en["trust"]["notes"])  # context, not a deduction
    assert en["volume"]["peak"]["month"] and en["volume"]["trough"]["month"]
    # compact summary carries no time series
    assert "monthly" not in en and "per_million" not in en["relative"]  # only the scalar median_per_million
    # files
    out = tmp_path / "out"
    full = json.loads((out / "analysis.json").read_text(encoding="utf-8"))
    assert len(full["languages"]["en"]["monthly"]) == 24
    for f in s["files"]["charts"]:
        assert Path(f).exists() and Path(f).stat().st_size > 10_000


def test_low_volume_language_is_not_trusted(tmp_path, capsys):
    code, s = run(["--topic", "intermittent fasting", "--langs", "cs"], capsys, tmp_path)
    cs = s["languages"]["cs"]
    assert cs["volume"]["median_monthly"] < 3000
    assert cs["trust"]["label"] != "high" and any("volume" in r.lower() for r in cs["trust"]["reasons"])


def test_ambiguous_topic_stops_with_candidates(tmp_path, capsys):
    code, s = run(["--topic", "mercury", "--langs", "pl", "en"], capsys, tmp_path)
    assert code == 2 and s["error"] == "ambiguous_topic"
    assert {c["qid"] for c in s["candidates"]} == {"Q1", "Q2"}
    assert s["candidates"][0]["description"] == "planet"
    # --qid resolves it
    code, s = run(["--topic", "mercury", "--langs", "pl", "en", "--qid", "Q2"], capsys, tmp_path)
    assert code == 0 and s["languages"]["pl"]["article"] == "Rtęć"
    assert s["label"] == "Mercury" and s["description"] == "chemical element"  # filled from Wikidata when pinned


def test_junk_candidates_are_ignored_not_ambiguous(tmp_path, capsys):
    code, s = run(["--topic", "astronomy", "--langs", "uk", "pl"], capsys, tmp_path)
    assert code == 0 and s["qid"] == "Q333"
    # the journal ("scientific journal") is filtered as junk, so nothing is left to list as an alternative
    assert s["alternatives"] == []
    uk = s["languages"]["uk"]
    assert uk["spikes"]["count"] >= 1 and uk["spikes"]["top"][0]["date"].endswith(
        ("-09-01", "-09-02", "-09-03", "-09-04", "-09-05", "-09-06", "-09-07")
    )


def test_specific_entity_is_not_resolved_automatically(tmp_path, capsys):
    """A synthetic radio programme is a specific entity (P31), so auto-resolution stops."""
    code, s = run(
        ["--topic", "learning English radio", "--langs", "en"],
        capsys,
        tmp_path,
    )

    assert code == 3
    assert s["error"] == "no_articles"
    assert s["candidates"][0]["qid"] == "Q9001"
    assert "radio program" in s["candidates"][0]["excluded"]
    assert s["candidates"][0]["instance_of"] == ["Q1555508"]
    assert "specific entities" in s["message"]
    assert "--qid" in s["message"]

def test_low_volume_auto_resolved_concept_requires_confirmation(tmp_path, capsys):
    """Q2731224 is concept-like, but its very low volume requires explicit confirmation."""
    code, s = run(
        ["--topic", "learning English", "--langs", "pl"],
        capsys,
        tmp_path,
    )

    assert code == 5
    assert s["error"] == "confirm_concept"
    assert s["qid"] == "Q2731224"

    code, s = run(
        ["--qid", "Q1860", "--langs", "pl"],
        capsys,
        tmp_path,
    )
    assert code == 0
    assert s["languages"]["pl"]["article"] == "Język angielski"

    code, s = run(
        ["--qid", "Q2731224", "--langs", "pl"],
        capsys,
        tmp_path,
    )
    assert code == 0
    assert s["languages"]["pl"]["article"] == "VOA Learning English"

def test_no_articles_anywhere(tmp_path, capsys):
    code, s = run(["--topic", "zzz nonexistent thing", "--langs", "pl"], capsys, tmp_path)
    assert code == 3 and s["error"] == "no_articles"
    assert s["suggestions"] == {"pl": []} and s["candidates"] == []


def test_pinned_titles_are_verified(tmp_path, capsys):
    code, s = run(
        ["--topic", "intermittent fasting", "--langs", "pl", "cs", "--article", 'pl="Głodówka lecznicza"'],
        capsys,
        tmp_path,
    )
    assert code == 0
    assert s["languages"]["pl"]["article"] == "Głodówka lecznicza" and s["languages"]["pl"]["wikidata_item"] == "Q999"
    assert any("different Wikidata items" in w for w in s["warnings"])  # Q999 vs Q1666254 -> scope warning
    code, s = run(
        ["--topic", "intermittent fasting", "--langs", "pl", "cs", "--article", "pl=Post przerywany"], capsys, tmp_path
    )
    assert code == 0 and "pl" in s["missing"] and any("does not exist" in w for w in s["warnings"])


def test_new_article_partial_history(tmp_path, capsys):
    code, s = run(["--topic", "intermittent fasting", "--langs", "uk"], capsys, tmp_path)
    uk = s["languages"]["uk"]
    assert uk["partial_history"] is True and uk["months_used"] == 15 and uk["first_revision"] == "2025-05-15"
    assert any("18 months" in r for r in uk["trust"]["reasons"])  # seasonal control off is stated in words


def test_offline_without_cache_is_api_error(tmp_path, capsys):
    code = main(
        ["--topic", "astronomy", "--langs", "uk", "--offline", "--out", str(tmp_path / "o")] + TODAY,
        cache_dir=tmp_path / "empty",
    )
    s = json.loads(capsys.readouterr().out)
    assert code == 4 and s["error"] == "api_error" and "offline" in s["message"]


def test_cache_makes_second_run_free(tmp_path, capsys):
    run(["--topic", "astronomy", "--langs", "uk", "--no-charts"], capsys, tmp_path)
    calls = []

    def counting(url, headers):
        calls.append(url)
        return fake_get(url, headers)

    code = main(
        ["--topic", "astronomy", "--langs", "uk", "--no-charts", "--out", str(tmp_path / "out2")] + TODAY,
        http_get=counting,
        cache_dir=tmp_path / "cache",
    )
    capsys.readouterr()
    assert code == 0 and calls == []  # every URL came from the cache


def test_bad_language_code(tmp_path, capsys):
    code, s = run(["--topic", "astronomy", "--langs", "../etc"], capsys, tmp_path)
    assert code == 1 and s["error"] == "bad_arguments"


def test_output_dirs_do_not_collide(tmp_path, capsys, monkeypatch=None):
    """Two runs of the same topic with different concepts must not overwrite each other."""
    import os

    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        for extra in (["--qid", "Q2731224"], ["--qid", "Q1860"]):  # the Q2731224 concept (explicitly pinned) vs the broad English concept
            code = main(
                ["--topic", "learning English", "--langs", "pl", "--no-charts"] + extra + TODAY,
                http_get=fake_get,
                cache_dir=tmp_path / "cache",
            )
            capsys.readouterr()
            assert code == 0
        assert (tmp_path / "out" / "learning-english-pl-q2731224" / "analysis.json").exists()
        assert (tmp_path / "out" / "learning-english-pl-q1860" / "analysis.json").exists()
    finally:
        os.chdir(cwd)


def test_qid_alone_is_enough_and_names_the_run(tmp_path, capsys):
    code, s = run(["--qid", "Q1860", "--langs", "pl"], capsys, tmp_path)
    assert code == 0 and s["topic"] == "English" and s["languages"]["pl"]["article"] == "Język angielski"


def test_argument_errors_are_structured_and_informative(tmp_path, capsys):
    code, s = run(["--langs", "pl"], capsys, tmp_path)
    assert code == 1 and "--topic" in s["message"] and "--qid" in s["message"]
    code, s = run(["--topic", "x", "--langs", "pl", "--agent", "robots"], capsys, tmp_path)
    assert code == 1 and "invalid choice" in s["message"]


def test_tiny_volume_everywhere_requires_confirmation(tmp_path, capsys):
    code, s = run(["--topic", "obscure fasting variant", "--langs", "pl"], capsys, tmp_path)
    assert code == 5 and s["error"] == "confirm_concept" and s["qid"] == "Q77" and s["volumes"]["pl"] < 100
    assert (
        "--qid Q77" in s["message"]
        and s["instance_of"] == ["dietary pattern (Q9999)"]
        and "dietary pattern" in s["message"]
    )
    code, s = run(["--topic", "obscure fasting variant", "--langs", "pl", "--qid", "Q77"], capsys, tmp_path)
    assert code == 0 and any("genuinely small" in w for w in s["warnings"])


def test_summary_is_compact(tmp_path, capsys):
    """Free-tier models have request limits near 8k tokens; the summary must stay small."""
    code, out = (
        main(
            [
                "--topic",
                "intermittent fasting",
                "--langs",
                "pl",
                "cs",
                "en",
                "uk",
                "--out",
                str(tmp_path / "o"),
                "--no-charts",
            ]
            + TODAY,
            http_get=fake_get,
            cache_dir=tmp_path / "cache",
        ),
        capsys.readouterr().out,
    )
    assert code == 0 and len(out) < 7000, len(out)
    s = json.loads(out)
    assert "strengths" not in s["languages"]["en"]["trust"] and "monthly" not in s["languages"]["en"]


def test_comparison_block_ranks_languages_deterministically(tmp_path, capsys):
    code, s = run(["--qid", "Q1860", "--langs", "pl", "cs", "en"], capsys, tmp_path)
    c = s["comparison"]
    rel = [x["lang"] for x in c["by_relative_attention"]]
    assert rel == sorted(rel, key=lambda lg: -s["languages"][lg]["relative"]["median_per_million"])
    assert c["highest_relative_attention"] == rel[0]
    assert c["statement"].startswith("Relative attention (views per million edition views): " + rel[0])
    assert "×" in c["statement"] and "Verdicts:" in c["statement"] and "Trust:" in c["statement"]
    code, s = run(["--topic", "astronomy", "--langs", "uk"], capsys, tmp_path)
    assert s["comparison"] is None  # one language: nothing to compare
