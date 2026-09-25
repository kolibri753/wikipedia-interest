"""The eval grader is code too. These pin down the two false positives found on
real transcripts ("40/100", "16 366"), the real failures it must keep catching,
and the context-compaction behaviour that keeps requests under free-tier limits."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "evals"))
import harness as H  # noqa: E402

TOOL = [
    json.dumps(
        {
            "volume": {"median_monthly": 16366.0, "total_views": 6939},
            "growth": {"yoy": -0.4622, "annualized_trend": -0.4826},
            "relative": {"median_per_million": 27.1, "yoy": -0.3081},
            "trust": {"score": 40},
            "share": 0.8354,
            "p_value": 0.0015,
        }
    )
]


def test_score_denominator_and_space_thousands_are_not_flagged():
    assert H.ungrounded_numbers("Trust 40/100 (medium); median ≈ 16 366 views; total 6,939.", TOOL) == []
    assert H.ungrounded_numbers("Trust is 40 / 100.", TOOL) == []


def test_rounded_and_percent_forms_are_grounded():
    assert (
        H.ungrounded_numbers("about 16,400 views/month, down 46% YoY (-48%/yr), share -31%, 84% human, p < 0.002", TOOL)
        == []
    )


def test_invented_numbers_are_flagged():
    assert H.ungrounded_numbers("about 9,500 views and a 73% rise", TOOL) == ["73%", "9,500"]
    # Known limit: a percentage that coincides with *any* value in the output (here the trust score 40)
    # passes. The check is a net for invented figures, not proof of correct attribution.
    assert H.ungrounded_numbers("a 40% rise", TOOL) == []


def test_years_and_qids_are_not_numbers_to_ground():
    assert H.ungrounded_numbers("In 2025 (Q1666254) the trend held.", TOOL) == []


# Real sentences from eval transcripts (Gemini 3.6 Flash, GPT-OSS-120B), Sept 2026.
GEMINI_SCHOOL = (
    "The decline was driven primarily by a single step down (-62% from September 2024 to October 2024), likely tied to "
    "academic school term dynamics (September is the start of the school year in Ukraine when school topics peak), "
    "rather than a steady slide over time."
)
GROQ_MEDIAN = (
    "The median share numbers show that, despite declines, a non-trivial fraction of each edition's traffic still visits the "
    "English-language article, indicating a baseline audience that could be receptive to a language-learning product."
)
GROQ_SURVEY = (
    "*Remember, Wikipedia pageviews measure attention, not demand or willingness to pay. To validate this signal, you could run a "
    "small survey of Ukrainian-speaking users to ask directly about their interest in astronomy topics.*\n"
    "**Which language audience looks most promising for “astronomy”?**\n\n| Language | Absolute trend | ... declining ... | social |"
)
GROQ_RECLASS = (
    "A large single-month drop of -62% from 2024-09 to 2024-10 appears to be a level shift rather than a gradual slide, and the "
    "bot-classification change (human share fell from 82% to 45%) accounts for part of the observed decline."
)
GEMINI_COMPOSITION = (
    "Traffic peaked in April 2025 (958 views), driven largely by a single-day spike on April 14, 2025 (508 views)."
)
PLANTED = "The spike on 2026-01-07 was likely due to New Year news coverage."


def test_cause_check_catches_real_inventions():
    assert H.unsupported_spike_causes(GEMINI_SCHOOL)
    assert H.unsupported_spike_causes(PLANTED)


def test_cause_check_allows_measured_causes_hedges_and_composition():
    assert H.unsupported_spike_causes(GROQ_RECLASS) == []  # reclassification is measured by the script
    assert H.unsupported_spike_causes(GEMINI_COMPOSITION) == []  # composition, not attribution
    assert (
        H.unsupported_spike_causes("The largest drop was -62% Sep->Oct 2024; worth checking what changed then.") == []
    )


def test_cause_check_false_positives_from_live_runs_are_fixed():
    assert H.unsupported_spike_causes(GROQ_MEDIAN) == []  # "median" is not "media"
    assert H.unsupported_spike_causes(GROQ_SURVEY) == []  # split on `topics.*` and on line breaks


def test_market_inference_check():
    assert H.unsupported_market_inferences(
        GROQ_MEDIAN
    )  # "indicating ... receptive to a product" is the real over-reach
    ok = (
        "Run a short survey to confirm that Wikipedia attention translates into intent to learn English. "
        "Wikipedia pageviews measure attention, not demand or willingness to pay."
    )
    assert H.unsupported_market_inferences(ok) == []
    assert H.unsupported_market_inferences("These numbers show strong demand for the course.")
    assert H.unsupported_market_inferences("Test demand with a landing page before assuming customers want this.") == []


def test_sentence_splitter_handles_markdown_and_tables():
    parts = H.sentences("First sentence.* **Second** one.\n| a | b |\n| c | d |")
    assert parts[0] == "First sentence." and parts[1].startswith("**Second**") and len(parts) == 4


def _call(cid, name):
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": cid, "type": "function", "function": {"name": name, "arguments": "{}"}}],
    }


def test_compaction_keeps_two_script_outputs_and_stubs_older_ones_with_pointers():
    analysis = json.dumps(
        {
            "topic": "t",
            "qid": "Q1",
            "label": "L",
            "files": {"analysis": "out/a/analysis.json"},
            "languages": {
                "pl": {"article": "A", "verdict": "declining", "trust": {"score": 80, "reasons": ["x" * 3000]}}
            },
            "missing": {"cs": []},
        }
    )
    first = json.dumps({"exit_code": 0, "stdout": analysis})
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _call("1", "run_script"),
        {"role": "tool", "tool_call_id": "1", "content": first},
        _call("2", "run_script"),
        {"role": "tool", "tool_call_id": "2", "content": json.dumps({"exit_code": 0, "stdout": "SECOND" * 300})},
        _call("3", "write_file"),
        {"role": "tool", "tool_call_id": "3", "content": json.dumps({"written": "notes.md"})},
        _call("4", "run_script"),
        {"role": "tool", "tool_call_id": "4", "content": json.dumps({"exit_code": 0, "stdout": "LATEST" * 200})},
    ]
    view = H.compact_messages(msgs, max_tokens=None)
    stub = json.loads(view[3]["content"])  # oldest script output -> valid JSON stub with pointers
    assert stub["files"] == {"analysis": "out/a/analysis.json"} and stub["qid"] == "Q1"
    assert stub["languages"]["pl"] == {"article": "A", "verdict": "declining", "trust": 80} and stub["missing"] == [
        "cs"
    ]
    assert view[5]["content"] == msgs[5]["content"]  # second-latest kept in full (KEEP_FULL = 2)
    assert view[7]["content"] == msgs[7]["content"]  # write_file result untouched
    assert view[9]["content"] == msgs[9]["content"]  # latest in full
    assert msgs[3]["content"] == first  # stored transcript untouched
    tight = H.compact_messages(msgs, max_tokens=800)  # over budget: stub the older full one first
    assert "superseded" in tight[5]["content"] and H.estimate_tokens(tight) <= 800


def test_compaction_stub_keeps_files_pointer_when_write_file_comes_after_analyze():
    """Regression: the report step needs files.analysis from an analyze output that a
    later write_file would otherwise have pushed out of the window."""
    analysis = json.dumps({"files": {"analysis": "out/x/analysis.json"}, "languages": {}, "pad": "z" * 2000})
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
        _call("1", "run_script"),
        {"role": "tool", "tool_call_id": "1", "content": json.dumps({"exit_code": 0, "stdout": analysis})},
        _call("2", "write_file"),
        {"role": "tool", "tool_call_id": "2", "content": json.dumps({"written": "notes.md"})},
    ]
    view = H.compact_messages(msgs, max_tokens=None)
    assert "out/x/analysis.json" in view[3]["content"]  # latest *script* output stays in full


def test_compaction_truncates_to_fit_a_request_cap():
    msgs = [
        {"role": "system", "content": "s" * 4000},
        {"role": "user", "content": "u"},
        _call("1", "run_script"),
        {"role": "tool", "tool_call_id": "1", "content": "y" * 40000},
    ]
    view = H.compact_messages(msgs, max_tokens=8000)
    assert H.estimate_tokens(view) <= 8000 * 0.9
    assert "truncated to fit" in view[3]["content"]


def test_truncation_keeps_json_envelope_valid():
    env = json.dumps({"exit_code": 0, "stdout": "z" * 30000, "stderr": ""})
    out = H._truncate(env, 3000)
    d = json.loads(out)  # still parseable
    assert d["exit_code"] == 0 and d["stdout"].endswith("request limit]") and len(out) <= 3200


# ---- phase-9 additions from real transcripts
GROQ_LEVEL_SHIFT = (
    "Additional notes: the biggest single-month drop (-62% from Sep 2024 to Oct 2024) looks like a level shift, "
    "while a sharp rise (+338% from Aug 2025 to Sep 2025) is driven by a short-term spike."
)
GEMINI_ACADEMIC = (
    "Conversely, a steep seasonal uptick occurred from August to September 2025 (+338%), aligning with the start of the "
    "academic year."
)


def test_measured_explanations_are_allowed_but_academic_year_is_not():
    assert (
        H.unsupported_spike_causes(GROQ_LEVEL_SHIFT) == []
    )  # level shift + spike are script findings; "short-term" is not "term"
    assert H.unsupported_spike_causes(GEMINI_ACADEMIC)  # the academic year is not in the data


def test_rounding_to_significant_figures_is_grounded():
    tool = [json.dumps({"volume": {"median_monthly": 1236.0}, "other": 611, "cs": 578})]
    assert H.ungrounded_numbers("about 1 200 views/month (median 1,236)", tool) == []
    assert H.ungrounded_numbers("about 1,240 views/month", tool) == []
    assert H.ungrounded_numbers("about 1,300 views/month", tool) == ["1,300"]  # 1236 rounds to 1,200, not 1,300
    assert H.ungrounded_numbers("about 3,000 views/month", tool) == [
        "3,000"
    ]  # (2,000 would pass: indistinguishable from a year)


def test_bounds_are_grounded_when_tight():
    tool = [json.dumps({"uk": 611, "cs": 578, "pl": 1236})]
    assert H.ungrounded_numbers("both operate at lower volumes (<650 views/month)", tool) == []
    assert H.ungrounded_numbers("under 700 views/month", tool) == []
    assert H.ungrounded_numbers("under 5,000 views/month", tool) == ["5,000"]  # true but uselessly loose
    assert H.ungrounded_numbers("over 1,000 views/month in Polish", tool) == []
    assert H.ungrounded_numbers("a plain 650 views/month", tool) == ["650"]  # not a bound, not a value
