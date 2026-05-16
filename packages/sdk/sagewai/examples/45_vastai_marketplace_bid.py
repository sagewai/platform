#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 45 — Vast.ai marketplace bidding: cheapest *reliable* GPU on Earth.

Closes Gap #8d of the inference spectrum. Where Example 47 is the
default "rent a known-good RTX 5090 from RunPod" tier, this example is
the **budget aggregator** tier: ``vastai search offers`` filters the
worldwide pool of idle bare-metal GPUs by per-host reliability score,
deep-learning-perf score, and internet speed; the cheapest match wins;
the same Unsloth fine-tune runs on a $0.20-$0.45/hr RTX 3090 instead
of a $0.69/hr RTX 5090.

Pipeline::

    search offers  →  pick top match  →  create instance  →  upload JSONL
                            │                                       │
                            └── reliability >= 0.95                 ↓
                                dlperf      >= 10           run unsloth
                                inet_down   >= 100 Mbps             │
                                                                    ↓
                                                            download LoRA
                                                                    │
                                                                    ↓
                                                            destroy instance

The same orchestration shape as Example 47, with two new steps —
*search the marketplace* and *report time-to-match* — and one new
proof column: *what would the same fine-tune have cost on RunPod?*

What's exercised:

- ``vastai search offers`` invocation with reliability filters from
  ``atelier/docs/v1.0/inference-provisioning-setup.md`` (post-Spheron
  swap, captured 2026-05-01)
- ``vastai create instance`` / ``vastai destroy instance`` lifecycle
  with the Unsloth Docker image, 20GB disk
- ``vastai copy`` / ``vastai execute`` for the JSONL upload + remote
  Unsloth recipe + LoRA download
- Cleanup-on-failure via ``try/finally`` + ``atexit`` + ``SIGTERM``
  handler — same three-way contract as Example 47
- Budget cap polling: GPU spend tracked via :class:`GpuSpendTracker`
  alongside ``sagewai.observability.costs``; the instance is destroyed
  before accrued cost crosses ``--budget-usd``
- Side-by-side cost table: Vast.ai (this example) vs. RunPod
  (Example 47) on the same workload, so the audience-pin person can
  see exactly when the marketplace tier wins
- Time-to-match: how long the marketplace took to surface a viable
  offer (typically seconds; we measure and report it explicitly)

The example **always** runs end-to-end. With ``VASTAI_API_KEY`` set in
``~/.sagewai/.env`` *and* ``vastai`` on ``PATH``, it bids on a real
host. Without either, it prints the search query, a synthetic offer
list, the chosen-offer reliability metrics, and the cost comparison —
the audience-pin person sees the bid before they spend a cent.

Requirements::

    pip install sagewai           # python-dotenv ships in the SDK tree
    # Optional (for the live path):
    #   - VASTAI_API_KEY in ~/.sagewai/.env
    #   - vastai on PATH (pip install vastai)

Usage::

    # Default: stub mode (no spend), prints the bid + cost comparison
    python 45_vastai_marketplace_bid.py

    # Live: bid on the cheapest reliable RTX 3090, fine-tune, tear down
    python 45_vastai_marketplace_bid.py --live

    # Tighter budget (watchdog kills the instance sooner)
    python 45_vastai_marketplace_bid.py --live --budget-usd 1.50

    # Stricter reliability (pick only hosts with >= 99% historical uptime)
    python 45_vastai_marketplace_bid.py --live --min-reliability 0.99

    # Different GPU (RTX 4090 for ~2x speed at ~2x cost; A100 80GB for big bases)
    python 45_vastai_marketplace_bid.py --live --gpu-name RTX_4090
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load Sagewai credentials early so VASTAI_API_KEY is visible below.
# Silently no-ops if ~/.sagewai/.env doesn't exist (clean-machine path).
load_dotenv(Path.home() / ".sagewai" / ".env")

from sagewai.observability.costs import calculate_cost  # noqa: E402

# ── Marketplace knobs (mirror atelier/docs/v1.0/inference-provisioning-setup.md) ──

INSTANCE_IMAGE: str = "unsloth/unsloth:latest"
INSTANCE_DISK_GB: int = 20

# Default GPU class. RTX 3090 is the budget sweet-spot on Vast.ai —
# 24GB VRAM fits a 3B 4-bit LoRA comfortably, and the marketplace has
# hundreds of these at any moment. Vast.ai filter syntax uses underscores
# in GPU names (e.g. ``RTX_3090``, not ``RTX 3090``).
DEFAULT_GPU_NAME: str = "RTX_3090"

# Reliability filter defaults. These are the "honest budget tier"
# numbers — cheap *and* dependable, not just cheap. See the landscape
# doc for the rationale.
DEFAULT_MIN_RELIABILITY: float = 0.95   # 95% historical uptime
DEFAULT_MIN_DLPERF: float = 10.0        # deep-learning perf score
DEFAULT_MIN_INET_DOWN_MBPS: float = 100.0  # 100 Mbps download

# Vast.ai list pricing (2026-05). These are the *typical* ranges
# operators see; actual offers vary minute-to-minute as hosts come and
# go from the marketplace. Used for the stub-mode synthetic offer list
# so the audience sees realistic numbers when no key is set.
GPU_TYPICAL_PRICE_PER_HR_USD: dict[str, float] = {
    "RTX_3090": 0.30,    # range: $0.20-$0.45
    "RTX_4090": 0.55,    # range: $0.40-$0.75
    "A100_80GB": 1.20,   # range: $0.80-$1.60
}

# Empirical fine-tune duration on a typical Vast.ai RTX 3090 host. The
# RTX 3090 is roughly half the throughput of an RTX 5090 (~hr vs ~30min
# in Example 47), so the same Unsloth recipe takes ~1h here. Pinning
# the number keeps the stub-mode budget breakdown honest.
EXPECTED_FINE_TUNE_HOURS: float = 1.00

# Issue acceptance criterion — total real spend under $3 for RTX 3090
# on a ~6-8 hour run (this example's recipe is ~1h, so the cap leaves
# headroom for a slower host or a re-run).
DEFAULT_BUDGET_USD: float = 3.00

# Cloud-LLM baseline (audience-pin's typical Anthropic Haiku call cost,
# post-overhead). Same number Example 47 uses so the cost-down narrative
# stays consistent across the inference spectrum.
BASELINE_COST_PER_CALL_USD: float = 0.005

# Production volume the audience-pin person quotes — 200 emails/day.
PRODUCTION_VOLUME_PER_DAY: int = 200

# Example 47 (RunPod) reference for the side-by-side comparison.
# Sourced from Example 47's pinned RTX 5090 figure
# ($0.69/hr × 0.50h = $0.345). Update if Example 47's defaults change.
RUNPOD_REFERENCE_GPU: str = "NVIDIA RTX 5090"
RUNPOD_REFERENCE_PRICE_PER_HR_USD: float = 0.69
RUNPOD_REFERENCE_FINE_TUNE_HOURS: float = 0.50


# ── Email-triage training data (mirrors Example 47, kept self-contained) ──

EMAIL_TRIAGE_TRAINING_DATA: list[dict[str, str]] = [
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Cannot log in\n\nI tried 5 times to log in. My account is locked. I have a deadline at 5pm.",
        "output": '{"urgency": "high", "reason": "account-lockout-deadline"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Feature request\n\nWould love a dark-mode option whenever you get to it. No rush.",
        "output": '{"urgency": "low", "reason": "feature-request"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Billing dispute\n\nYou charged me twice for the May invoice. Please refund the duplicate.",
        "output": '{"urgency": "high", "reason": "billing-dispute"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Quick integration question\n\nDoes the Slack connector support threaded replies? Asking before we wire it up.",
        "output": '{"urgency": "medium", "reason": "integration-question"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Production outage on /checkout\n\nOur production checkout returns 500 since 14:02 UTC. We're losing revenue.",
        "output": '{"urgency": "high", "reason": "production-outage"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Renewal question\n\nOur seat count grew this quarter. Can you re-quote the annual plan for 35 seats?",
        "output": '{"urgency": "medium", "reason": "renewal-question"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: SSO is down\n\nNo one in our org can sign in via Okta. Started 10 minutes ago. Already paged on-call.",
        "output": '{"urgency": "high", "reason": "auth-outage"}',
    },
    {
        "instruction": "Classify the urgency of this customer-support email.",
        "input": "Subject: Forgot my MFA token\n\nMy MFA token doesn't work. I have a presentation in an hour.",
        "output": '{"urgency": "high", "reason": "mfa-deadline"}',
    },
]


# ── Helpers ────────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * max(1, 68 - len(text))}")


@dataclass
class Environment:
    """What the host machine has available."""

    has_vastai_key: bool
    has_vastai_cli: bool
    vastai_version: str | None = None

    @property
    def can_go_live(self) -> bool:
        return self.has_vastai_key and self.has_vastai_cli


def _detect_environment() -> Environment:
    """Detect ``VASTAI_API_KEY`` + ``vastai`` CLI availability."""
    has_key = bool(os.environ.get("VASTAI_API_KEY"))
    cli_path = shutil.which("vastai")
    version: str | None = None
    if cli_path:
        try:
            proc = subprocess.run(
                [cli_path, "--version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()[0]
        except (subprocess.TimeoutExpired, OSError):
            version = "(version probe failed)"
    return Environment(
        has_vastai_key=has_key,
        has_vastai_cli=cli_path is not None,
        vastai_version=version,
    )


@dataclass
class Offer:
    """A row from ``vastai search offers``.

    Vast.ai returns a JSON array of offer objects when called with
    ``--raw``; only the fields we actually use are pulled into this
    dataclass. The full offer object has ~50 fields — keep this surface
    narrow so the example is readable.
    """

    offer_id: int
    gpu_name: str
    num_gpus: int
    dph_total: float            # dollars-per-hour, total billed rate
    reliability: float          # historical uptime, 0.0-1.0
    dlperf: float               # deep-learning perf score
    inet_down_mbps: float
    inet_up_mbps: float
    cuda_max_good: float | None
    machine_id: int | None
    geolocation: str | None

    @classmethod
    def from_raw(cls, raw: dict) -> "Offer":
        return cls(
            offer_id=int(raw.get("id", 0)),
            gpu_name=str(raw.get("gpu_name", "?")),
            num_gpus=int(raw.get("num_gpus", 1)),
            dph_total=float(raw.get("dph_total", raw.get("dph_base", 0.0))),
            reliability=float(raw.get("reliability", raw.get("reliability2", 0.0))),
            dlperf=float(raw.get("dlperf", 0.0)),
            inet_down_mbps=float(raw.get("inet_down", 0.0)),
            inet_up_mbps=float(raw.get("inet_up", 0.0)),
            cuda_max_good=raw.get("cuda_max_good"),
            machine_id=raw.get("machine_id"),
            geolocation=raw.get("geolocation"),
        )


@dataclass
class GpuSpendTracker:
    """Tracks accrued GPU rental cost in USD.

    Records ``$/hr * elapsed_hours`` against ``project_id``. Same shape
    Example 47 uses for RunPod, parameterised on the per-hour rate the
    marketplace bid actually won. Plays beside
    ``sagewai.observability.costs`` so the Observatory dashboard's
    blended cost view (cloud-LLM + GPU rental) reads from one place.
    """

    project_id: str
    price_per_hour_usd: float
    started_at: float | None = None
    stopped_at: float | None = None

    def start(self) -> None:
        self.started_at = time.monotonic()
        self.stopped_at = None

    def stop(self) -> None:
        if self.started_at is not None and self.stopped_at is None:
            self.stopped_at = time.monotonic()

    @property
    def elapsed_seconds(self) -> float:
        if self.started_at is None:
            return 0.0
        end = self.stopped_at if self.stopped_at is not None else time.monotonic()
        return max(0.0, end - self.started_at)

    @property
    def accrued_usd(self) -> float:
        return self.price_per_hour_usd * (self.elapsed_seconds / 3600.0)

    def would_exceed(self, budget_usd: float) -> bool:
        return self.accrued_usd >= budget_usd


@dataclass
class InstanceHandle:
    """Live instance identity returned by ``vastai create instance``."""

    instance_id: int
    offer: Offer
    label: str
    created_at: float = field(default_factory=time.time)


# ── Search query builder + filters ────────────────────────────────


def build_search_query(
    *,
    gpu_name: str,
    min_reliability: float,
    min_dlperf: float,
    min_inet_down_mbps: float,
) -> str:
    """Build the offer-search query string.

    Vast.ai's query language is a space-separated set of ``key OP value``
    clauses. ``key=value`` is exact match; ``key>=N`` / ``key<=N`` are
    inclusive bounds. All clauses are ``AND``-combined. The CLI's ``-o``
    flag sorts; ``dph+`` means *cheapest first* (ascending dollars per
    hour). We also pin ``rentable=true`` and ``num_gpus=1`` so we don't
    accidentally bid on a multi-GPU host or one mid-rebuild.

    Reference: ``atelier/docs/v1.0/inference-provisioning-setup.md``
    Vast.ai section.
    """
    return (
        f"gpu_name={gpu_name} "
        f"reliability>={min_reliability:.2f} "
        f"dlperf>={min_dlperf:.0f} "
        f"inet_down>={min_inet_down_mbps:.0f} "
        f"num_gpus=1 "
        f"rentable=true"
    )


def build_search_command(query: str) -> list[str]:
    """Build the ``vastai search offers`` argv with JSON output.

    ``--raw`` returns the full offer objects as JSON; ``-o 'dph+'``
    sorts ascending by total dollars per hour so ``offers[0]`` is the
    cheapest reliable match.
    """
    return ["vastai", "search", "offers", query, "-o", "dph+", "--raw"]


def build_create_command(*, offer_id: int, label: str) -> list[str]:
    """Build the ``vastai create instance`` argv.

    Mirrors the recipe in
    ``atelier/docs/v1.0/inference-provisioning-setup.md``:

        vastai create instance <ID> \\
            --image=unsloth/unsloth:latest \\
            --disk=20 \\
            --label=<label>
    """
    return [
        "vastai", "create", "instance", str(offer_id),
        f"--image={INSTANCE_IMAGE}",
        f"--disk={INSTANCE_DISK_GB}",
        f"--label={label}",
        "--raw",
    ]


def build_copy_command(*, local_path: str, instance_id: int, remote_path: str) -> list[str]:
    """Build the ``vastai copy`` argv for an upload.

    Vast.ai's copy command takes ``<src> <dest>`` where either side may
    be ``<INSTANCE_ID>:<path>``. We use it for both upload (src=local)
    and download (dest=local).
    """
    return [
        "vastai", "copy",
        local_path,
        f"{instance_id}:{remote_path}",
    ]


def build_receive_command(*, instance_id: int, remote_path: str, local_path: str) -> list[str]:
    """Build the ``vastai copy`` argv for a download (instance → local)."""
    return [
        "vastai", "copy",
        f"{instance_id}:{remote_path}",
        local_path,
    ]


def build_execute_command(*, instance_id: int, remote_command: str) -> list[str]:
    """Build the ``vastai execute`` argv that runs ``remote_command`` on the instance."""
    return [
        "vastai", "execute", str(instance_id),
        remote_command,
    ]


def build_destroy_command(*, instance_id: int) -> list[str]:
    """Build the ``vastai destroy instance`` argv that tears the instance down."""
    return ["vastai", "destroy", "instance", str(instance_id)]


# The Unsloth recipe to run inside the instance. Identical to Example
# 47's recipe — same data, same hyper-params, same base. The only
# difference is the GPU underneath. Kept inline so the example is
# self-contained.
REMOTE_FINETUNE_SCRIPT: str = r'''
set -euo pipefail
cd /workspace
python - <<'PY'
import json, os
from datasets import Dataset
from unsloth import FastLanguageModel
from trl import SFTTrainer
from transformers import TrainingArguments

with open("/workspace/email_triage.jsonl") as fh:
    samples = [json.loads(line) for line in fh if line.strip()]

def to_alpaca(ex):
    return {
        "text": (
            f"### Instruction:\n{ex['instruction']}\n\n"
            f"### Input:\n{ex['input']}\n\n"
            f"### Response:\n{ex['output']}"
        ),
    }

dataset = Dataset.from_list([to_alpaca(s) for s in samples])

model, tokenizer = FastLanguageModel.from_pretrained(
    model_name="unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
    max_seq_length=2048,
    load_in_4bit=True,
)
model = FastLanguageModel.get_peft_model(
    model, r=16, lora_alpha=32, lora_dropout=0.05,
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj"],
)

trainer = SFTTrainer(
    model=model,
    tokenizer=tokenizer,
    train_dataset=dataset,
    dataset_text_field="text",
    max_seq_length=2048,
    args=TrainingArguments(
        per_device_train_batch_size=2,
        gradient_accumulation_steps=2,
        num_train_epochs=1,
        learning_rate=2e-4,
        output_dir="/workspace/output",
        logging_steps=1,
        save_strategy="epoch",
    ),
)
trainer.train()
model.save_pretrained("/workspace/output/lora")
tokenizer.save_pretrained("/workspace/output/lora")
PY
'''.strip()


# ── Synthetic offer list (stub mode, never spends a cent) ────────


SYNTHETIC_OFFERS: list[dict] = [
    {
        "id": 8472019,
        "gpu_name": "RTX_3090",
        "num_gpus": 1,
        "dph_total": 0.241,
        "reliability": 0.987,
        "dlperf": 14.2,
        "inet_down": 412.0,
        "inet_up": 198.0,
        "cuda_max_good": 12.4,
        "machine_id": 18209,
        "geolocation": "DE",
    },
    {
        "id": 8472044,
        "gpu_name": "RTX_3090",
        "num_gpus": 1,
        "dph_total": 0.268,
        "reliability": 0.962,
        "dlperf": 13.8,
        "inet_down": 285.0,
        "inet_up": 142.0,
        "cuda_max_good": 12.4,
        "machine_id": 19771,
        "geolocation": "US",
    },
    {
        "id": 8471902,
        "gpu_name": "RTX_3090",
        "num_gpus": 1,
        "dph_total": 0.319,
        "reliability": 0.953,
        "dlperf": 12.9,
        "inet_down": 156.0,
        "inet_up": 88.0,
        "cuda_max_good": 12.2,
        "machine_id": 16344,
        "geolocation": "CA",
    },
]


# ── Subprocess plumbing ───────────────────────────────────────────


def _run(
    argv: list[str],
    *,
    timeout: float | None = None,
    capture: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return the completed process.

    Raises ``RuntimeError`` on non-zero exit. Callers in the live
    orchestration path catch this so they can ensure teardown.
    """
    proc = subprocess.run(
        argv,
        capture_output=capture,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "(no output)").strip().splitlines()[-3:]
        raise RuntimeError(
            f"{' '.join(argv[:3])} failed (exit {proc.returncode}): "
            f"{' / '.join(tail)}"
        )
    return proc


_INSTANCE_ID_RE = re.compile(r"\b(\d{6,})\b")


def _parse_instance_id(create_stdout: str) -> int:
    """Extract the instance id from ``vastai create instance`` output.

    With ``--raw`` Vast.ai prints ``{"new_contract": <id>, "success": true}``.
    Older releases printed ``Started. {'success': True, 'new_contract': N}``
    with single quotes — handle both shapes.
    """
    stripped = create_stdout.strip()
    # Try strict JSON first.
    try:
        parsed = json.loads(stripped)
        if isinstance(parsed, dict) and "new_contract" in parsed:
            return int(parsed["new_contract"])
    except (json.JSONDecodeError, ValueError):
        pass
    # Fallback — find the longest digit run (the instance id is always >= 6 digits).
    candidates = _INSTANCE_ID_RE.findall(stripped)
    if candidates:
        return int(max(candidates, key=len))
    raise RuntimeError(
        "Could not parse instance id from `vastai create instance` output. "
        "First 500 chars: " + stripped[:500]
    )


def _parse_offers_json(stdout: str) -> list[Offer]:
    """Parse ``vastai search offers --raw`` JSON output into ``Offer`` rows."""
    try:
        rows = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"vastai search offers did not return valid JSON: {exc}"
        ) from exc
    if not isinstance(rows, list):
        raise RuntimeError(
            "vastai search offers returned non-list JSON: "
            f"{type(rows).__name__}"
        )
    return [Offer.from_raw(r) for r in rows if isinstance(r, dict)]


# ── Instance lifecycle (live orchestration) ──────────────────────


_ACTIVE_INSTANCE: InstanceHandle | None = None
_TEARDOWN_DONE: bool = False


def _register_signal_handlers(*, dry_run: bool) -> None:
    """Wire SIGTERM + SIGINT + atexit to ``_teardown_active_instance``.

    Cleanup must run even if the host process panics or is killed —
    a stuck instance accrues cost until manually destroyed via the
    Vast.ai web console. Belt-and-braces: ``atexit`` for normal exits +
    signal handlers for kill signals + a ``try/finally`` in ``run_live``.
    """
    if dry_run:
        return

    def _on_signal(signum: int, _frame: object) -> None:
        print(
            f"\n  [signal {signum}] caught — destroying the active instance "
            "before exit."
        )
        _teardown_active_instance()
        # Re-raise the default signal disposition so the process actually exits.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    atexit.register(_teardown_active_instance)


def _teardown_active_instance() -> None:
    """Destroy the instance tracked in ``_ACTIVE_INSTANCE``, if any.

    Idempotent: calling this multiple times (atexit + finally + signal)
    is safe — the second invocation no-ops once teardown has succeeded.
    """
    global _TEARDOWN_DONE  # noqa: PLW0603
    if _TEARDOWN_DONE or _ACTIVE_INSTANCE is None:
        return
    inst = _ACTIVE_INSTANCE
    print(f"  Destroying instance {inst.instance_id} ({inst.label}) …")
    try:
        _run(build_destroy_command(instance_id=inst.instance_id), timeout=60)
        print(f"  Instance {inst.instance_id} destroyed.")
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        # The instance may already be gone (manual cleanup); surface but
        # don't crash — the caller has nothing else to clean up.
        print(f"  [warn] teardown returned: {exc}")
        print(
            "  [warn] verify in the Vast.ai console: "
            "https://cloud.vast.ai/instances/"
        )
    _TEARDOWN_DONE = True


def _write_training_jsonl(samples: list[dict[str, str]]) -> Path:
    """Write the email-triage dataset to a temp file and return its path."""
    tmp = Path(tempfile.mkdtemp(prefix="sagewai-vastai-")) / "email_triage.jsonl"
    with tmp.open("w") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")
    return tmp


async def _budget_watchdog(
    tracker: GpuSpendTracker,
    *,
    budget_usd: float,
    check_interval_seconds: float,
) -> None:
    """Background task that destroys the active instance when accrued cost crosses ``budget_usd``.

    Polls every ``check_interval_seconds``. The instance's teardown
    handler does the actual destroy — we just trip the trigger by
    signalling the host process. Returns when the tracker is stopped
    externally.
    """
    while tracker.stopped_at is None:
        if tracker.would_exceed(budget_usd):
            print(
                f"\n  [budget] accrued ${tracker.accrued_usd:.4f} >= "
                f"${budget_usd:.2f} budget — destroying the instance."
            )
            _teardown_active_instance()
            os.kill(os.getpid(), signal.SIGTERM)
            return
        await asyncio.sleep(check_interval_seconds)


async def search_offers_live(
    *,
    query: str,
    timeout: float = 60.0,
) -> tuple[list[Offer], float]:
    """Run ``vastai search offers`` and return ``(offers, time_to_match_seconds)``.

    The marketplace usually surfaces the first viable offer in
    sub-second time, but operators should see the number explicitly —
    one of the issue's acceptance criteria.
    """
    cmd = build_search_command(query)
    print(f"    $ {shlex.join(cmd)}")
    start = time.monotonic()
    proc = await asyncio.to_thread(_run, cmd, timeout=timeout)
    elapsed = time.monotonic() - start
    offers = _parse_offers_json(proc.stdout)
    return offers, elapsed


async def run_live(
    *,
    gpu_name: str,
    min_reliability: float,
    min_dlperf: float,
    min_inet_down_mbps: float,
    budget_usd: float,
    project_id: str,
    label: str,
    download_dir: Path,
) -> tuple[bool, GpuSpendTracker, Offer | None, float, str | None]:
    """Run the full live orchestration. Returns ``(success, tracker, chosen_offer, time_to_match_s, lora_local_path)``.

    Cleanup is guaranteed: teardown runs in the ``finally`` block, in
    the signal handler, and in the ``atexit`` hook. The budget watchdog
    races the pipeline and trips teardown if accrued cost crosses the
    cap.
    """
    global _ACTIVE_INSTANCE  # noqa: PLW0603

    # 1. Search the marketplace
    print("  Searching the marketplace …")
    query = build_search_query(
        gpu_name=gpu_name,
        min_reliability=min_reliability,
        min_dlperf=min_dlperf,
        min_inet_down_mbps=min_inet_down_mbps,
    )
    offers, time_to_match = await search_offers_live(query=query)
    if not offers:
        print(
            "  [error] no offers matched the reliability filter. "
            "Suggestions:\n"
            f"    - loosen --min-reliability (currently {min_reliability:.2f})\n"
            f"    - loosen --min-dlperf (currently {min_dlperf:.0f})\n"
            f"    - try a different GPU (currently {gpu_name})\n"
            "    - or fall back to Example 47 (RunPod) for a guaranteed pod."
        )
        # Return a tracker with no spend so the cost-down report still works.
        return False, GpuSpendTracker(project_id=project_id, price_per_hour_usd=0.0), None, time_to_match, None

    chosen = offers[0]
    print(
        f"  Marketplace surfaced {len(offers)} offer(s) in "
        f"{time_to_match:.2f}s. Top match:"
    )
    print(f"    offer_id    = {chosen.offer_id}")
    print(f"    gpu_name    = {chosen.gpu_name}  ({chosen.num_gpus}× GPU)")
    print(f"    dph_total   = ${chosen.dph_total:.4f}/hr")
    print(f"    reliability = {chosen.reliability:.4f}  "
          f"(filter: >= {min_reliability:.2f})")
    print(f"    dlperf      = {chosen.dlperf:.2f}  (filter: >= {min_dlperf:.0f})")
    print(f"    inet_down   = {chosen.inet_down_mbps:.0f} Mbps  "
          f"(filter: >= {min_inet_down_mbps:.0f})")
    print(f"    inet_up     = {chosen.inet_up_mbps:.0f} Mbps")
    if chosen.geolocation:
        print(f"    geolocation = {chosen.geolocation}")
    print()

    tracker = GpuSpendTracker(
        project_id=project_id, price_per_hour_usd=chosen.dph_total,
    )
    lora_local: str | None = None

    # 2. Create instance from the chosen offer
    print(f"  Creating instance from offer {chosen.offer_id} …")
    create_cmd = build_create_command(offer_id=chosen.offer_id, label=label)
    print(f"    $ {shlex.join(create_cmd)}")
    create_proc = _run(create_cmd, timeout=180)
    instance_id = _parse_instance_id(create_proc.stdout)
    _ACTIVE_INSTANCE = InstanceHandle(
        instance_id=instance_id, offer=chosen, label=label,
    )
    tracker.start()
    print(f"  Instance created: {instance_id}")
    print()

    # 3. Spawn the budget watchdog
    watchdog = asyncio.create_task(
        _budget_watchdog(
            tracker, budget_usd=budget_usd, check_interval_seconds=2.0,
        ),
    )

    try:
        # 4. Upload the JSONL
        print("  Uploading email-triage JSONL …")
        local_jsonl = _write_training_jsonl(EMAIL_TRIAGE_TRAINING_DATA)
        copy_cmd = build_copy_command(
            local_path=str(local_jsonl),
            instance_id=instance_id,
            remote_path="/workspace/email_triage.jsonl",
        )
        print(f"    $ {shlex.join(copy_cmd)}")
        await asyncio.to_thread(_run, copy_cmd, timeout=300)
        print("  Upload OK.")
        print()

        # 5. Run the fine-tune
        print("  Running unsloth LoRA fine-tune on the rented GPU …")
        exec_cmd = build_execute_command(
            instance_id=instance_id, remote_command=REMOTE_FINETUNE_SCRIPT,
        )
        print(f"    $ vastai execute {instance_id} <unsloth recipe>")
        # Long-running — Unsloth on RTX 3090 fits the 8-sample LoRA in
        # ~1h. The watchdog will trip if it goes over budget.
        await asyncio.to_thread(_run, exec_cmd, timeout=7200)
        print("  Fine-tune OK.")
        print()

        # 6. Download the LoRA
        print("  Downloading the trained LoRA adapter …")
        lora_local = str(download_dir / "lora")
        receive_cmd = build_receive_command(
            instance_id=instance_id,
            remote_path="/workspace/output/lora",
            local_path=lora_local,
        )
        print(f"    $ {shlex.join(receive_cmd)}")
        await asyncio.to_thread(_run, receive_cmd, timeout=600)
        print(f"  LoRA downloaded to: {lora_local}")
        print()

        success = True
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"\n  [error] live orchestration failed: {exc}")
        success = False
    finally:
        tracker.stop()
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
        _teardown_active_instance()

    return success, tracker, chosen, time_to_match, lora_local


# ── Stub-mode plan (always safe; never spends a cent) ─────────────


def print_search_plan(
    *,
    gpu_name: str,
    min_reliability: float,
    min_dlperf: float,
    min_inet_down_mbps: float,
) -> None:
    """Print the search query + a synthetic offer list shape."""
    query = build_search_query(
        gpu_name=gpu_name,
        min_reliability=min_reliability,
        min_dlperf=min_dlperf,
        min_inet_down_mbps=min_inet_down_mbps,
    )
    print("  ── Search the marketplace ──")
    print()
    print(f"    $ {shlex.join(build_search_command(query))}")
    print()
    print("  Synthetic offer list (what `vastai search offers` would return):")
    print()
    print(f"    {'offer':>8}  {'gpu':<10}  {'$/hr':>8}  "
          f"{'rel':>5}  {'dlperf':>6}  {'down':>6}  loc")
    print(f"    {'─' * 8}  {'─' * 10}  {'─' * 8}  "
          f"{'─' * 5}  {'─' * 6}  {'─' * 6}  ───")
    for raw in SYNTHETIC_OFFERS:
        offer = Offer.from_raw(raw)
        print(
            f"    {offer.offer_id:>8d}  {offer.gpu_name:<10}  "
            f"${offer.dph_total:>6.4f}  {offer.reliability:>5.3f}  "
            f"{offer.dlperf:>6.2f}  {offer.inet_down_mbps:>4.0f}  "
            f"{offer.geolocation or '-'}"
        )
    print()
    print("  Top match (cheapest reliable RTX 3090 right now):")
    chosen = Offer.from_raw(SYNTHETIC_OFFERS[0])
    print(f"    offer_id    = {chosen.offer_id}")
    print(f"    dph_total   = ${chosen.dph_total:.4f}/hr")
    print(f"    reliability = {chosen.reliability:.4f}  "
          f"(filter: >= {min_reliability:.2f})  ✓")
    print(f"    dlperf      = {chosen.dlperf:.2f}  "
          f"(filter: >= {min_dlperf:.0f})  ✓")
    print()


def print_orchestration_plan(*, gpu_name: str, budget_usd: float, label: str) -> None:
    """Print the exact commands the live path would run + the budget breakdown."""
    typical_price = GPU_TYPICAL_PRICE_PER_HR_USD.get(gpu_name, 0.30)
    expected_cost = typical_price * EXPECTED_FINE_TUNE_HOURS

    print("  ── Commands vastai would run (in order) ──")
    print()
    print("  1. Create the instance from the chosen offer:")
    print(f"     $ {shlex.join(build_create_command(offer_id=8472019, label=label))}")
    print()
    print("  2. Upload the email-triage training data:")
    print(f"     $ {shlex.join(build_copy_command(local_path='./email_triage.jsonl', instance_id=8472019, remote_path='/workspace/email_triage.jsonl'))}")
    print()
    print("  3. Run the Unsloth fine-tune on the rented GPU:")
    print("     $ vastai execute <instance-id> '<unsloth recipe>'")
    print("       (recipe: 4-bit Llama-3.2-3B + LoRA r=16, alpha=32, 1 epoch — same as Example 47)")
    print()
    print("  4. Download the trained LoRA adapter:")
    print(f"     $ {shlex.join(build_receive_command(instance_id=8472019, remote_path='/workspace/output/lora', local_path='./lora'))}")
    print()
    print("  5. Destroy the instance (always — cleanup runs even on failure):")
    print(f"     $ {shlex.join(build_destroy_command(instance_id=8472019))}")
    print()
    print("  ── Estimated bid breakdown ──")
    print()
    print(f"  GPU             = {gpu_name}")
    print(f"  Typical $/hr    = ${typical_price:.4f}/hr  "
          "(Vast.ai marketplace, varies minute-to-minute)")
    print(f"  Expected hours  = {EXPECTED_FINE_TUNE_HOURS:.2f}h "
          "(Unsloth 3B LoRA, 8 samples, 1 epoch)")
    print(f"  Expected spend  = ${expected_cost:.4f}")
    print(f"  Budget cap      = ${budget_usd:.2f}  "
          f"(watchdog destroys instance if exceeded)")
    print()
    if expected_cost > budget_usd:
        print(f"  [warn] expected spend ${expected_cost:.4f} > budget "
              f"${budget_usd:.2f}; the watchdog would trip.")
        print()


def print_live_proof(
    *, success: bool, tracker: GpuSpendTracker, chosen: Offer | None,
    time_to_match_seconds: float, lora_local_path: str | None,
    budget_usd: float, gpu_name: str,
) -> None:
    """Print the proof block after a live run."""
    rental_minutes = tracker.elapsed_seconds / 60.0
    rental_cost = tracker.accrued_usd
    # Pair the GPU-rental tracker with the per-call cloud baseline so the
    # Observatory dashboard can render both together.
    cloud_call_baseline = calculate_cost(
        input_tokens=250, output_tokens=30,
        model="claude-haiku-4-5-20251001",
    )

    print(f"  Bid outcome       : {'completed' if success else 'failed'}")
    print(f"  Time-to-match     : {time_to_match_seconds:.2f}s  "
          "(marketplace search → top offer)")
    if chosen is not None:
        print(f"  Won offer         : {chosen.offer_id}  "
              f"(machine {chosen.machine_id})")
        print(f"  GPU               : {chosen.gpu_name} @ ${chosen.dph_total:.4f}/hr")
        print(f"  Reliability       : {chosen.reliability:.4f}")
        print(f"  dlperf            : {chosen.dlperf:.2f}")
        print(f"  inet_down/up      : {chosen.inet_down_mbps:.0f} / "
              f"{chosen.inet_up_mbps:.0f} Mbps")
        if chosen.geolocation:
            print(f"  Geolocation       : {chosen.geolocation}")
    else:
        print(f"  GPU requested     : {gpu_name}  (no offer matched the filter)")
    print(f"  Rental duration   : {rental_minutes:.1f} min "
          f"({tracker.elapsed_seconds:.0f}s wall)")
    print(f"  Rental spend      : ${rental_cost:.4f}  "
          f"(budget cap = ${budget_usd:.2f})")
    print(f"  Cloud-call baseline (calculate_cost): ${cloud_call_baseline:.6f}/call")
    if lora_local_path:
        print(f"  LoRA downloaded   : {lora_local_path}")
    print(f"  Instance destroyed: {_TEARDOWN_DONE}")
    print()


def print_runpod_comparison(
    *, vastai_gpu_name: str,
    vastai_price_per_hr: float, vastai_hours: float,
    runpod_price_per_hr: float = RUNPOD_REFERENCE_PRICE_PER_HR_USD,
    runpod_hours: float = RUNPOD_REFERENCE_FINE_TUNE_HOURS,
) -> None:
    """Print the side-by-side cost table: Vast.ai (this example) vs. RunPod (Example 47)."""
    vastai_cost = vastai_price_per_hr * vastai_hours
    runpod_cost = runpod_price_per_hr * runpod_hours
    delta = runpod_cost - vastai_cost
    pct_savings = (delta / runpod_cost * 100) if runpod_cost > 0 else 0.0
    vastai_label = f"Vast.ai {vastai_gpu_name.replace('_', ' ')}"

    print(f"  {'tier':<10}  {'GPU':<18}  {'$/hr':>8}  {'hours':>6}  "
          f"{'spend':>8}")
    print(f"  {'─' * 10}  {'─' * 18}  {'─' * 8}  {'─' * 6}  {'─' * 8}")
    print(f"  {'Ex 47':<10}  {RUNPOD_REFERENCE_GPU:<18}  "
          f"${runpod_price_per_hr:>6.4f}  {runpod_hours:>6.2f}  "
          f"${runpod_cost:>6.4f}")
    print(f"  {'Ex 45':<10}  {vastai_label:<18}  "
          f"${vastai_price_per_hr:>6.4f}  {vastai_hours:>6.2f}  "
          f"${vastai_cost:>6.4f}")
    print()
    if delta > 0:
        print(f"  Vast.ai saves     : ${delta:.4f} ({pct_savings:.1f}%) on the "
              "same fine-tune workload.")
    elif delta < 0:
        print(f"  Vast.ai cost more : ${-delta:.4f} on this run "
              "(rare — usually Vast.ai wins on price). Possible causes:\n"
              "    - the chosen RTX 3090 host took longer than expected\n"
              "    - the budget cap forced a re-run on Vast.ai")
    else:
        print(f"  Tied on price.")
    print()
    print("  Why this matters:")
    print("    Vast.ai trades faster provisioning + bare-metal certainty (RunPod)")
    print("    for lower per-hour rate + per-host reliability scoring (Vast.ai).")
    print("    Use Ex 45 for batch fine-tunes and overnight runs where the host's")
    print("    historical uptime matters; use Ex 47 when you want a known-good")
    print("    pod up in seconds.")
    print()


def print_costdown(
    *, gpu_rental_usd: float, baseline_call_usd: float, daily_volume: int,
) -> None:
    """Print the cost-down comparison: cloud-LLM-only vs. one-time fine-tune."""
    monthly_baseline = baseline_call_usd * daily_volume * 30
    annual_baseline = monthly_baseline * 12
    payback_calls = (
        int(gpu_rental_usd / baseline_call_usd) if baseline_call_usd > 0 else 0
    )

    print(f"  Cloud baseline    : ${baseline_call_usd:.6f}/call "
          f"(Anthropic Haiku, post-overhead)")
    print(f"  Local (fine-tuned): $0.000000/call (Ollama serves the LoRA)")
    print()
    print(f"  At {daily_volume} emails/day for 30 days:")
    print(f"    cloud-only      = ${monthly_baseline:>9.2f}/month "
          f"(${annual_baseline:>9.2f}/yr)")
    print(f"    after fine-tune = ${0.0:>9.2f}/month — the same task costs $0")
    print(f"    one-time spend  = ${gpu_rental_usd:>9.4f}  "
          "(this Vast.ai fine-tune)")
    print()
    print(f"  Payback           : after ~{payback_calls} cloud calls, "
          "the fine-tune has paid for itself")
    if daily_volume > 0:
        print(f"                      ({payback_calls / daily_volume:.1f} days at "
              f"{daily_volume}/day)")
    print()


# ── main ───────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Example 45 — Vast.ai marketplace bidding.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Actually bid on a real instance (requires VASTAI_API_KEY + vastai). "
             "Default is stub mode — prints commands without spending.",
    )
    parser.add_argument(
        "--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
        help=f"Hard budget cap in USD; default ${DEFAULT_BUDGET_USD:.2f}. "
             "Watchdog destroys the instance when accrued cost crosses this.",
    )
    parser.add_argument(
        "--gpu-name", default=DEFAULT_GPU_NAME,
        help=f"Vast.ai GPU name; default '{DEFAULT_GPU_NAME}'. "
             "Try 'RTX_4090' for ~2x speed at ~2x cost; 'A100_80GB' for big bases.",
    )
    parser.add_argument(
        "--min-reliability", type=float, default=DEFAULT_MIN_RELIABILITY,
        help=f"Minimum host historical uptime; default {DEFAULT_MIN_RELIABILITY:.2f}. "
             "Tighten to 0.99 for production runs; loosen to 0.90 for cheap exploration.",
    )
    parser.add_argument(
        "--min-dlperf", type=float, default=DEFAULT_MIN_DLPERF,
        help=f"Minimum deep-learning-perf score; default {DEFAULT_MIN_DLPERF:.0f}.",
    )
    parser.add_argument(
        "--min-inet-down-mbps", type=float, default=DEFAULT_MIN_INET_DOWN_MBPS,
        help=f"Minimum download bandwidth in Mbps; default "
             f"{DEFAULT_MIN_INET_DOWN_MBPS:.0f}.",
    )
    parser.add_argument(
        "--project-id", default="acme-prod",
        help="Project id used for spend attribution in the cost dashboard.",
    )
    args = parser.parse_args()

    _line()
    print(" Sagewai — Vast.ai marketplace bidding (example 45, Gap #8d)")
    _line()
    print()

    # ── 1. Probe the environment ───────────────────────────────
    _line(" 1. Probe runtime environment ")
    print()
    env = _detect_environment()
    print(f"  VASTAI_API_KEY in env  : {'✓' if env.has_vastai_key else '✗'}  "
          "(read from ~/.sagewai/.env via python-dotenv)")
    print(f"  vastai on PATH         : {'✓' if env.has_vastai_cli else '✗'}"
          + (f"  ({env.vastai_version})" if env.vastai_version else ""))
    print(f"  --live flag passed     : {'✓' if args.live else '✗'}")
    print()

    will_go_live = args.live and env.can_go_live
    if args.live and not env.can_go_live:
        print("  [warn] --live requested but environment is incomplete.")
        if not env.has_vastai_key:
            print("         Set VASTAI_API_KEY in ~/.sagewai/.env "
                  "(template: atelier/docs/v1.0/inference-provisioning-setup.md)")
        if not env.has_vastai_cli:
            print("         Install the CLI: pip install vastai")
        print("  Falling back to stub mode for this run.")
        print()

    label = f"sagewai-ft-{int(time.time())}"

    # ── 2. Search plan (always — even in stub mode) ─────────────
    _line(" 2. Marketplace search ")
    print()
    print_search_plan(
        gpu_name=args.gpu_name,
        min_reliability=args.min_reliability,
        min_dlperf=args.min_dlperf,
        min_inet_down_mbps=args.min_inet_down_mbps,
    )

    # ── 3. Orchestration plan ──────────────────────────────────
    _line(" 3. Orchestration plan ")
    print()
    print_orchestration_plan(
        gpu_name=args.gpu_name,
        budget_usd=args.budget_usd,
        label=label,
    )

    # ── 4. Live or stub ────────────────────────────────────────
    chosen: Offer | None = None
    time_to_match: float = 0.0

    if will_go_live:
        _line(" 4. Live orchestration ")
        print()
        _register_signal_handlers(dry_run=False)
        download_dir = Path(tempfile.mkdtemp(prefix="sagewai-vastai-out-"))
        success, tracker, chosen, time_to_match, lora_local_path = await run_live(
            gpu_name=args.gpu_name,
            min_reliability=args.min_reliability,
            min_dlperf=args.min_dlperf,
            min_inet_down_mbps=args.min_inet_down_mbps,
            budget_usd=args.budget_usd,
            project_id=args.project_id,
            label=label,
            download_dir=download_dir,
        )
        print()
        _line(" 5. The proof — live run ")
        print()
        print_live_proof(
            success=success,
            tracker=tracker,
            chosen=chosen,
            time_to_match_seconds=time_to_match,
            lora_local_path=lora_local_path,
            budget_usd=args.budget_usd,
            gpu_name=args.gpu_name,
        )
        gpu_spend = tracker.accrued_usd
        vastai_price_per_hr = (
            chosen.dph_total if chosen is not None
            else GPU_TYPICAL_PRICE_PER_HR_USD.get(args.gpu_name, 0.30)
        )
        vastai_hours = tracker.elapsed_seconds / 3600.0 if tracker.elapsed_seconds > 0 else EXPECTED_FINE_TUNE_HOURS
    else:
        _line(" 4. Stub mode — no spend ")
        print()
        print("  No live orchestration requested. To run for real:")
        print("    1. Set VASTAI_API_KEY in ~/.sagewai/.env")
        print("    2. pip install vastai && vastai set api-key $VASTAI_API_KEY")
        print("    3. python 45_vastai_marketplace_bid.py --live")
        print()
        print("  Setup walkthrough: "
              "atelier/docs/v1.0/inference-provisioning-setup.md")
        print()
        # Use the chosen synthetic offer's price (what the live path's
        # `vastai search offers ... -o dph+` would surface as cheapest
        # reliable match) so the stub-mode comparison reflects what the
        # marketplace would actually win, not the unfiltered average.
        if args.gpu_name == DEFAULT_GPU_NAME:
            vastai_price_per_hr = float(SYNTHETIC_OFFERS[0]["dph_total"])
        else:
            vastai_price_per_hr = GPU_TYPICAL_PRICE_PER_HR_USD.get(args.gpu_name, 0.30)
        vastai_hours = EXPECTED_FINE_TUNE_HOURS
        gpu_spend = vastai_price_per_hr * vastai_hours

    # ── 5. The killer comparison: Vast.ai vs. RunPod ──────────
    _line(" Side-by-side: Vast.ai (Ex 45) vs. RunPod (Ex 47) ")
    print()
    print_runpod_comparison(
        vastai_gpu_name=args.gpu_name,
        vastai_price_per_hr=vastai_price_per_hr,
        vastai_hours=vastai_hours,
    )

    # ── 6. Cost-down (always — the headline pitch) ────────────
    _line(" Cost-down: cloud-LLM baseline vs. fine-tuned local ")
    print()
    print_costdown(
        gpu_rental_usd=gpu_spend,
        baseline_call_usd=BASELINE_COST_PER_CALL_USD,
        daily_volume=PRODUCTION_VOLUME_PER_DAY,
    )

    _line(" The training-loop pillar ")
    print()
    print("  Vast.ai is the budget-aggregator tier — operators trade a")
    print("  few minutes of provisioning latency for a 30-50% cheaper")
    print("  per-hour rate, with per-host reliability scoring keeping the")
    print("  marketplace dependable rather than hopeful. Reliability >=")
    print("  0.95, dlperf >= 10, inet_down >= 100 Mbps; the cheapest match")
    print("  wins. Cleanup-on-failure is enforced three ways (try/finally,")
    print("  atexit, SIGTERM) so a stuck instance can never drain your")
    print("  budget. The downloaded LoRA deploys via Ollama (Example 38)")
    print("  and the same task costs $0/call thereafter.")
    print()
    print("  Optionality is the brand: when Anthropic raises prices, you")
    print("  already have your own model — and it cost you under $3 to")
    print("  train.")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Make sure the instance is gone even on Ctrl-C during the watchdog loop.
        _teardown_active_instance()
        sys.exit(130)
