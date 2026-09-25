#!/usr/bin/env python3
"""Experiment 02 — can a free/cheap model follow SKILL.md and call a tool?

This is the gating risk for the whole testing plan: the assignment requires
the complete scenario to be tested with a cheap model, and we have to do it
at zero cost. We test the *actual* mechanism a skill relies on:
  system prompt = SKILL.md body, one `bash` tool, a realistic user request
  -> does the model emit a bash tool call with the right analyze.py command?
Then we feed back a realistic tool result and read how it interprets it.

Usage (standard library only; set the key for the provider you try):
  GROQ_API_KEY=...        python experiments/exp02_free_model_toolcall.py groq
  GEMINI_API_KEY=...      python experiments/exp02_free_model_toolcall.py gemini
  OPENROUTER_API_KEY=...  python experiments/exp02_free_model_toolcall.py openrouter
Override the model with MODEL=... (provider catalogs change monthly).
Run count: RUNS=3 by default (rate limits!).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].strip()

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "openai/gpt-oss-120b"),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "GEMINI_API_KEY",
        "gemini-3.6-flash",
    ),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions", "OPENROUTER_API_KEY", "nvidia/nemotron-3-ultra-550b-a55b:free"),
}

BASH_TOOL = {
    "type": "function",
    "function": {
        "name": "bash",
        "description": "Run a shell command in the skill directory and return its stdout/stderr.",
        "parameters": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]},
    },
}

SYSTEM = (
    "You are an AI assistant with a `bash` tool. The following skill is available and its "
    "files live in the current working directory:\n\n" + SKILL
)

PROMPTS = [
    (
        "pl_cs_fasting",
        "Compare the growth of interest in intermittent fasting in Polish-language and Czech-language "
        "Wikipedia over the last two years.",
        r"analyze\.py.*--topic\s+[\"']?intermittent fasting[\"']?.*--langs\s+(pl\s+cs|cs\s+pl)",
    ),
    (
        "uk_astronomy",
        "We are thinking about adding an astronomy course to an educational app. Is interest in this "
        "topic growing in Ukrainian-language Wikipedia, and how much can this growth be trusted?",
        r"analyze\.py.*--topic\s+[\"']?astronomy[\"']?.*--langs\s+uk\b",
    ),
]

# A realistic, compact tool result (shape = what analyze.py will print).
FAKE_RESULT = {
    "topic": "intermittent fasting",
    "window": "2024-09-01..2026-08-31",
    "agent": "user",
    "languages": {
        "pl": {
            "article": "Post przerywany",
            "verdict": "growing",
            "magnitude": "moderate",
            "trust": {
                "score": 85,
                "label": "high",
                "reasons": [
                    "Noticeable spikes: 12% of views came from 4 spike day(s) (largest: 2026-01-07 with 9,812 views)."
                ],
            },
            "growth": {"yoy": 0.21, "annualized_trend": 0.18},
            "trend_test": {"months_up": 10, "pairs": 12, "p_value": 0.009},
            "volume": {"median_monthly": 24100},
            "share_of_project": {"median_per_million": 96.3},
        },
        "cs": {
            "article": "Přerušovaný půst",
            "verdict": "unclear",
            "magnitude": "moderate",
            "trust": {
                "score": 40,
                "label": "medium",
                "reasons": [
                    "Low volume: median ~2,600 views/month; treat percentage changes as indicative only.",
                    "The direction is not distinguishable from noise (seasonal Mann–Kendall p=0.39).",
                ],
            },
            "growth": {"yoy": 0.27, "annualized_trend": 0.15},
            "trend_test": {"months_up": 7, "pairs": 12, "p_value": 0.39},
            "volume": {"median_monthly": 2600},
            "share_of_project": {"median_per_million": 51.0},
        },
    },
    "charts": ["out/intermittent-fasting/chart_monthly.png", "out/intermittent-fasting/chart_share.png"],
}


def post(url: str, key: str, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/kolibri753/wikipedia-interest",
            "X-Title": "wikipedia-interest skill eval",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise SystemExit(f"HTTP {e.code}: {body[:500]}")


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in PROVIDERS:
        raise SystemExit(f"usage: {sys.argv[0]} {{{'|'.join(PROVIDERS)}}}")
    url, key_env, default_model = PROVIDERS[sys.argv[1]]
    key = os.environ.get(key_env) or sys.exit(f"set {key_env}")
    model = os.environ.get("MODEL", default_model)
    runs = int(os.environ.get("RUNS", "3"))
    results = []
    first_assistant_msg = None  # the provider's own message object, echoed back verbatim in turn 2
    for pid, prompt, pattern in PROMPTS:
        for run in range(runs):
            t0 = time.perf_counter()
            resp = post(
                url,
                key,
                {
                    "model": model,
                    "temperature": 0.2,
                    "tools": [BASH_TOOL],
                    "tool_choice": "auto",
                    "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}],
                },
            )
            msg = resp["choices"][0]["message"]
            calls = msg.get("tool_calls") or []
            cmd = ""
            if calls:
                try:
                    cmd = json.loads(calls[0]["function"]["arguments"]).get("command", "")
                except json.JSONDecodeError:
                    cmd = "<malformed JSON arguments>"
            ok = bool(re.search(pattern, cmd))
            if pid == "pl_cs_fasting" and calls and first_assistant_msg is None:
                first_assistant_msg = msg
            usage = resp.get("usage", {})
            results.append(
                {
                    "prompt": pid,
                    "run": run,
                    "tool_call": bool(calls),
                    "command": cmd,
                    "correct": ok,
                    "seconds": round(time.perf_counter() - t0, 1),
                    "prompt_tokens": usage.get("prompt_tokens"),
                    "text": (msg.get("content") or "")[:200],
                }
            )
            print(
                f"[{pid} #{run}] tool_call={bool(calls)} correct={ok} {results[-1]['seconds']}s "
                f"prompt_tokens={usage.get('prompt_tokens')}\n   cmd: {cmd[:160]}"
            )
            time.sleep(3)

    # Second turn: interpretation of a realistic result (read this one by eye).
    # Harness rule learned from Gemini: echo the provider's assistant message *verbatim*
    # (it may carry provider-specific fields such as thought signatures); never rebuild it.
    print("\n=== interpretation turn (manual review) ===")
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": PROMPTS[0][1]}]
    if first_assistant_msg and first_assistant_msg.get("tool_calls"):
        call_id = first_assistant_msg["tool_calls"][0]["id"]
        messages += [first_assistant_msg, {"role": "tool", "tool_call_id": call_id, "content": json.dumps(FAKE_RESULT)}]
    else:  # provider produced no tool call earlier: present the result as plain user content instead
        messages += [
            {
                "role": "user",
                "content": 'Output of `python scripts/analyze.py --topic "intermittent fasting" --langs pl cs`:\n'
                + json.dumps(FAKE_RESULT),
            }
        ]
    resp = post(url, key, {"model": model, "temperature": 0.2, "tools": [BASH_TOOL], "messages": messages})
    answer = resp["choices"][0]["message"].get("content") or "<no text; tool_calls=%s>" % resp["choices"][0][
        "message"
    ].get("tool_calls")
    print(answer)
    # Cheap automatic checks on the interpretation.
    # Causal attribution of spikes is the failure we saw in the first Gemini run
    # ("likely linked to New Year health/diet news coverage"). The data says when, not why.
    causal = re.compile(
        r"(likely|probably|possibly|may|might|could|due to|caused by|linked to|because of|"
        r"attributed to|coincid)",
        re.I,
    )
    spiky = [s_ for s_ in re.split(r"(?<=[.!?])\s+", answer) if re.search(r"spike|peak|surge|jump", s_, re.I)]
    unsupported = [
        s_
        for s_ in spiky
        if causal.search(s_) and not re.search(r"worth checking|hypothesis|verify|unknown|not in the data", s_, re.I)
    ]
    checks = {
        "no_unsupported_spike_causes": not unsupported,
        "mentions_trust_or_reliab": bool(re.search(r"trust|reliab|confiden", answer, re.I)),
        "mentions_low_volume_for_cs": bool(re.search(r"volume|2,?600|small|thin", answer, re.I)),
        "does_not_call_cs_growing": not re.search(r"Czech[^.]{0,80}\bgrowing\b", answer, re.I),
        "mentions_articles_used": bool(re.search(r"Post przerywany|Přerušovaný", answer)),
        "mentions_proxy_caveat": bool(re.search(r"proxy|willingness to pay|not (a )?market", answer, re.I)),
    }
    print("\nchecks:", json.dumps(checks, indent=1))
    for s_ in unsupported:
        print("  unsupported causal claim:", s_.strip()[:200])
    out = {
        "provider": sys.argv[1],
        "model": model,
        "tool_call_results": results,
        "interpretation": answer,
        "checks": checks,
    }
    Path(ROOT / "experiments" / f"exp02_{sys.argv[1]}.json").write_text(
        json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8"
    )
    n_ok = sum(r["correct"] for r in results)
    print(f"\n{n_ok}/{len(results)} correct tool calls. Saved experiments/exp02_{sys.argv[1]}.json — paste it back.")


if __name__ == "__main__":
    main()
