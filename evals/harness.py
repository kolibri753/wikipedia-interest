#!/usr/bin/env python3
"""End-to-end evals of the skill with a cheap model.

What this reproduces: the mechanism every skill-aware agent uses. The model
receives SKILL.md as instructions plus tools, decides which script to run, the
harness *actually runs it*, the model reads the real output and answers. Then
each transcript is graded with programmatic checks drawn from failures we saw
in the live probes (invented spike causes, wrong Wikidata concept, ungrounded
numbers, calling a missing article "growing").

Zero cost: any OpenAI-compatible endpoint with tool calling. Defaults are the
free tiers of Gemini (5 requests/min) / Groq / OpenRouter. Data requests are
served from evals/fixtures/cache and recorded there on first miss, so repeated
runs are deterministic and do not re-hit Wikimedia.

Usage (from the skill directory):
    GEMINI_API_KEY=... python evals/harness.py gemini                 # all scenarios
    GEMINI_API_KEY=... python evals/harness.py gemini --only uk_astronomy
    MODEL=... RPM=... override the model id and the request rate.
Results: evals/results/<provider>-<model>-<timestamp>/{summary.md,summary.json,transcript_*.json}
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from wiki_interest.env import load_dotenv  # noqa: E402

EVALS = ROOT / "evals"
FIXTURES = EVALS / "fixtures" / "cache"
TODAY = "2026-09-23"  # pinned so URLs (and fixtures) are stable across days
MAX_STEPS = 10
TOOL_OUTPUT_LIMIT = 14_000  # chars of script output shown to the model
READ_FILE_LIMIT = 6_000  # analysis.json is ~40 KB; under an 8k-token cap a full read crowds out the summary

# Free tiers as observed: (url, key env var, default model, requests/min, tokens/min budget, max tokens per request)
# Groq's free gpt-oss-120b has an 8,000 tokens-per-minute budget which is also the cap for a single request.
PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY", "openai/gpt-oss-120b", 25, 8000, 8000),
    "gemini": (
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
        "GEMINI_API_KEY",
        "gemini-3.6-flash",
        5,
        None,
        None,
    ),
    "openrouter": (
        "https://openrouter.ai/api/v1/chat/completions",
        "OPENROUTER_API_KEY",
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        15,
        None,
        None,
    ),
}


class ProviderError(RuntimeError):
    """The provider, not the model's behaviour, ended the scenario.
    status: provider_error (5xx/429 exhausted after retries) | context_limit (one request too large)
            | quota_exhausted (daily/billing quota: retrying is pointless)"""

    def __init__(self, status: str, detail: str):
        super().__init__(detail)
        self.status = status


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_script",
            "description": "Run one of the skill's scripts in the working directory. Only `python scripts/analyze.py ...` "
            "and `python scripts/report.py ...` are allowed. Returns exit code, stdout and stderr.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": 'e.g. python scripts/analyze.py --topic "astronomy" --langs uk',
                    }
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file from the working directory (e.g. out/<run>/analysis.json). Large files are truncated.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write a text file in the working directory (e.g. notes.md for report.py).",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
        },
    },
]

PREAMBLE = (
    "You are an AI assistant helping a founder of a B2C product. You have three tools: run_script (runs the skill's "
    "scripts), read_file and write_file. The skill below is installed; its files live in the working directory. Follow "
    "its instructions. When you have the answer, reply to the user in plain prose (no tool call).\n\n"
)


# --------------------------------------------------------------------------- LLM client
class RateLimitedChat:
    """Paces requests on two dimensions free tiers actually enforce: requests per
    minute and tokens per minute (Groq: 8,000 TPM for gpt-oss-120b). The token
    budget is a sliding 60-second window fed by the request estimate before the
    call and corrected with the provider's usage report after it."""

    def __init__(
        self, url: str, key: str, model: str, rpm: int, tpm: int | None = None, log: Callable[[str], None] = print
    ):
        self.url, self.key, self.model, self.log = url, key, model, log
        self.min_interval = 60.0 / max(1, rpm)
        self.tpm = tpm
        self._last = 0.0
        self._window: list[tuple[float, int]] = []  # (time, tokens)
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    def _wait_for_token_budget(self, est: int) -> None:
        if not self.tpm:
            return
        while True:
            now = time.monotonic()
            self._window = [(t, n) for t, n in self._window if now - t < 60]
            used = sum(n for _, n in self._window)
            if used + est <= self.tpm or not self._window:
                return
            wait = 60 - (now - self._window[0][0]) + 0.5
            self.log(f"    token budget: {used}+{est} > {self.tpm}/min; waiting {wait:.0f}s")
            time.sleep(wait)

    def __call__(self, messages: list[dict], tools: list[dict]) -> dict:
        payload = {"model": self.model, "temperature": 0.2, "messages": messages, "tools": tools, "tool_choice": "auto"}
        est = estimate_tokens(messages) + 600  # + tool schema and a typical completion
        for attempt in range(6):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._wait_for_token_budget(est)
            self._last = time.monotonic()
            self._window.append((self._last, est))
            req = urllib.request.Request(
                self.url,
                data=json.dumps(payload).encode("utf-8"),
                method="POST",
                headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "User-Agent": "wikipedia-interest-eval/1.0",  # Groq's edge returns 403 without one
                    "HTTP-Referer": "https://github.com/kolibri753/wikipedia-interest",
                    "X-Title": "wikipedia-interest evals",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=180) as r:
                    data = json.loads(r.read().decode("utf-8"))
                self.calls += 1
                usage = data.get("usage") or {}
                self.prompt_tokens += usage.get("prompt_tokens") or 0
                self.completion_tokens += usage.get("completion_tokens") or 0
                actual = (usage.get("prompt_tokens") or 0) + (usage.get("completion_tokens") or 0)
                if actual:
                    self._window[-1] = (self._window[-1][0], actual)
                return data["choices"][0]["message"]
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")
                if e.code == 413 or re.search(r"Requested \d+", body) or "reduce your message size" in body:
                    raise ProviderError("context_limit", f"HTTP {e.code}: {body[:300]}") from None
                if (
                    e.code == 429
                    and re.search(r"exceeded your current quota|billing details|quota exceeded for metric", body, re.I)
                    and not re.search(r"per minute|retry in|try again in", body, re.I)
                ):
                    raise ProviderError("quota_exhausted", f"HTTP 429 (daily/billing quota): {body[:300]}") from None
                if e.code in (429, 500, 502, 503, 504) and attempt < 5:
                    delay = _retry_delay(e.headers, body) or 20.0 * (2**attempt)
                    self.log(f"    HTTP {e.code}; retrying in {delay:.0f}s")
                    time.sleep(min(delay, 180))
                    continue
                raise ProviderError("provider_error", f"HTTP {e.code}: {body[:300]}") from None
            except (urllib.error.URLError, TimeoutError) as e:
                if attempt < 5:
                    time.sleep(20.0 * (2**attempt))
                    continue
                raise ProviderError("provider_error", repr(e)) from None
        raise ProviderError("provider_error", "gave up after retries")


def _retry_delay(headers, body: str) -> float | None:
    ra = headers.get("Retry-After") if headers else None
    if ra:
        try:
            return float(ra)
        except ValueError:
            pass
    m = re.search(r"(?:retry|try again) in (?:~)?(\d+(?:\.\d+)?)\s*(ms|s)\b", body, re.I)
    if m:
        v = float(m.group(1))
        return (v / 1000 if m.group(2) == "ms" else v) + 1
    return None


# --------------------------------------------------------------------------- tools
def run_script(command: str, workdir: Path) -> str:
    """Execute an allowed skill script with the model's arguments. Data comes
    from the fixtures cache (recorded on first miss); --today is pinned."""
    try:
        argv = shlex.split(command, posix=True)
    except ValueError as e:
        return json.dumps({"exit_code": 1, "error": f"could not parse command: {e}"})
    if argv and argv[0] in ("python", "python3", "py"):
        argv = argv[1:]
    if (
        not argv
        or Path(argv[0].replace("\\", "/")).name not in ("analyze.py", "report.py")
        or not argv[0].replace("\\", "/").startswith("scripts/")
    ):
        return json.dumps(
            {
                "exit_code": 1,
                "error": "only `python scripts/analyze.py ...` and `python scripts/report.py ...` are allowed here",
            }
        )
    script = ROOT / "scripts" / Path(argv[0]).name
    args = argv[1:]
    if script.name == "analyze.py" and "--today" not in args:
        args += ["--today", TODAY]
    env = {**os.environ, "WIKI_INTEREST_CACHE": str(FIXTURES), "PYTHONUTF8": "1"}
    t0 = time.perf_counter()
    res = subprocess.run(
        [sys.executable, str(script), *args],
        cwd=workdir,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )
    out = res.stdout
    if len(out) > TOOL_OUTPUT_LIMIT:
        out = out[:TOOL_OUTPUT_LIMIT] + f"\n... [truncated {len(res.stdout) - TOOL_OUTPUT_LIMIT} chars]"
    return json.dumps(
        {
            "exit_code": res.returncode,
            "stdout": out,
            "stderr": res.stderr[-2000:],
            "seconds": round(time.perf_counter() - t0, 1),
        },
        ensure_ascii=False,
    )


def read_file(path: str, workdir: Path) -> str:
    target = (workdir / path).resolve()
    if workdir.resolve() not in target.parents:
        return json.dumps({"error": "path must stay inside the working directory"})
    if not target.exists():
        return json.dumps({"error": f"no such file: {path}"})
    text = target.read_text(encoding="utf-8", errors="replace")
    if len(text) > READ_FILE_LIMIT:
        text = text[:READ_FILE_LIMIT] + (
            f"\n... [truncated {len(text) - READ_FILE_LIMIT} chars; the compact summary printed by "
            f"analyze.py already contains every figure needed for an answer]"
        )
    return json.dumps({"path": path, "content": text}, ensure_ascii=False)


def write_file(path: str, content: str, workdir: Path) -> str:
    target = (workdir / path).resolve()
    if workdir.resolve() not in target.parents and target != workdir.resolve():
        return json.dumps({"error": "path must stay inside the working directory"})
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return json.dumps({"written": str(target.relative_to(workdir)), "chars": len(content)})


# --------------------------------------------------------------------------- context management
STUB_CHARS = 400


KEEP_FULL = 2  # script outputs kept in full when the budget allows (broad + narrow concept, or two turns)


def compact_messages(messages: list[dict], max_tokens: int | None) -> list[dict]:
    """What the model is sent. The stored transcript keeps everything. Script
    outputs are the bulk of the context, so the last KEEP_FULL of them stay in
    full and older ones become small valid-JSON stubs that keep the pointers a
    later step needs (files, concept, per-language verdicts). If a provider has
    a per-request cap (Groq free tier: 8k tokens) and the estimate is still over
    85% of it, older full outputs are stubbed one by one, then the latest is
    truncated. A live Gemini run showed the cost of stubbing too eagerly: the
    model tried to read analysis.json back and re-ran the analysis to recover
    numbers it had just lost. read_file exists now, and two outputs stay."""
    call_names: dict[str, str] = {}
    for m in messages:
        for c in m.get("tool_calls") or []:
            call_names[c["id"]] = c["function"]["name"]
    script_idx = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "tool" and call_names.get(m.get("tool_call_id")) == "run_script"
    ]
    read_idx = [
        i
        for i, m in enumerate(messages)
        if m.get("role") == "tool" and call_names.get(m.get("tool_call_id")) == "read_file"
    ]
    full_set = set(script_idx[-KEEP_FULL:]) | set(read_idx[-1:])
    view = [dict(m) for m in messages]
    for i in script_idx + read_idx:
        if i not in full_set and len(view[i].get("content", "")) > STUB_CHARS:
            view[i]["content"] = _stub(view[i]["content"])
    if max_tokens:
        budget_chars = int(max_tokens * 0.85 * 4)
        candidates = sorted(full_set)  # oldest first
        while candidates[:-1] and sum(len(json.dumps(m, ensure_ascii=False)) for m in view) > budget_chars:
            i = candidates.pop(0)
            view[i]["content"] = _stub(view[i]["content"])
        excess = sum(len(json.dumps(m, ensure_ascii=False)) for m in view) - budget_chars
        if excess > 0 and candidates:
            i = candidates[-1]
            view[i]["content"] = _truncate(view[i]["content"], max(1500, len(view[i]["content"]) - excess))
    return view


def _truncate(content: str, keep: int) -> str:
    """Shorten a tool result to about `keep` chars. Our results are JSON envelopes
    ({"stdout": ...} or {"content": ...}); cut the inner text so the envelope
    stays valid JSON instead of handing the model a half-cut object."""
    note = " ... [truncated to fit the model's request limit]"
    try:
        d = json.loads(content)
        for key in ("stdout", "content"):
            if isinstance(d.get(key), str) and len(d[key]) > 200:
                overhead = len(content) - len(d[key])
                d[key] = d[key][: max(200, keep - overhead - len(note))] + note
                return json.dumps(d, ensure_ascii=False)
    except json.JSONDecodeError:
        pass
    return content[:keep] + note


def _stub(content: str) -> str:
    """A superseded script result, shortened but still valid JSON, keeping what a
    later step may need to refer back to."""
    try:
        d = json.loads(content)
    except json.JSONDecodeError:
        return content[:STUB_CHARS] + " ... [earlier output shortened; superseded]"
    stub = {"exit_code": d.get("exit_code"), "note": "earlier output shortened; superseded by a later result"}
    try:
        out = json.loads(d.get("stdout") or "")
        if isinstance(out, dict):
            for k in ("error", "topic", "qid", "label", "files", "report"):
                if k in out:
                    stub[k] = out[k]
            if "languages" in out:
                stub["languages"] = {
                    lg: {
                        "article": v.get("article"),
                        "verdict": v.get("verdict"),
                        "trust": (v.get("trust") or {}).get("score"),
                    }
                    for lg, v in out["languages"].items()
                }
            if out.get("missing"):
                stub["missing"] = list(out["missing"])
    except (json.JSONDecodeError, AttributeError, TypeError):
        stub["stdout_head"] = (d.get("stdout") or "")[:STUB_CHARS]
    return json.dumps(stub, ensure_ascii=False)


def estimate_tokens(messages: list[dict]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages) // 4


# --------------------------------------------------------------------------- agent loop
def run_scenario(
    sc: dict, chat: Callable, system: str, workdir: Path, log: Callable[[str], None], max_tokens: int | None = None
) -> dict:
    """Runs the scenario; never raises. Provider failures set transcript["status"]
    and keep whatever tool calls already happened."""
    workdir.mkdir(parents=True, exist_ok=True)
    messages = [{"role": "system", "content": system}]
    transcript = {
        "id": sc["id"],
        "turns": [],
        "tool_calls": [],
        "tool_outputs": [],
        "finals": [],
        "steps": 0,
        "errors": [],
        "status": "completed",
        "max_request_tokens_est": 0,
    }
    for user_turn in sc["turns"]:
        messages.append({"role": "user", "content": user_turn})
        transcript["turns"].append(user_turn)
        for _ in range(MAX_STEPS):
            transcript["steps"] += 1
            view = compact_messages(messages, max_tokens)
            transcript["max_request_tokens_est"] = max(transcript["max_request_tokens_est"], estimate_tokens(view))
            try:
                msg = chat(view, TOOLS)
            except ProviderError as e:
                transcript["status"] = e.status
                transcript["errors"].append(str(e))
                transcript["finals"].append("")
                transcript["messages"] = messages
                return transcript
            messages.append(msg)  # verbatim: providers may attach signatures
            calls = msg.get("tool_calls") or []
            if not calls:
                transcript["finals"].append(msg.get("content") or "")
                break
            for call in calls:
                fn = call["function"]["name"]
                try:
                    args = json.loads(call["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                    transcript["errors"].append(f"malformed tool arguments: {call['function'].get('arguments')!r}")
                if fn == "run_script":
                    log(f"    $ {args.get('command', '')[:140]}")
                    result = run_script(args.get("command", ""), workdir)
                    transcript["tool_calls"].append({"name": fn, "command": args.get("command", "")})
                elif fn == "read_file":
                    log(f"    read {args.get('path')}")
                    result = read_file(args.get("path", ""), workdir)
                    transcript["tool_calls"].append({"name": fn, "path": args.get("path")})
                elif fn == "write_file":
                    log(f"    write {args.get('path')}")
                    result = write_file(args.get("path", "notes.md"), args.get("content", ""), workdir)
                    transcript["tool_calls"].append(
                        {"name": fn, "path": args.get("path"), "content": args.get("content", "")}
                    )
                else:
                    result = json.dumps({"error": f"unknown tool {fn}"})
                transcript["tool_outputs"].append(result)
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        else:
            transcript["errors"].append("step limit reached without a final answer")
            transcript["finals"].append("")
    transcript["messages"] = messages
    return transcript


# --------------------------------------------------------------------------- checks
def sentences(text: str) -> list[str]:
    """Split on sentence ends (allowing closing markdown/quote marks after the
    punctuation) and on line breaks, so list items and table rows are judged
    one at a time. A live false positive came from a split that ignored
    `topics.*` and swallowed the next paragraph."""
    parts = re.split(r"(?<=[.!?])[*_\"”')\]]*\s+|\n+", text)
    return [p.strip() for p in parts if p and p.strip()]


MOVEMENT = re.compile(
    r"\b(spike|spikes|peak|peaked|surge|surged|jump|jumped|drop|dropped|fell|fall|decline|declined|declines|declining|dip|dipped|rise|rose|uptick|upswing|downturn|slump|plunge|plunged|increase|increased|decrease|decreased|step[- ]?down|level shift)\b",
    re.I,
)
ATTRIBUTION = re.compile(
    r"\b(due to|caused by|linked to|tied to|because of|attributed to|attributable to|explained by|coincid\w*|align(?:s|ed|ing)? with|in line with|corresponds? to|driven by|reflect(?:s|ing)?|stems? from|result(?:s|ing)? from|thanks to|triggered by|likely|probably|possibly|presumably|suggesting that|indicating that)\b",
    re.I,
)
EXTERNAL_CAUSE = re.compile(
    r"\b(news|press|coverage|headlines?|holidays?|new year|tv|television|broadcast|social media|viral|exams?|school(?: year| term)?|academic (?:year|term|calendar)|semester|covid|pandemic|google|ai|chatbots?|answer boxes?|war|elections?|celebrit\w+|documentary|documentaries|policy|policies|marketing|campaigns?|weather|competing (?:resources|apps|sites)|curriculum|trend on tiktok|influencer)\b",
    re.I,
)
HEDGE = re.compile(
    r"\b(worth checking|hypothes\w*|verify|verified|validate|validation|unknown|not (?:in|shown by) the data|cannot (?:tell|say|confirm)|check what|would need|to confirm|the data (?:does not|doesn't) (?:say|show))\b",
    re.I,
)
MEASURED_CAUSE = re.compile(
    r"\b(reclassif\w+|bot[- ]classif\w+|classifier|redirects?|spike days?|level shift|edition[- ]wide|whole edition)\b",
    re.I,
)
MARKET_CLAIM = re.compile(
    r"\b(receptive|willing(?:ness)? to (?:pay|buy)|purchase intent|demand (?:is|exists|for)|customers? (?:want|are|will)|will (?:pay|buy|convert|subscribe)|monetiz\w+|market size|product[- ]market fit|latent demand|ready (?:for|to buy)|appetite for)\b",
    re.I,
)
MARKET_INFERENCE = re.compile(
    r"\b(indicat\w+|suggest\w+|show\w*|mean\w*|signal\w*|point\w* to|implies|imply|confirm\w*|prov\w+)\b", re.I
)
MARKET_CAVEAT = re.compile(
    r"\b(not (?:a )?(?:measure of )?demand|not willingness|(?:does|do|did) not (?:mean|imply|show|measure|prove)|cannot (?:show|tell|prove)|proxy|attention, not|rather than demand|validate|confirm|test(?:ing)?|survey|landing page|before (?:assuming|concluding))\b",
    re.I,
)


def unsupported_spike_causes(text: str) -> list[str]:
    """Sentences that explain a movement by an external cause the data cannot
    contain, without hedging it as something to verify. Causes the scripts do
    measure (reclassification, redirects, spike days, edition-wide shifts) are
    fine."""
    out = []
    for sent in sentences(text):
        if not (MOVEMENT.search(sent) and ATTRIBUTION.search(sent) and EXTERNAL_CAUSE.search(sent)):
            continue
        if HEDGE.search(sent):
            continue
        # if the only "cause" words are measured ones, it is a relay of trust.notes, not an invention
        ext = [m.group(0) for m in EXTERNAL_CAUSE.finditer(sent)]
        if ext and MEASURED_CAUSE.search(sent) and all(re.fullmatch(r"(?i)ai|policy|policies", e) for e in ext):
            continue
        out.append(sent[:220])
    return out


def unsupported_market_inferences(text: str) -> list[str]:
    """Sentences that read demand, receptiveness or willingness to pay off
    pageviews as a finding. Saying what to validate and how is fine; saying
    what the audience wants is not — pageviews cannot show it."""
    out = []
    for sent in sentences(text):
        if MARKET_CLAIM.search(sent) and MARKET_INFERENCE.search(sent) and not MARKET_CAVEAT.search(sent):
            out.append(sent[:220])
    return out


def _tool_output_values(outputs: list[str]) -> list[float]:
    text = " ".join(outputs).replace(",", "")
    vals = []
    for m in re.finditer(r"-?\d+(?:\.\d+)?", text):
        try:
            vals.append(float(m.group()))
        except ValueError:
            pass
    return vals


def _rounding_tolerance(n: float) -> float:
    """Half a unit in the last significant digit of the number as written: '1 200'
    is precise to hundreds (±50), '16,400' to hundreds, '8,617' to units. Human
    rounding of a tool value is grounded; an invented figure is not."""
    digits = re.sub(r"[^0-9]", "", str(int(abs(n)))) if abs(n) >= 1 else ""
    if not digits:
        return 0.5
    stripped = digits.rstrip("0")
    trailing_zeros = len(digits) - len(stripped)
    return max(0.5, 0.5 * 10**trailing_zeros)


def _grounded(n: float, vals: list[float], as_percent: bool) -> bool:
    """A number in the answer is grounded if some tool-output value matches it
    within rounding: significant-figure tolerance or 2% for plain numbers; for
    percentages, either a value already in percent or a fraction*100 within
    ±1.5 points."""
    for v in vals:
        if as_percent:
            if abs(abs(v) - abs(n)) <= 1.5 or abs(abs(v) * 100 - abs(n)) <= 1.5:
                return True
        elif abs(v - n) <= max(1.0, 0.02 * abs(n), _rounding_tolerance(n)):
            return True
    return False


BOUND_BELOW = re.compile(r"(?:<|≤|under|below|less than|fewer than|up to|at most|no more than)\s*$", re.I)
BOUND_ABOVE = re.compile(r"(?:>|≥|over|above|more than|at least|exceed(?:s|ing)?)\s*$", re.I)


def _bounded(n: float, vals: list[float], before: str) -> bool:
    """'<650 views/month' is grounded if some tool value is below 650 and 650 is a
    tight bound for it (within 1.5x); symmetric for 'over'/'at least'."""
    if BOUND_BELOW.search(before):
        return any(v <= n <= 1.5 * v for v in vals if v > 0)
    if BOUND_ABOVE.search(before):
        return any(v >= n >= 0.5 * v for v in vals if v > 0)
    return False


def ungrounded_numbers(final: str, outputs: list[str]) -> list[str]:
    vals = _tool_output_values(outputs)
    bad = []
    for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s?%", final):
        before = final[max(0, m.start() - 14) : m.start()]
        if not _grounded(float(m.group(1)), vals, as_percent=True) and not _bounded(
            float(m.group(1)), [v * 100 for v in vals if -5 <= v <= 5] + vals, before
        ):
            bad.append(m.group())
    # thousands may be separated by comma, thin/no-break space or a plain space ("16 366")
    for m in re.finditer(r"(?<![\d.Q#/])(\d{1,3}(?:[,\u202f\u00a0 ]\d{3})+|\d{3,})(?![\d%])", final):
        raw = re.sub(r"[,\u202f\u00a0 ]", "", m.group(1))
        before = final[max(0, m.start() - 14) : m.start()]
        if re.fullmatch(r"20\d\d", raw) or "/" in before[-3:]:  # years; denominators such as 40/100
            continue
        n = float(raw)
        if not _grounded(n, vals, as_percent=False) and not _bounded(n, vals, before):
            bad.append(m.group())
    return bad


def grade(sc: dict, tr: dict) -> list[dict]:
    final = "\n".join(tr["finals"])
    commands = [c.get("command", "") for c in tr["tool_calls"] if c["name"] == "run_script"]
    written = [c for c in tr["tool_calls"] if c["name"] == "write_file"]
    results = []

    def add(name, passed, evidence):
        results.append({"text": name, "passed": bool(passed), "evidence": evidence[:300]})

    for chk in sc.get("checks", []):
        t = chk["type"]
        if t == "command_regex":
            hits = [c for c in commands if re.search(chk["pattern"], c, re.I)]
            add(chk["name"], hits, hits[0] if hits else f"commands: {commands}")
        elif t == "final_regex":
            m = re.search(chk["pattern"], final, re.I | re.S)
            add(chk["name"], m, m.group(0)[:200] if m else "no match in final answer")
        elif t == "final_not_regex":
            m = re.search(chk["pattern"], final, re.I | re.S)
            add(chk["name"], not m, m.group(0)[:200] if m else "ok")
        elif t == "any_of":
            ok = any(re.search(p, final, re.I | re.S) for p in chk["final_patterns"]) or any(
                re.search(p, c, re.I) for p in chk.get("command_patterns", []) for c in commands
            )
            add(chk["name"], ok, "matched" if ok else "none of the alternatives matched")
        elif t == "no_unsupported_spike_causes":
            bad = [b for f in tr["finals"] for b in unsupported_spike_causes(f)]
            add(chk["name"], not bad, bad[0] if bad else "no external cause asserted for spikes/drops without hedging")
        elif t == "no_unsupported_market_inference":
            bad = [b for f in tr["finals"] for b in unsupported_market_inferences(f)]
            add(chk["name"], not bad, bad[0] if bad else "no demand/receptiveness read off pageviews as a finding")
        elif t == "numbers_grounded":
            bad = ungrounded_numbers(final, tr["tool_outputs"])
            add(
                chk["name"],
                not bad,
                f"not in any tool output: {bad}" if bad else "all percentages/large numbers appear in tool outputs",
            )
        elif t == "file_exists":
            hits = glob.glob(str(tr["_workdir"] / chk["glob"]), recursive=True)
            add(chk["name"], hits, hits[0] if hits else f"no file matching {chk['glob']}")
        elif t == "wrote_file_regex":
            hits = [w for w in written if re.search(chk["pattern"], w.get("content", ""), re.I | re.S)]
            add(chk["name"], hits, f"{len(written)} file(s) written" if written else "no write_file call")
        elif t == "max_tool_calls":
            add(chk["name"], len(commands) <= chk["n"], f"{len(commands)} script calls")
        elif t == "finished":
            add(
                chk["name"],
                all(tr["finals"]) and not tr["errors"],
                "; ".join(tr["errors"]) or "final answer produced for every turn",
            )
    return results


# --------------------------------------------------------------------------- main
def main() -> int:
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("provider", choices=sorted(PROVIDERS))
    ap.add_argument("--only", nargs="*", help="scenario ids")
    ap.add_argument("--evals", default=str(EVALS / "evals.json"))
    a = ap.parse_args()
    url, key_env, default_model, default_rpm, default_tpm, default_cap = PROVIDERS[a.provider]
    key = os.environ.get(key_env) or sys.exit(f"set {key_env}")
    model = os.environ.get("MODEL", default_model)
    rpm = int(os.environ.get("RPM", default_rpm))
    tpm = int(os.environ["TPM"]) if os.environ.get("TPM") else default_tpm  # tokens-per-minute budget
    max_tokens = int(os.environ["MAX_REQUEST_TOKENS"]) if os.environ.get("MAX_REQUEST_TOKENS") else (default_cap or tpm)

    skill_body = (ROOT / "SKILL.md").read_text(encoding="utf-8").split("---", 2)[2].strip()
    system = PREAMBLE + skill_body
    evals = json.loads(Path(a.evals).read_text(encoding="utf-8"))
    scenarios = [s for s in evals["evals"] if not a.only or s["id"] in a.only]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = EVALS / "results" / f"{a.provider}-{re.sub(r'[^a-z0-9.-]+', '-', model.lower())}-{stamp}"
    run_dir.mkdir(parents=True, exist_ok=True)
    chat = RateLimitedChat(url, key, model, rpm, tpm=tpm)
    print(
        f"model={model} rpm={rpm} tpm={tpm} max_request_tokens={max_tokens} scenarios={len(scenarios)} results={run_dir.relative_to(ROOT)}"
    )

    summary = {"provider": a.provider, "model": model, "started": stamp, "scenarios": []}
    for sc in scenarios:
        print(f"\n[{sc['id']}] {sc['turns'][0][:100]}")
        workdir = run_dir / sc["id"]
        t0 = time.perf_counter()
        calls_before, ptok_before = chat.calls, chat.prompt_tokens
        tr = run_scenario(sc, chat, system, workdir, print, max_tokens=max_tokens)
        tr["_workdir"] = workdir
        graded = grade(sc, tr) if tr["status"] == "completed" else []
        tr.pop("_workdir")
        passed = sum(g["passed"] for g in graded)
        rec = {
            "id": sc["id"],
            "status": tr["status"],
            "passed": passed,
            "total": len(graded),
            "checks": graded,
            "model_calls": chat.calls - calls_before,
            "prompt_tokens": chat.prompt_tokens - ptok_before,
            "max_request_tokens_est": tr["max_request_tokens_est"],
            "seconds": round(time.perf_counter() - t0, 1),
            "steps": tr["steps"],
            "errors": tr["errors"],
            "tool_calls": len(tr["tool_calls"]),
        }
        summary["scenarios"].append(rec)
        (workdir.parent / f"transcript_{sc['id']}.json").write_text(
            json.dumps(tr, indent=1, ensure_ascii=False, default=str), encoding="utf-8"
        )
        if tr["status"] != "completed":
            print(f"    ABORTED ({tr['status']}) after {len(tr['tool_calls'])} tool call(s): {tr['errors'][-1][:160]}")
            print("    -> not graded; re-run this scenario with --only " + sc["id"])
            if tr["status"] == "quota_exhausted":
                print("    provider quota is exhausted; stopping the run")
                summary["scenarios"] += [
                    {
                        "id": x["id"],
                        "status": "not_run",
                        "passed": 0,
                        "total": 0,
                        "checks": [],
                        "model_calls": 0,
                        "prompt_tokens": 0,
                        "max_request_tokens_est": 0,
                        "seconds": 0,
                        "steps": 0,
                        "errors": [],
                        "tool_calls": 0,
                    }
                    for x in scenarios[scenarios.index(sc) + 1 :]
                ]
                break
            continue
        print(
            f"    {passed}/{len(graded)} checks passed, {rec['model_calls']} model calls, ~{rec['max_request_tokens_est']} tokens max request, {rec['seconds']}s"
        )
        for g in graded:
            if not g["passed"]:
                print(f"      FAIL {g['text']}: {g['evidence']}")
        if tr["finals"]:
            print("    final answer (tail):", (tr["finals"][-1] or "")[-400:].replace("\n", " "))

    done = [x for x in summary["scenarios"] if x["status"] == "completed"]
    aborted = [x for x in summary["scenarios"] if x["status"] != "completed"]
    summary["totals"] = {
        "passed": sum(x["passed"] for x in done),
        "total": sum(x["total"] for x in done),
        "completed": len(done),
        "aborted": len(aborted),
        "model_calls": chat.calls,
        "prompt_tokens": chat.prompt_tokens,
        "completion_tokens": chat.completion_tokens,
    }
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=1, ensure_ascii=False), encoding="utf-8")
    md = [
        f"# Eval run — {a.provider} / {model} — {stamp}",
        "",
        "| scenario | status | checks | model calls | max request tokens (est) | s |",
        "|---|---|---|---|---|---|",
    ]
    for x in summary["scenarios"]:
        checks = f"{x['passed']}/{x['total']}" if x["status"] == "completed" else "—"
        md.append(
            f"| {x['id']} | {x['status']} | {checks} | {x['model_calls']} | {x['max_request_tokens_est']} | {x['seconds']} |"
        )
    md += [
        "",
        f"**{summary['totals']['passed']}/{summary['totals']['total']} checks passed over {len(done)} completed scenario(s)**; "
        f"{len(aborted)} aborted by the provider (not graded). {chat.calls} model calls, "
        f"{chat.prompt_tokens} prompt + {chat.completion_tokens} completion tokens.",
        "",
        "## Failed checks",
    ]
    for x in done:
        for g in x["checks"]:
            if not g["passed"]:
                md.append(f"- `{x['id']}` **{g['text']}** — {g['evidence']}")
    if aborted:
        md += ["", "## Aborted (provider)"]
        for x in aborted:
            md.append(
                f"- `{x['id']}` {x['status']} after {x['tool_calls']} tool call(s): {x['errors'][-1][:200] if x['errors'] else ''}"
            )
    (run_dir / "summary.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(
        f"\n{summary['totals']['passed']}/{summary['totals']['total']} checks passed ({len(done)} completed, {len(aborted)} aborted). "
        f"See {run_dir.relative_to(ROOT)}/summary.md"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
