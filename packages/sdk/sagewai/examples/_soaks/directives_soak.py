#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Soak B — Directive library across LLMs (atelier issue #9).

The directive layer is the moat that lets even the cheapest LLM behave
as a competent workflow agent. This soak proves it: pick one
classification task, run it through ``DirectiveEngine`` against several
LLMs of different sizes and providers, and publish the four numbers
that decide whether the "harness any LLM" claim survives a senior
engineer's bullshit detector — accuracy, $/call, p50/p99 latency, and
JSON output validity.

What's exercised:

- ``sagewai.directives.DirectiveEngine.resolve()`` for prompt
  preprocessing
- ``litellm.acompletion`` swap across Anthropic / OpenAI / Ollama
  (whichever credentials and local models are present)
- ``litellm.completion_cost`` for spend accounting
- A 50-sample held-out classification dataset with balanced ground
  truth across four urgency tiers
- Strict-JSON output contract — invalid JSON is failure, not a soft
  warning

Requirements::

    pip install sagewai
    # Optional, in any combination:
    #   - ANTHROPIC_API_KEY  → Claude Sonnet + Claude Haiku slots
    #   - OPENAI_API_KEY     → GPT-4o-mini slot
    #   - ollama serve       → up to two locally-pulled chat models

Usage::

    python -m sagewai.examples._soaks.directives_soak
    # or
    python packages/sdk/sagewai/examples/_soaks/directives_soak.py

Spend cap: every model run aborts when its per-model spend would cross
``$0.50``; the whole soak's hard cap is ``$2.00``. Both caps are
intentionally well under the issue's $10 budget so a CI re-run cannot
accidentally burn the user's account. Output JSON lands at
``$SAGEWAI_SOAK_RESULTS_PATH`` (default: ``~/.sagewai/directives-soak-results.json``).
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError

import litellm

from sagewai.directives import DirectiveEngine, DirectiveResult


# ── configuration ──────────────────────────────────────────────────


PER_MODEL_SPEND_CAP_USD = 0.50
TOTAL_SPEND_CAP_USD = 2.00
OLLAMA_TAGS_URL = "http://127.0.0.1:11434/api/tags"
OLLAMA_REQUEST_TIMEOUT_S = 60.0
PAID_REQUEST_TIMEOUT_S = 30.0


# ── dataset (50 emails, 4 tiers, balanced ground truth) ────────────


SAMPLES: list[tuple[str, str]] = [
    # ---- P0 (production down / data loss / security): 13 samples
    ("P0", "Subject: ENTIRE PLATFORM DOWN — every user reports 503 for 25 minutes; we are losing $5k/min."),
    ("P0", "Subject: Security breach — customer reports another tenant's data showing in their dashboard. Confirmed reproducible."),
    ("P0", "Subject: Database is corrupted after the migration. Customer data is being returned wrong across all reads."),
    ("P0", "Subject: Login system is completely broken — nobody can sign in. Started 10 minutes ago. ~3000 users affected."),
    ("P0", "Subject: Webhook signing keys are leaking in our outgoing emails. All customers may be exposed. Please advise immediately."),
    ("P0", "Subject: Production outage — APIs returning 500 across the board. Status page shows green but everything is failing."),
    ("P0", "Subject: Data loss — last night's backup is missing 4 hours of transaction records. Audit team needs answers today."),
    ("P0", "Subject: SSO integration bug is bypassing MFA. Auditors discovered it 30 minutes ago. Cannot wait."),
    ("P0", "Subject: Our payment processor returned $40k of duplicate charges to customers because of a webhook loop on your side."),
    ("P0", "Subject: GDPR delete request was logged as completed but the data is still queryable. Regulator asked us about this morning."),
    ("P0", "Subject: Ransomware-style behaviour — files in our shared workspace are being mass-renamed by what looks like an account inside your platform."),
    ("P0", "Subject: Critical CVE in your SDK; security team is blocking all deploys until we hear back. Need a patched version today."),
    ("P0", "Subject: All customer support tickets are vanishing as soon as they are submitted. Started 15 min ago. Cannot triage anything."),
    # ---- P1 (major feature broken, blocks workflow): 13 samples
    ("P1", "Subject: Cannot create new projects — the wizard fails on step 3 with 'invalid org id'. Affects all my team since this morning."),
    ("P1", "Subject: Our nightly export to S3 has been failing for three days straight; the BI team has no fresh data."),
    ("P1", "Subject: SAML login works for me as admin but every regular user gets a 403 after the redirect. We can't onboard anyone."),
    ("P1", "Subject: API rate limits dropped from 5000 to 50 RPM overnight. Our integration is now returning errors all day."),
    ("P1", "Subject: The agent-run feature crashes with 'context too long' on any document over 5 pages. Blocking our launch tomorrow."),
    ("P1", "Subject: Cannot invite teammates — invitation emails go out but the join link returns 'token invalid'. Tested with 3 different inboxes."),
    ("P1", "Subject: The CSV export for our weekly report is missing the 'amount' column since this morning. Finance will not accept the report."),
    ("P1", "Subject: Our dashboard widgets are all stuck on yesterday's data; refresh button does nothing. Whole sales team is blind right now."),
    ("P1", "Subject: Webhooks for the 'invoice.paid' event have stopped firing entirely since last night. We rely on this for fulfilment."),
    ("P1", "Subject: Our two-factor authentication is rejecting valid codes. Blocking my entire ops team. Cannot do anything sensitive today."),
    ("P1", "Subject: Bulk import of 12k customer records has been stuck at 14% for the last 5 hours; cancellation does nothing."),
    ("P1", "Subject: The mobile SDK build crashes the host app on iOS 18 — affects all our beta testers, blocking our App Store submission."),
    ("P1", "Subject: Our scheduled reports stopped sending Monday. Eight execs notice. We need this fixed before tomorrow's board meeting."),
    # ---- P2 (minor bug, workaround exists): 12 samples
    ("P2", "Subject: The 'Edit' button on the user list page opens the wrong record about 1 in 30 times. Refresh fixes it."),
    ("P2", "Subject: Pagination in the audit-log view skips one entry between page 4 and page 5. Not blocking, but confusing."),
    ("P2", "Subject: When I drag a card across two columns quickly, the destination column sometimes flickers grey for a second."),
    ("P2", "Subject: Search with a leading apostrophe (e.g. 'O'Brien') returns zero results. Escaping it works as a workaround."),
    ("P2", "Subject: The colour of the success toast is slightly different in dark mode vs light mode. Brand team flagged it for v2 polish."),
    ("P2", "Subject: Our weekly digest email shows the timezone as UTC even though my profile is set to CET. Times are correct otherwise."),
    ("P2", "Subject: Sorting the report table by 'created_at' descending sometimes orders today and yesterday inconsistently. Re-sort fixes it."),
    ("P2", "Subject: The keyboard shortcut Cmd+K opens the command palette but Ctrl+K doesn't (on macOS). Fine to use the mouse for now."),
    ("P2", "Subject: When I export a chart as PNG, the legend is cut off on the right. Resizing the browser window before export works."),
    ("P2", "Subject: The dark-mode toggle in the user menu doesn't persist across browser sessions. Manually toggling each session is fine."),
    ("P2", "Subject: An HTTP 408 appears in our logs every ~50 requests but the actual call retries and succeeds. Want to understand it."),
    ("P2", "Subject: The 'projects' filter dropdown on the activity page shows only the first 25 projects. We have 60. Workaround: search box."),
    # ---- P3 (feature request / billing / docs): 12 samples
    ("P3", "Subject: Could the API return total_count alongside paginated results? Would make our integration cleaner."),
    ("P3", "Subject: We'd love a Slack notification when a new agent run completes. Integration would be a big quality-of-life win."),
    ("P3", "Subject: Question on billing — is the fleet-worker count billed per-month or per-seat-active-day? Couldn't find it in the docs."),
    ("P3", "Subject: Idea — a 'duplicate workflow' button so I don't have to recreate similar workflows from scratch each time."),
    ("P3", "Subject: When will SOC 2 Type II report be available? Our procurement team is asking before they can renew our contract next quarter."),
    ("P3", "Subject: Can we get a webhook for project-archived events? Would let us trigger our own cleanup automation."),
    ("P3", "Subject: Could you add Russian and Brazilian-Portuguese to the email-template translations? Most of our users speak one of those."),
    ("P3", "Subject: Documentation question — the example for 'BatchRunner' uses an old API shape (v0.4); could you update it to v1.0?"),
    ("P3", "Subject: Suggestion — a per-user dark-mode preference that respects the system setting by default. Tiny ask, would love it."),
    ("P3", "Subject: Could the dashboard show cumulative spend this month next to today's spend? Would help me track our quarterly budget."),
    ("P3", "Subject: Can you publish a Postman collection for the API? Would speed up onboarding for the new engineers we're hiring."),
    ("P3", "Subject: Feature request — let me filter the activity log by 'actor type' (human vs agent vs system). Would tighten our audit reviews."),
]

assert sum(1 for t, _ in SAMPLES if t == "P0") == 13
assert sum(1 for t, _ in SAMPLES if t == "P1") == 13
assert sum(1 for t, _ in SAMPLES if t == "P2") == 12
assert sum(1 for t, _ in SAMPLES if t == "P3") == 12
assert len(SAMPLES) == 50


# ── prompt template (the one constant the directive layer rewrites) ─


SYSTEM_PROMPT = """You are a customer support triage assistant.

Classify the email into one urgency tier:
- P0: Production down, security breach, data loss. Same-hour response required.
- P1: Major feature broken, workflow blocked. Same-day response required.
- P2: Minor bug with a workaround, slow performance. 3-day response acceptable.
- P3: Feature request, billing question, documentation. Within-week response acceptable.

Output STRICT JSON with this exact shape and nothing else:
{"tier": "P0", "reason": "<one short sentence>"}

The "tier" value must be exactly one of "P0", "P1", "P2", "P3"."""

USER_TEMPLATE = """Triage timestamp: @datetime.

Email to classify:

{email}"""


# ── result records ─────────────────────────────────────────────────


@dataclass
class CallRecord:
    sample_index: int
    expected_tier: str
    predicted_tier: str | None
    json_valid: bool
    latency_ms: float
    cost_usd: float
    raw_output: str


@dataclass
class ModelReport:
    model: str
    samples_attempted: int
    samples_completed: int
    accuracy: float
    json_validity_rate: float
    p50_latency_ms: float
    p99_latency_ms: float
    avg_cost_usd: float
    total_cost_usd: float
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    failure_reason: str | None = None


# ── helpers ────────────────────────────────────────────────────────


def _ollama_chat_models(max_n: int = 2) -> list[str]:
    """Return up to ``max_n`` chat-tuned Ollama models, prefixed for litellm."""
    try:
        with urllib.request.urlopen(OLLAMA_TAGS_URL, timeout=1.5) as resp:
            data = json.loads(resp.read())
    except (URLError, OSError, json.JSONDecodeError):
        return []
    names = [m.get("name", "") for m in data.get("models", []) if m.get("name")]
    # Skip clearly code-tuned models — they perform poorly on natural-language
    # classification and would unfairly drag the directive-layer numbers.
    chat = [n for n in names if not any(t in n.lower() for t in ("coder", "code"))]
    # Prefer well-known small chat families first, in this order.
    priority = ("llama3.2", "llama3.1", "llama3", "qwen2.5", "qwen2", "mistral", "phi3", "gemma2", "gemma")
    chat.sort(
        key=lambda n: (
            next((i for i, p in enumerate(priority) if n.lower().startswith(p)), len(priority)),
            n,
        )
    )
    return chat[:max_n]


def _selected_models() -> list[str]:
    """Resolve which model strings to run against, in order."""
    selected: list[str] = []
    if os.environ.get("ANTHROPIC_API_KEY"):
        selected.append("claude-sonnet-4-6")
        selected.append("claude-haiku-4-5-20251001")
    if os.environ.get("OPENAI_API_KEY"):
        selected.append("openai/gpt-4o-mini")
    for ollama_name in _ollama_chat_models(max_n=3):
        selected.append(f"ollama/{ollama_name}")
    return selected


def _is_ollama(model: str) -> bool:
    return model.startswith("ollama/") or model.startswith("ollama_chat/")


def _parse_json_tier(raw: str) -> tuple[str | None, bool]:
    """Extract tier label from the model's raw output.

    Returns ``(tier_or_None, json_valid)``. If the model wrapped JSON in
    code fences or added trailing prose, we still try a best-effort
    bracket extraction — but ``json_valid`` is True only when the entire
    trimmed output parses as JSON cleanly.
    """
    text = raw.strip()
    # Strip common code-fence wrappers
    if text.startswith("```"):
        lines = [line for line in text.splitlines() if not line.startswith("```")]
        text = "\n".join(lines).strip()
    json_valid = False
    parsed: Any = None
    try:
        parsed = json.loads(text)
        json_valid = True
    except json.JSONDecodeError:
        # Best-effort: find first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                parsed = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                parsed = None
    if isinstance(parsed, dict):
        tier_raw = parsed.get("tier")
        if isinstance(tier_raw, str):
            tier = tier_raw.strip().upper()
            if tier in {"P0", "P1", "P2", "P3"}:
                return tier, json_valid
    return None, json_valid


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    s = sorted(values)
    idx = max(0, min(len(s) - 1, int(round(pct / 100.0 * (len(s) - 1)))))
    return s[idx]


def _safe_completion_cost(model: str, response: Any) -> float:
    """Best-effort cost extraction. Ollama → 0.0; failures → 0.0 with logging."""
    if _is_ollama(model):
        return 0.0
    try:
        return float(litellm.completion_cost(completion_response=response))
    except Exception:
        return 0.0


def _bar(frac: float, width: int = 18) -> str:
    filled = int(round(frac * width))
    return "█" * filled + "·" * (width - filled)


# ── one model's run ────────────────────────────────────────────────


async def run_one_model(
    model: str,
    engine: DirectiveEngine,
    samples: list[tuple[str, str]],
    *,
    on_call: Any | None = None,
) -> ModelReport:
    """Send every sample through the directive engine + LLM. Capture metrics."""
    is_local = _is_ollama(model)
    timeout = OLLAMA_REQUEST_TIMEOUT_S if is_local else PAID_REQUEST_TIMEOUT_S
    records: list[CallRecord] = []
    spend_so_far = 0.0
    failure_reason: str | None = None
    for idx, (expected_tier, email) in enumerate(samples):
        if not is_local and spend_so_far >= PER_MODEL_SPEND_CAP_USD:
            failure_reason = (
                f"per-model spend cap hit after {idx} samples "
                f"(${spend_so_far:.4f} >= ${PER_MODEL_SPEND_CAP_USD:.2f})"
            )
            break
        # Resolve directives in the user-side prompt so @datetime, etc. expand
        try:
            resolved: DirectiveResult = await engine.resolve(
                USER_TEMPLATE.replace("{email}", email)
            )
            user_text = resolved.prompt
        except Exception as exc:
            user_text = USER_TEMPLATE.replace("{email}", email).replace(
                "@datetime", datetime.now(tz=timezone.utc).isoformat(timespec="seconds")
            )
            if on_call:
                on_call(model, idx, "directive-fallback", str(exc))
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ]
        t0 = time.perf_counter()
        try:
            response = await asyncio.wait_for(
                litellm.acompletion(
                    model=model,
                    messages=messages,
                    temperature=0.0,
                    max_tokens=120,
                ),
                timeout=timeout + 5.0,
            )
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - t0) * 1000.0
            records.append(
                CallRecord(
                    sample_index=idx,
                    expected_tier=expected_tier,
                    predicted_tier=None,
                    json_valid=False,
                    latency_ms=elapsed_ms,
                    cost_usd=0.0,
                    raw_output=f"<error: {exc!r}>",
                )
            )
            if on_call:
                on_call(model, idx, expected_tier, f"ERROR {type(exc).__name__}")
            continue
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        try:
            raw = response.choices[0].message.content or ""
        except (AttributeError, IndexError):
            raw = ""
        tier, json_valid = _parse_json_tier(raw)
        cost = _safe_completion_cost(model, response)
        spend_so_far += cost
        records.append(
            CallRecord(
                sample_index=idx,
                expected_tier=expected_tier,
                predicted_tier=tier,
                json_valid=json_valid,
                latency_ms=elapsed_ms,
                cost_usd=cost,
                raw_output=raw,
            )
        )
        if on_call:
            mark = "✓" if tier == expected_tier else "✗"
            on_call(model, idx, expected_tier, f"{mark} pred={tier} json={json_valid} {elapsed_ms:.0f}ms")
    correct = sum(1 for r in records if r.predicted_tier == r.expected_tier)
    json_valid_count = sum(1 for r in records if r.json_valid)
    completed = len(records)
    latencies = [r.latency_ms for r in records]
    confusion: dict[str, dict[str, int]] = {}
    for tier in ("P0", "P1", "P2", "P3"):
        confusion[tier] = {p: 0 for p in ("P0", "P1", "P2", "P3", "INVALID")}
    for r in records:
        pred_key = r.predicted_tier if r.predicted_tier else "INVALID"
        confusion[r.expected_tier][pred_key] += 1
    return ModelReport(
        model=model,
        samples_attempted=completed,
        samples_completed=completed,
        accuracy=correct / completed if completed else 0.0,
        json_validity_rate=json_valid_count / completed if completed else 0.0,
        p50_latency_ms=statistics.median(latencies) if latencies else 0.0,
        p99_latency_ms=_percentile(latencies, 99.0),
        avg_cost_usd=spend_so_far / completed if completed else 0.0,
        total_cost_usd=spend_so_far,
        confusion=confusion,
        failure_reason=failure_reason,
    )


# ── entry point ────────────────────────────────────────────────────


def _print_progress(model: str, idx: int, expected: str, status: str) -> None:
    sys.stdout.write(f"\r  [{model:<40s}] {idx + 1:>3d}/50 {expected} {status:<48s}")
    sys.stdout.flush()


async def main() -> None:
    print("─" * 72)
    print(" Sagewai — directive-library soak (atelier issue #9, soak B)")
    print("─" * 72)
    print()

    models = _selected_models()
    if not models:
        print(
            "  No usable LLMs found.\n"
            "  Set ANTHROPIC_API_KEY / OPENAI_API_KEY, or run 'ollama serve'\n"
            "  with at least one chat-tuned model pulled (e.g. 'ollama pull gemma2:9b').\n"
            "  Aborting."
        )
        sys.exit(2)
    print("  Models selected for this run:")
    for m in models:
        print(f"    - {m}")
    print()
    print(f"  Dataset: {len(SAMPLES)} synthetic emails, balanced across P0–P3.")
    print(f"  Per-model spend cap: ${PER_MODEL_SPEND_CAP_USD:.2f} | Total cap: ${TOTAL_SPEND_CAP_USD:.2f}")
    print()

    engine = DirectiveEngine(model="gpt-4o-mini")  # any model_profile is fine here
    results: list[ModelReport] = []
    total_spend = 0.0

    for model in models:
        print(f"─── {model} ".ljust(72, "─"))
        report = await run_one_model(
            model,
            engine,
            SAMPLES,
            on_call=_print_progress,
        )
        sys.stdout.write("\n")
        results.append(report)
        total_spend += report.total_cost_usd
        if report.failure_reason:
            print(f"  ! halted early: {report.failure_reason}")
        print(
            f"  accuracy {report.accuracy * 100:5.1f}% "
            f"| json {report.json_validity_rate * 100:5.1f}% "
            f"| p50 {report.p50_latency_ms:>6.0f}ms "
            f"| p99 {report.p99_latency_ms:>6.0f}ms "
            f"| $/call ${report.avg_cost_usd:.6f} "
            f"| total ${report.total_cost_usd:.4f}"
        )
        print()
        if total_spend >= TOTAL_SPEND_CAP_USD:
            print(f"  ! total spend cap hit (${total_spend:.4f}); skipping remaining models")
            break

    print("─── The proof ".ljust(72, "─"))
    print()
    print(f"  {'model':<42s}  acc%   json%   p50ms   p99ms   $/call    total$")
    print(f"  {'-' * 42}  -----  ------  ------  ------  --------  --------")
    for r in results:
        print(
            f"  {r.model:<42s}  {r.accuracy * 100:>5.1f}  {r.json_validity_rate * 100:>6.1f}  "
            f"{r.p50_latency_ms:>6.0f}  {r.p99_latency_ms:>6.0f}  "
            f"{r.avg_cost_usd:>8.6f}  {r.total_cost_usd:>8.4f}"
        )
    print()
    print(f"  Total spend across {len(results)} model(s): ${total_spend:.4f} "
          f"(cap was ${TOTAL_SPEND_CAP_USD:.2f})")
    print()
    print("  Accuracy bars:")
    for r in results:
        print(f"    {r.model:<42s} {_bar(r.accuracy)} {r.accuracy * 100:>5.1f}%")
    print()

    out_path = Path(
        os.environ.get("SAGEWAI_SOAK_RESULTS_PATH")
        or (Path.home() / ".sagewai" / "directives-soak-results.json")
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "soak": "directives",
        "issue": "atelier#9",
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "dataset_size": len(SAMPLES),
        "tier_balance": {
            "P0": sum(1 for t, _ in SAMPLES if t == "P0"),
            "P1": sum(1 for t, _ in SAMPLES if t == "P1"),
            "P2": sum(1 for t, _ in SAMPLES if t == "P2"),
            "P3": sum(1 for t, _ in SAMPLES if t == "P3"),
        },
        "per_model_spend_cap_usd": PER_MODEL_SPEND_CAP_USD,
        "total_spend_cap_usd": TOTAL_SPEND_CAP_USD,
        "total_spend_usd": total_spend,
        "models": [asdict(r) for r in results],
    }
    out_path.write_text(json.dumps(payload, indent=2, default=str))
    print(f"  Raw results: {out_path}")
    print()
    print("  Next step: paste the per-model table into")
    print("  sagewai/atelier:docs/v1.0/directives-soak-report.md (template lives")
    print("  in this script's sibling README).")


if __name__ == "__main__":
    asyncio.run(main())
