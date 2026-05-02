#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 42 — Customer support triage agent (drop-in for tonight).

Your CTO told you to "add AI to the product this quarter." You picked
the most painful inbox on the team — customer support — and you have
one weekend.

This example is the agent that triages your inbox tonight. For every
incoming email it:

1. Classifies the urgency tier (P0 / P1 / P2 / P3) with a one-sentence
   reason a human can audit.
2. Decides auto-respond vs escalate based on the tier.
3. Drafts the response for the auto-respondable ones — short
   acknowledgement for P2, full reply for P3.

What's exercised:

- ``sagewai.directives.DirectiveEngine`` for prompt preprocessing
  (``@datetime`` resolves to "now"; the directive surface is where
  ``@context``, ``@memory``, ``/tool.name`` plug in next)
- ``litellm.acompletion`` as the LLM-swap point — same code runs
  against Claude Haiku, GPT-4o-mini, or Ollama with a single env-var
  swap
- Strict-JSON output contract (the soak in
  ``packages/sdk/sagewai/examples/_soaks/directives_soak.py`` measured
  100% JSON validity across three local 7B-class models)

This is **not** a demo of a chatbot. It is the production shape of a
single workflow that used to need a person, automated correctly, with
cost and quality both measurable. Drop the same code in front of your
Zendesk webhook tonight and start the auto-respond pile tomorrow
morning.

Requirements::

    pip install sagewai
    # Default path — free, runs anywhere:
    ollama pull llama3.2
    # Optional swaps (any combination):
    #   export ANTHROPIC_API_KEY=sk-ant-...   # Claude Haiku 4.5
    #   export OPENAI_API_KEY=sk-...          # GPT-4o-mini

Usage::

    python 42_support_triage_agent.py
    python 42_support_triage_agent.py --primary claude-haiku-4-5-20251001
    python 42_support_triage_agent.py --primary ollama/llama3.2:latest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import URLError

import litellm

from sagewai.directives import DirectiveEngine, DirectiveResult


# ── the inbox: 6 representative emails the agent triages on every run ─


INBOX: list[tuple[str, str]] = [
    # (true_tier_for_audit_only, email_body)
    (
        "P0",
        "Subject: ENTIRE PLATFORM DOWN — every user reports 503 for 25 minutes; "
        "we are losing $5k/min.",
    ),
    (
        "P0",
        "Subject: Security breach — customer reports another tenant's data "
        "showing in their dashboard. Confirmed reproducible.",
    ),
    (
        "P1",
        "Subject: Cannot create new projects — the wizard fails on step 3 with "
        "'invalid org id'. Affects all my team since this morning.",
    ),
    (
        "P2",
        "Subject: Pagination in the audit-log view skips one entry between page "
        "4 and page 5. Not blocking, but confusing.",
    ),
    (
        "P3",
        "Subject: Could the API return total_count alongside paginated results? "
        "Would make our integration cleaner.",
    ),
    (
        "P3",
        "Subject: Documentation question — the example for 'BatchRunner' uses "
        "an old API shape (v0.4); could you update it to v1.0?",
    ),
]


# ── one prompt, one shape, every LLM ───────────────────────────────


SYSTEM_PROMPT = """You are a customer support triage assistant.

For each email, classify and respond:

Tier definitions:
- P0: Production down, security breach, data loss. Same-hour response.
- P1: Major feature broken, workflow blocked. Same-day response.
- P2: Minor bug with a workaround, slow performance. 3-day response.
- P3: Feature request, billing question, documentation. Within-week.

Drafting rules:
- For P0 or P1: do NOT draft a response. The human takes over. Set
  draft_response to "" (empty string).
- For P2: draft a brief 1-sentence acknowledgement. The human will
  follow up.
- For P3: draft a complete friendly reply in 2-3 sentences.

Output STRICT JSON with this exact shape and nothing else:
{
  "tier": "P0",
  "reason": "<one short sentence justifying the tier>",
  "draft_response": "<empty for P0/P1, brief for P2, full for P3>"
}"""

USER_TEMPLATE = """Triage timestamp: @datetime.

Email to classify:

{email}"""


@dataclass
class TriageResult:
    tier: str | None
    reason: str
    draft_response: str
    json_valid: bool
    latency_ms: float
    cost_usd: float
    raw_output: str


@dataclass
class RoutingDecision:
    label: str  # "auto-respond" | "draft + escalate" | "escalate (no draft)"
    icon: str   # short emoji-free symbol for tier severity
    color: str  # ANSI colour code
    auto_respond: bool


ROUTING: dict[str, RoutingDecision] = {
    "P0": RoutingDecision("escalate (no draft)", "!!", "\033[31m", False),
    "P1": RoutingDecision("escalate (no draft)", " !", "\033[33m", False),
    "P2": RoutingDecision("draft + escalate",    " ~", "\033[36m", False),
    "P3": RoutingDecision("auto-respond",        " *", "\033[32m", True),
}
RESET = "\033[0m"


# ── LLM auto-detection ────────────────────────────────────────────


def _ollama_first_chat_model() -> str | None:
    try:
        with urllib.request.urlopen(
            "http://127.0.0.1:11434/api/tags", timeout=1.5
        ) as resp:
            data = json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError):
        return None
    names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    chat = [n for n in names if not any(t in n.lower() for t in ("coder", "code"))]
    priority = ("llama3.2", "llama3.1", "qwen2.5", "mistral", "gemma2", "phi3")
    chat.sort(
        key=lambda n: next(
            (i for i, p in enumerate(priority) if n.lower().startswith(p)),
            len(priority),
        )
    )
    return chat[0] if chat else None


def _available_llms() -> list[str]:
    out: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        out.append("claude-haiku-4-5-20251001")
    if os.environ.get("OPENAI_API_KEY"):
        out.append("openai/gpt-4o-mini")
    olm = _ollama_first_chat_model()
    if olm:
        out.append(f"ollama/{olm}")
    return out


def _is_ollama(model: str) -> bool:
    return model.startswith("ollama/") or model.startswith("ollama_chat/")


# ── the core: one triage call ──────────────────────────────────────


async def triage(
    email: str,
    *,
    model: str,
    engine: DirectiveEngine,
) -> TriageResult:
    """Triage one email — classify, route, draft. Single LLM call."""
    try:
        resolved: DirectiveResult = await engine.resolve(
            USER_TEMPLATE.replace("{email}", email)
        )
        user_text = resolved.prompt
    except Exception:
        # Directive engine is best-effort; fall through to a manual @datetime
        user_text = USER_TEMPLATE.replace("{email}", email).replace(
            "@datetime", datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
        )
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_text},
    ]
    timeout = 60.0 if _is_ollama(model) else 30.0
    t0 = time.perf_counter()
    try:
        response = await asyncio.wait_for(
            litellm.acompletion(
                model=model,
                messages=messages,
                temperature=0.0,
                max_tokens=240,
            ),
            timeout=timeout + 5.0,
        )
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        return TriageResult(
            tier=None,
            reason=f"LLM call failed: {type(exc).__name__}",
            draft_response="",
            json_valid=False,
            latency_ms=elapsed_ms,
            cost_usd=0.0,
            raw_output=repr(exc),
        )
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    raw = ""
    try:
        raw = response.choices[0].message.content or ""
    except (AttributeError, IndexError):
        pass
    cost = 0.0
    if not _is_ollama(model):
        try:
            cost = float(litellm.completion_cost(completion_response=response))
        except Exception:
            cost = 0.0
    text = raw.strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines() if not line.startswith("```")
        ).strip()
    parsed: dict[str, Any] | None = None
    json_valid = False
    try:
        parsed = json.loads(text)
        json_valid = True
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
    tier: str | None = None
    reason = ""
    draft = ""
    if isinstance(parsed, dict):
        tier_raw = parsed.get("tier")
        if isinstance(tier_raw, str) and tier_raw.strip().upper() in ROUTING:
            tier = tier_raw.strip().upper()
        reason = str(parsed.get("reason") or "").strip()
        draft = str(parsed.get("draft_response") or "").strip()
    return TriageResult(
        tier=tier,
        reason=reason,
        draft_response=draft,
        json_valid=json_valid,
        latency_ms=elapsed_ms,
        cost_usd=cost,
        raw_output=raw,
    )


# ── pretty-printing the inbox decisions ────────────────────────────


def _truncate(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _wrap(text: str, width: int, indent: str = "      ") -> str:
    """Naive wrap — split on whitespace at width boundary."""
    out_lines: list[str] = []
    current = ""
    for word in text.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            out_lines.append(current)
            current = word
    if current:
        out_lines.append(current)
    return f"\n{indent}".join(out_lines)


def _print_triage(idx: int, true_tier: str, email: str, result: TriageResult) -> None:
    decision = ROUTING.get(result.tier or "", None)
    if decision is None:
        print(f"  ?? {idx + 1}. tier=INVALID — {_truncate(email, 64)}")
        print(f"      → ESCALATE (model output unparsable; raw: {result.raw_output[:80]!r})")
        return
    headline = _truncate(email.replace("Subject: ", ""), 64)
    audit = f"(human-labelled {true_tier})" if true_tier != result.tier else ""
    print(f"  {decision.color}{decision.icon} {result.tier}{RESET}  {headline}  {audit}")
    print(f"      reason: {result.reason}")
    print(f"      → {decision.label.upper()}")
    if result.draft_response:
        wrapped = _wrap(result.draft_response, 64)
        print(f"      draft:  {wrapped}")
    print()


# ── the agent's full pass over the inbox ───────────────────────────


async def run_inbox(
    model: str, engine: DirectiveEngine
) -> tuple[list[TriageResult], dict[str, Any]]:
    """Triage every email in the INBOX with the given model. Return summary."""
    results: list[TriageResult] = []
    for idx, (true_tier, email) in enumerate(INBOX):
        r = await triage(email, model=model, engine=engine)
        results.append(r)
        _print_triage(idx, true_tier, email, r)
    correct = sum(
        1 for (tt, _), r in zip(INBOX, results) if r.tier == tt
    )
    auto = sum(1 for r in results if r.tier == "P3")
    escalated = sum(1 for r in results if r.tier in {"P0", "P1"})
    drafted = sum(1 for r in results if r.tier in {"P2", "P3"})
    total_cost = sum(r.cost_usd for r in results)
    latencies = [r.latency_ms for r in results]
    summary = {
        "model": model,
        "emails": len(results),
        "agreement_with_audit_labels": correct,
        "auto_respond": auto,
        "escalated_no_draft": escalated,
        "drafted": drafted,
        "json_valid": sum(1 for r in results if r.json_valid),
        "total_cost_usd": total_cost,
        "avg_cost_per_email": total_cost / max(len(results), 1),
        "p50_latency_ms": statistics.median(latencies) if latencies else 0.0,
        "elapsed_s": sum(latencies) / 1000.0,
    }
    return results, summary


# ── the swap-proof: rerun a tiny subset on alternate LLMs ──────────


async def swap_proof(
    primary: str,
    alternates: list[str],
    primary_results: list[TriageResult],
    engine: DirectiveEngine,
) -> dict[str, dict[str, Any]]:
    """Take 2 representative emails, rerun on each alternate LLM, compare."""
    if not alternates:
        return {}
    # Pick first P0 and first P3 (cleanest separation in the dataset)
    chosen_idx: list[int] = []
    for tier in ("P0", "P3"):
        for i, (tt, _) in enumerate(INBOX):
            if tt == tier:
                chosen_idx.append(i)
                break
    swap_summary: dict[str, dict[str, Any]] = {}
    for alt in alternates:
        same_decisions = 0
        total_cost = 0.0
        for i in chosen_idx:
            r = await triage(INBOX[i][1], model=alt, engine=engine)
            if r.tier == primary_results[i].tier:
                same_decisions += 1
            total_cost += r.cost_usd
        swap_summary[alt] = {
            "samples": len(chosen_idx),
            "same_routing_decision_as_primary": same_decisions,
            "total_cost_usd": total_cost,
        }
    return swap_summary


# ── monthly-cost forecaster: the CFO line ──────────────────────────


def _forecast_monthly(model: str, avg_cost_per_email: float) -> str:
    if avg_cost_per_email <= 0:
        return f"{model:<42s}  $0.00/day  $0.00/month  (local — no per-call cost)"
    daily_200 = avg_cost_per_email * 200
    monthly = daily_200 * 30
    return (
        f"{model:<42s}  ${daily_200:>5.2f}/day  ${monthly:>6.2f}/month  "
        f"(at 200 emails/day)"
    )


# ── entry point ────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--primary",
        type=str,
        default=None,
        help="LLM to use for the full inbox pass (auto-detected if omitted).",
    )
    args = parser.parse_args()

    print("─" * 72)
    print(" Sagewai — customer support triage agent (example 42)")
    print("─" * 72)
    print()

    available = _available_llms()
    if not available:
        print(
            "  No LLMs available.\n"
            "  Either:\n"
            "    - export ANTHROPIC_API_KEY or OPENAI_API_KEY, or\n"
            "    - run 'ollama serve' with a chat model pulled "
            "('ollama pull llama3.2')."
        )
        sys.exit(2)

    primary = args.primary or available[0]
    if primary not in available:
        print(f"  Primary model '{primary}' not in available list: {available}")
        sys.exit(2)
    alternates = [m for m in available if m != primary]

    print(f"  Primary model:  {primary}")
    if alternates:
        print(f"  Swap proof on:  {', '.join(alternates)}")
    print(f"  Inbox size:     {len(INBOX)} email(s)")
    print()
    print("─── Triaging the inbox ".ljust(72, "─"))
    print()

    engine = DirectiveEngine(model=primary)
    primary_results, primary_summary = await run_inbox(primary, engine)
    swap = await swap_proof(primary, alternates, primary_results, engine)

    print("─── The proof ".ljust(72, "─"))
    print()
    print(
        f"  {primary_summary['emails']} email(s) triaged in "
        f"{primary_summary['elapsed_s']:.1f}s using {primary}"
    )
    print(
        f"    {primary_summary['auto_respond']} auto-respond, "
        f"{primary_summary['escalated_no_draft']} escalated, "
        f"{primary_summary['drafted']} drafted"
    )
    print(
        f"    JSON validity: "
        f"{primary_summary['json_valid']}/{primary_summary['emails']}"
    )
    print(
        f"    Agreement with human-labelled tiers: "
        f"{primary_summary['agreement_with_audit_labels']}/{primary_summary['emails']}"
    )
    print(f"    Total spend: ${primary_summary['total_cost_usd']:.6f}")
    print()

    if swap:
        print("  Same code, swap LLMs (2 representative emails per alternate):")
        for model_name, s in swap.items():
            cost = s["total_cost_usd"]
            cost_str = f"${cost:.6f}" if cost else "free"
            print(
                f"    {model_name:<42s}  agreement "
                f"{s['same_routing_decision_as_primary']}/{s['samples']}  "
                f"spend {cost_str}"
            )
        print()

    print("  Monthly forecast at 200 emails/day:")
    print(f"    {_forecast_monthly(primary, primary_summary['avg_cost_per_email'])}")
    for model_name, s in swap.items():
        avg = s["total_cost_usd"] / max(s["samples"], 1)
        print(f"    {_forecast_monthly(model_name, avg)}")
    print()
    print(
        "  → Drop the same code in front of your Zendesk webhook tonight; the\n"
        "    auto-respond pile is yours tomorrow morning."
    )


if __name__ == "__main__":
    asyncio.run(main())
