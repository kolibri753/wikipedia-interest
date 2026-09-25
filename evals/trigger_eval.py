#!/usr/bin/env python3
"""Trigger eval for the skill's `description`.

Skill-aware agents see only name + description of every installed skill and
decide from that whether to load one. This asks a cheap model to choose among
our skill and three plausible distractors for a set of requests that should
and should not trigger it, and reports precision/recall plus the misses.
Anthropic's skill-creator recommends exactly this kind of should/should-not set
because models under-trigger skills.

Usage (same providers and env vars as harness.py; ~28 short requests):
    GEMINI_API_KEY=... python evals/trigger_eval.py gemini
Results: evals/results/triggers-<provider>-<model>-<timestamp>.json
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harness import PROVIDERS, ProviderError, RateLimitedChat, ROOT, EVALS  # noqa: E402
from wiki_interest.env import load_dotenv  # noqa: E402  (harness put src/ on sys.path)

DISTRACTORS = {
    "web-research": "Search the web for current information on any topic and summarize findings with sources. Use when the "
                    "user needs facts, news or an overview from the internet.",
    "csv-charts": "Load a CSV or spreadsheet the user provides and produce charts and summary statistics from it. Use for the "
                  "user's own data files (sales, usage, survey exports).",
    "wikipedia-summary": "Fetch a Wikipedia article and summarize or explain its content, or answer factual questions from it. "
                         "Use when the user wants to know what an article says.",
}

SHOULD = [
    "Compare the growth of interest in intermittent fasting in Polish-language and Czech-language Wikipedia over the last two years.",
    "We are thinking about adding an astronomy course to an educational application. Is interest in this topic growing in Ukrainian-language Wikipedia, and how much can this growth be trusted?",
    "We are creating a language-learning application. Compare interest in learning English across the language editions we selected and prepare a short report: which audiences should we investigate next and why?",
    "Is interest in meditation growing in Germany? Any data on that?",
    "Which of Spanish, Portuguese or Italian should we localize our cooking app into next?",
    "We sell a budgeting app. Is personal finance a growing topic among Vietnamese speakers?",
    "How has attention to electric bikes changed in Dutch over the past two years?",
    "Is the trend for 'cold plunge' real or just hype? Check a few languages.",
    "Give me a one-page PDF on how interest in chess has evolved in Turkish and Arabic.",
    "Rank these markets by how much people look up 'sourdough': Polish, Czech, Hungarian.",
    "Yesterday you analyzed astronomy for Ukrainian; now add Polish and use desktop traffic only.",
    "Is there an audience for board games content in Japanese? Growing or shrinking?",
    "Which topics related to sleep are gaining attention in French?",
    "We want evidence on whether demand for yoga is rising in Brazil before we build a course.",
]
SHOULD_NOT = [
    "Summarize the Wikipedia article on intermittent fasting for me.",
    "What does the Wikipedia article about astronomy say about exoplanets?",
    "Here is our sales CSV for Q3 — chart revenue by region.",
    "Our app's daily active users dropped 20% last month. What could be the cause?",
    "How do I call the Wikimedia REST API from Python?",
    "What is the population of Poland?",
    "Translate our landing page into Czech.",
    "Write a Polish marketing email for our astronomy course.",
    "What are the latest news about Wikipedia's funding?",
    "Give me a list of the most edited Wikipedia articles this week.",
    "Explain how Wikipedia decides which articles get deleted.",
    "Calculate the compound annual growth rate of 120 to 180 over three years.",
    "Which programming language should I learn for data analysis?",
    "Draft a survey to test willingness to pay for a language-learning app.",
]


def main() -> int:
    load_dotenv()
    if len(sys.argv) < 2 or sys.argv[1] not in PROVIDERS:
        raise SystemExit(f"usage: {sys.argv[0]} {{{'|'.join(PROVIDERS)}}}")
    url, key_env, default_model, rpm, tpm, _cap = PROVIDERS[sys.argv[1]]
    key = os.environ.get(key_env) or sys.exit(f"set {key_env}")
    model = os.environ.get("MODEL", default_model)
    chat = RateLimitedChat(url, key, model, int(os.environ.get("RPM", rpm)), tpm=tpm)

    front = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[1]
    name = re.search(r"^name:\s*(.+)$", front, re.M).group(1).strip()
    desc = re.search(r"^description:\s*(.+)$", front, re.M).group(1).strip()
    skills = {name: desc, **DISTRACTORS}
    listing = "\n".join(f"- {n}: {d}" for n, d in skills.items())
    system = ("You are an assistant with these skills installed. Given the user's request, reply with ONLY the name of the single "
              "skill you would load first, or the word none if no skill applies.\n\n" + listing)

    rows = []
    aborted = None
    try:
        for expected, prompts in ((name, SHOULD), ("none-or-other", SHOULD_NOT)):
            for pr in prompts:
                msg = chat([{"role": "system", "content": system}, {"role": "user", "content": pr}], tools=[])
                answer = (msg.get("content") or "").strip().strip("`*\"'").lower()
                picked = next((n for n in skills if n in answer), "none")
                ok = (picked == name) if expected == name else (picked != name)
                rows.append({"prompt": pr, "expected": expected, "picked": picked, "ok": ok})
                print(f"{'OK  ' if ok else 'MISS'} [{picked:18s}] {pr[:90]}")
    except ProviderError as e:  # quota or persistent provider failure: keep what was measured, do not traceback
        aborted = f"{e.status}: {e}"
        print(f"\nABORTED by provider after {len(rows)} of {len(SHOULD) + len(SHOULD_NOT)} requests — {aborted[:200]}")

    done_should = [r for r in rows if r["expected"] == name]
    done_not = [r for r in rows if r["expected"] != name]
    tp = sum(r["ok"] for r in done_should)
    tn = sum(r["ok"] for r in done_not)
    fp = len(done_not) - tn
    fn = len(done_should) - tp
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / len(done_should) if done_should else 0.0
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = EVALS / "results" / f"triggers-{sys.argv[1]}-{re.sub(r'[^a-z0-9.-]+', '-', model.lower())}-{stamp}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"model": model, "description": desc, "status": "aborted" if aborted else "completed",
                               "aborted": aborted, "completed_requests": len(rows), "recall": recall, "precision": precision,
                               "false_negatives": fn, "false_positives": fp, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\nrecall {tp}/{len(done_should)} = {recall:.2f}   precision {precision:.2f}   (false positives {fp}, false negatives {fn})"
          + ("   [partial: provider aborted the run]" if aborted else ""))
    print(f"saved {out.relative_to(ROOT)}")
    return 1 if aborted else 0


if __name__ == "__main__":
    sys.exit(main())
