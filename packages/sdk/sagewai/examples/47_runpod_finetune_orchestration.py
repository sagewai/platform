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
"""Example 47 — RunPod fine-tune orchestration: rent, train, tear down.

Closes Gap #8a of the inference spectrum. The audience-pin person —
a senior engineer at a 50-500 person SaaS, told to "add AI to the
product this quarter" — has a corporate card and no time to learn
AWS GPU instance types. This example is the **default working tier**:
``runpodctl`` rents an RTX 5090 in seconds, runs Example 38's Unsloth
fine-tune on it, downloads the LoRA back to the developer's machine,
and tears the pod down — under a budget cap, with cleanup-on-failure
guaranteed even if the host process panics.

Pipeline::

    create pod  →  upload JSONL  →  run unsloth  →  download LoRA  →  remove pod
                                                          │
                                                          └── budget cap
                                                              kills the pod
                                                              if accrued
                                                              cost > --budget-usd

The same orchestration shape works for any fine-tune workload — the
example uses the email-triage dataset Example 36 builds because it's
the same data Example 38 trains on locally; running this on RunPod
gets you the cloud-GPU-trained version of the same LoRA.

What's exercised:

- ``runpodctl create pod`` invocation with the exact flags from
  ``atelier/docs/v1.0/inference-provisioning-landscape.md``
- ``runpodctl send`` / ``runpodctl receive`` for the JSONL upload +
  LoRA download
- Cleanup-on-failure via ``try/finally`` + ``atexit`` + ``SIGTERM``
  handler so a stuck pod never drains the budget
- Budget cap polling: GPU spend tracked via :class:`GpuSpendTracker`
  alongside ``sagewai.observability.costs``; the pod is killed before
  accrued cost crosses ``--budget-usd``
- Blended cost report: cloud-LLM baseline (Anthropic Haiku) vs.
  "after this fine-tune deploys, the same task costs $0" — the
  cost-down number you pitch to your CFO

The example **always** runs end-to-end. With ``RUNPOD_API_KEY`` set in
``~/.sagewai/.env`` *and* ``runpodctl`` on ``PATH``, it orchestrates
a real pod. Without either, it prints the exact commands it would
run, the cost breakdown, and a pointer to the setup doc — the
audience-pin person sees what the integration looks like before
they spend a cent.

Requirements::

    pip install sagewai           # python-dotenv ships in the SDK tree
    # Optional (for the live path):
    #   - RUNPOD_API_KEY in ~/.sagewai/.env
    #   - runpodctl on PATH (brew install runpod/runpodctl/runpodctl)

Usage::

    # Default: stub mode (no spend), prints the orchestration plan
    python 47_runpod_finetune_orchestration.py

    # Live: rent an RTX 5090, run the fine-tune, tear down the pod
    python 47_runpod_finetune_orchestration.py --live

    # Tighter budget (will kill the pod sooner if needed)
    python 47_runpod_finetune_orchestration.py --live --budget-usd 1.00

    # Cheaper GPU (RTX 4090 ~$0.34/hr instead of 5090 ~$0.69/hr)
    python 47_runpod_finetune_orchestration.py --live --gpu-type "NVIDIA RTX 4090"
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

# Load Sagewai credentials early so RUNPOD_API_KEY is visible below.
# Silently no-ops if ~/.sagewai/.env doesn't exist (clean-machine path).
load_dotenv(Path.home() / ".sagewai" / ".env")

from sagewai.observability.costs import calculate_cost  # noqa: E402

# ── Pod knobs (mirror atelier/docs/v1.0/inference-provisioning-landscape.md) ──

POD_IMAGE: str = "unsloth/unsloth:latest"
POD_GPU_TYPE_DEFAULT: str = "NVIDIA RTX 5090"
POD_GPU_COUNT: int = 1
POD_CONTAINER_DISK_GB: int = 20

# RunPod Community Cloud list pricing (2026-05). RTX 5090 is the default
# this example targets; RTX 4090 is the budget alternative. Keep these in
# sync with atelier/docs/v1.0/inference-provisioning-landscape.md so the
# pitch matches the docs.
GPU_PRICE_PER_HR_USD: dict[str, float] = {
    "NVIDIA RTX 5090": 0.69,
    "NVIDIA RTX 4090": 0.34,
    "NVIDIA A10G": 0.50,
    "NVIDIA A100 80GB": 1.60,
}

# How long an Unsloth LoRA fine-tune of a 3B base on the 8-sample email
# triage dataset takes on an RTX 5090. Empirical from Example 38's recipe
# scaled to the cloud GPU; we pin the number so the stub-mode budget
# breakdown is honest. Re-measure if you change the dataset shape.
EXPECTED_FINE_TUNE_HOURS: float = 0.50

# Default headline budget for the demo. Locked at $2 to match the issue's
# "Total demo budget under $2 in real spend" acceptance criterion.
DEFAULT_BUDGET_USD: float = 2.00

# Cloud-LLM baseline. Same number Example 38 pitches: an audience-pin
# person's typical small-model triage cost on Anthropic Haiku, after
# system-prompt + retry overhead. This is the figure the CFO will use.
BASELINE_COST_PER_CALL_USD: float = 0.005

# Production volume the audience-pin person quotes — 200 emails/day.
PRODUCTION_VOLUME_PER_DAY: int = 200


# ── Email-triage training data (mirrors Example 38, kept self-contained) ──

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

    has_runpod_key: bool
    has_runpodctl: bool
    runpodctl_version: str | None = None

    @property
    def can_go_live(self) -> bool:
        return self.has_runpod_key and self.has_runpodctl


def _detect_environment() -> Environment:
    """Detect ``RUNPOD_API_KEY`` + ``runpodctl`` availability."""
    has_key = bool(os.environ.get("RUNPOD_API_KEY"))
    runpodctl_path = shutil.which("runpodctl")
    version: str | None = None
    if runpodctl_path:
        try:
            proc = subprocess.run(
                [runpodctl_path, "version"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            version = (proc.stdout or proc.stderr).strip().splitlines()[0]
        except (subprocess.TimeoutExpired, OSError):
            version = "(version probe failed)"
    return Environment(
        has_runpod_key=has_key,
        has_runpodctl=runpodctl_path is not None,
        runpodctl_version=version,
    )


@dataclass
class GpuSpendTracker:
    """Tracks accrued GPU rental cost in USD.

    Records ``$/hr * elapsed_hours`` against ``project_id``. The same
    pattern Example 34 uses for LLM-call cost, with the rental price as
    the tracked unit instead of token count. Plays beside
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
class PodHandle:
    """Live pod identity returned by ``runpodctl create pod``."""

    pod_id: str
    name: str
    gpu_type: str
    created_at: float = field(default_factory=time.time)


# ── Command builders (always safe to call — no side effects) ─────


def build_create_pod_command(
    *, gpu_type: str, name: str,
) -> list[str]:
    """Build the ``runpodctl create pod`` argv.

    Mirrors the bash snippet in
    ``atelier/docs/v1.0/inference-provisioning-landscape.md``:

        runpodctl create pod \\
            --imageName unsloth/unsloth:latest \\
            --gpuType "NVIDIA RTX 5090" \\
            --gpuCount 1 \\
            --containerDiskInGb 20 \\
            --name <name>

    The flags use the camelCase form modern ``runpodctl`` (>= 1.14)
    accepts. Older versions used hyphenated forms (``--gpu-type``);
    if you're pinned to an older release, swap accordingly.
    """
    return [
        "runpodctl", "create", "pod",
        "--imageName", POD_IMAGE,
        "--gpuType", gpu_type,
        "--gpuCount", str(POD_GPU_COUNT),
        "--containerDiskInGb", str(POD_CONTAINER_DISK_GB),
        "--name", name,
    ]


def build_send_command(*, local_path: str, pod_id: str, remote_path: str) -> list[str]:
    """Build the ``runpodctl send`` argv for an upload."""
    return [
        "runpodctl", "send",
        "--pod", pod_id,
        local_path,
        remote_path,
    ]


def build_receive_command(*, pod_id: str, remote_path: str, local_path: str) -> list[str]:
    """Build the ``runpodctl receive`` argv for a download."""
    return [
        "runpodctl", "receive",
        "--pod", pod_id,
        remote_path,
        local_path,
    ]


def build_exec_command(*, pod_id: str, remote_command: str) -> list[str]:
    """Build the ``runpodctl exec`` argv that runs ``remote_command`` in the pod."""
    return [
        "runpodctl", "exec",
        "--pod", pod_id,
        "--", "bash", "-lc", remote_command,
    ]


def build_remove_command(*, pod_id: str) -> list[str]:
    """Build the ``runpodctl remove pod`` argv that tears the pod down."""
    return ["runpodctl", "remove", "pod", pod_id]


# The Unsloth recipe to run inside the pod. Mirrors Example 38's CUDA
# path but bound to the JSONL we uploaded. Kept inline so the example
# is self-contained — no second file the reader has to chase.
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


_POD_ID_RE = re.compile(r"\b([a-z0-9]{14,})\b")


def _parse_pod_id(create_stdout: str) -> str:
    """Extract the pod id from ``runpodctl create pod`` output.

    runpodctl prints lines like ``pod "abcd1234567890" created`` — the id
    is the longest alphanumeric token on those lines. We tolerate either
    quoted or unquoted shapes since the CLI's output format has shifted
    across releases.
    """
    for line in create_stdout.splitlines():
        if "pod" not in line.lower():
            continue
        candidates = _POD_ID_RE.findall(line)
        if candidates:
            return max(candidates, key=len)
    # Fallback: any long token in the entire output.
    candidates = _POD_ID_RE.findall(create_stdout)
    if candidates:
        return max(candidates, key=len)
    raise RuntimeError(
        "Could not parse pod id from `runpodctl create pod` output. "
        "First 500 chars: " + create_stdout[:500]
    )


# ── Pod lifecycle (live orchestration) ────────────────────────────


_ACTIVE_POD: PodHandle | None = None
_TEARDOWN_DONE: bool = False


def _register_signal_handlers(*, dry_run: bool) -> None:
    """Wire SIGTERM + SIGINT + atexit to ``_teardown_active_pod``.

    Cleanup must run even if the host process panics or is killed —
    a stuck pod accrues cost until manually destroyed via the RunPod
    web console. Belt-and-braces: ``atexit`` for normal exits + signal
    handlers for kill signals + a ``try/finally`` in ``run_live``.
    """
    if dry_run:
        return

    def _on_signal(signum: int, _frame: object) -> None:
        print(
            f"\n  [signal {signum}] caught — tearing down the active pod "
            "before exit."
        )
        _teardown_active_pod()
        # Re-raise the default signal disposition so the process actually exits.
        signal.signal(signum, signal.SIG_DFL)
        os.kill(os.getpid(), signum)

    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)
    atexit.register(_teardown_active_pod)


def _teardown_active_pod() -> None:
    """Remove the pod tracked in ``_ACTIVE_POD``, if any.

    Idempotent: calling this multiple times (atexit + finally + signal)
    is safe — the second invocation no-ops once teardown has succeeded.
    """
    global _TEARDOWN_DONE  # noqa: PLW0603
    if _TEARDOWN_DONE or _ACTIVE_POD is None:
        return
    pod = _ACTIVE_POD
    print(f"  Tearing down pod {pod.pod_id} ({pod.name}) …")
    try:
        _run(build_remove_command(pod_id=pod.pod_id), timeout=60)
        print(f"  Pod {pod.pod_id} removed.")
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        # The pod may already be gone (manual cleanup); surface but don't
        # crash — the caller has nothing else to clean up.
        print(f"  [warn] teardown returned: {exc}")
        print(
            "  [warn] verify in the RunPod console: "
            "https://www.console.runpod.io/pods"
        )
    _TEARDOWN_DONE = True


def _write_training_jsonl(samples: list[dict[str, str]]) -> Path:
    """Write the email-triage dataset to a temp file and return its path."""
    tmp = Path(tempfile.mkdtemp(prefix="sagewai-runpod-")) / "email_triage.jsonl"
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
    """Background task that kills the active pod when accrued cost crosses ``budget_usd``.

    Polls every ``check_interval_seconds``. The pod's teardown handler
    handles the actual removal — we just trip the trigger by signalling
    the host process. Returns when the tracker is stopped externally.
    """
    while tracker.stopped_at is None:
        if tracker.would_exceed(budget_usd):
            print(
                f"\n  [budget] accrued ${tracker.accrued_usd:.4f} >= "
                f"${budget_usd:.2f} budget — killing the pod."
            )
            _teardown_active_pod()
            os.kill(os.getpid(), signal.SIGTERM)
            return
        await asyncio.sleep(check_interval_seconds)


async def run_live(
    *,
    gpu_type: str,
    budget_usd: float,
    project_id: str,
    pod_name: str,
    download_dir: Path,
) -> tuple[bool, GpuSpendTracker, str | None]:
    """Run the full live orchestration. Returns ``(success, tracker, lora_local_path)``.

    Cleanup is guaranteed: teardown runs in the ``finally`` block, in
    the signal handler, and in the ``atexit`` hook. The budget
    watchdog races the pipeline and trips teardown if accrued cost
    crosses the cap.
    """
    global _ACTIVE_POD  # noqa: PLW0603
    price_per_hour = GPU_PRICE_PER_HR_USD.get(gpu_type, 0.69)
    tracker = GpuSpendTracker(
        project_id=project_id, price_per_hour_usd=price_per_hour,
    )
    lora_local: str | None = None

    # 1. Create pod
    print(f"  Creating pod (image={POD_IMAGE}, gpu={gpu_type}) …")
    create_cmd = build_create_pod_command(gpu_type=gpu_type, name=pod_name)
    print(f"    $ {shlex.join(create_cmd)}")
    create_proc = _run(create_cmd, timeout=180)
    pod_id = _parse_pod_id(create_proc.stdout)
    _ACTIVE_POD = PodHandle(pod_id=pod_id, name=pod_name, gpu_type=gpu_type)
    tracker.start()
    print(f"  Pod created: {pod_id}")
    print()

    # 2. Spawn the budget watchdog
    watchdog = asyncio.create_task(
        _budget_watchdog(
            tracker, budget_usd=budget_usd, check_interval_seconds=2.0,
        ),
    )

    try:
        # 3. Upload the JSONL
        print("  Uploading email-triage JSONL …")
        local_jsonl = _write_training_jsonl(EMAIL_TRIAGE_TRAINING_DATA)
        send_cmd = build_send_command(
            local_path=str(local_jsonl),
            pod_id=pod_id,
            remote_path="/workspace/email_triage.jsonl",
        )
        print(f"    $ {shlex.join(send_cmd)}")
        await asyncio.to_thread(_run, send_cmd, timeout=300)
        print("  Upload OK.")
        print()

        # 4. Run the fine-tune
        print("  Running unsloth LoRA fine-tune on the rented GPU …")
        exec_cmd = build_exec_command(
            pod_id=pod_id, remote_command=REMOTE_FINETUNE_SCRIPT,
        )
        print(f"    $ runpodctl exec --pod {pod_id} -- bash -lc <unsloth recipe>")
        # Long-running — Unsloth on RTX 5090 fits the 8-sample LoRA in
        # under 30 minutes. The watchdog will trip if it goes over budget.
        await asyncio.to_thread(_run, exec_cmd, timeout=3600)
        print("  Fine-tune OK.")
        print()

        # 5. Download the LoRA
        print("  Downloading the trained LoRA adapter …")
        lora_local = str(download_dir / "lora")
        receive_cmd = build_receive_command(
            pod_id=pod_id,
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
        _teardown_active_pod()

    return success, tracker, lora_local


# ── Stub-mode plan (always safe; never spends a cent) ─────────────


def print_orchestration_plan(*, gpu_type: str, budget_usd: float, pod_name: str) -> None:
    """Print the exact commands the live path would run + the budget breakdown."""
    price = GPU_PRICE_PER_HR_USD.get(gpu_type, 0.69)
    expected_cost = price * EXPECTED_FINE_TUNE_HOURS

    print("  ── Commands runpodctl would run (in order) ──")
    print()
    print("  1. Create the pod:")
    print(f"     $ {shlex.join(build_create_pod_command(gpu_type=gpu_type, name=pod_name))}")
    print()
    print("  2. Upload the email-triage training data:")
    print(f"     $ {shlex.join(build_send_command(local_path='./email_triage.jsonl', pod_id='<pod-id>', remote_path='/workspace/email_triage.jsonl'))}")
    print()
    print("  3. Run the Unsloth fine-tune on the rented GPU:")
    print("     $ runpodctl exec --pod <pod-id> -- bash -lc '<unsloth recipe>'")
    print("       (recipe: 4-bit Llama-3.2-3B + LoRA r=16, alpha=32, 1 epoch)")
    print()
    print("  4. Download the trained LoRA adapter:")
    print(f"     $ {shlex.join(build_receive_command(pod_id='<pod-id>', remote_path='/workspace/output/lora', local_path='./lora'))}")
    print()
    print("  5. Tear down the pod (always — cleanup runs even on failure):")
    print(f"     $ {shlex.join(build_remove_command(pod_id='<pod-id>'))}")
    print()
    print("  ── Budget breakdown ──")
    print()
    print(f"  GPU             = {gpu_type}")
    print(f"  Price           = ${price:.4f}/hr")
    print(f"  Expected hours  = {EXPECTED_FINE_TUNE_HOURS:.2f}h "
          "(Unsloth 3B LoRA, 8 samples, 1 epoch)")
    print(f"  Expected spend  = ${expected_cost:.4f}")
    print(f"  Budget cap      = ${budget_usd:.2f}  "
          f"(watchdog kills pod if exceeded)")
    print()
    if expected_cost > budget_usd:
        print(f"  [warn] expected spend ${expected_cost:.4f} > budget "
              f"${budget_usd:.2f}; the watchdog would trip.")
        print()


def print_live_proof(
    *, success: bool, tracker: GpuSpendTracker, lora_local_path: str | None,
    budget_usd: float, gpu_type: str,
) -> None:
    """Print the proof block after a live run."""
    rental_minutes = tracker.elapsed_seconds / 60.0
    rental_cost = tracker.accrued_usd
    # The blended-cost view in the Observatory dashboard sums GPU-rental
    # spend (this tracker) and per-call LLM spend (calculate_cost); the
    # import below keeps that pairing visible in this example.
    cloud_call_baseline = calculate_cost(
        input_tokens=250, output_tokens=30,
        model="claude-haiku-4-5-20251001",
    )

    print(f"  Pod outcome       : {'completed' if success else 'failed'}")
    print(f"  GPU               : {gpu_type} @ ${tracker.price_per_hour_usd:.4f}/hr")
    print(f"  Rental duration   : {rental_minutes:.1f} min "
          f"({tracker.elapsed_seconds:.0f}s wall)")
    print(f"  Rental spend      : ${rental_cost:.4f}  "
          f"(budget cap = ${budget_usd:.2f})")
    print(f"  Cloud-call baseline (calculate_cost): ${cloud_call_baseline:.6f}/call")
    if lora_local_path:
        print(f"  LoRA downloaded   : {lora_local_path}")
    print(f"  Pod torn down     : {_TEARDOWN_DONE}")
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
          "(this RunPod fine-tune)")
    print()
    print(f"  Payback           : after ~{payback_calls} cloud calls, "
          "the fine-tune has paid for itself")
    print(f"                      ({payback_calls / daily_volume:.1f} days at "
          f"{daily_volume}/day)")
    print()


# ── main ───────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Example 47 — RunPod fine-tune orchestration.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Actually rent a pod (requires RUNPOD_API_KEY + runpodctl). "
             "Default is stub mode — prints commands without spending.",
    )
    parser.add_argument(
        "--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
        help=f"Hard budget cap in USD; default ${DEFAULT_BUDGET_USD:.2f}. "
             "Watchdog kills the pod when accrued cost crosses this.",
    )
    parser.add_argument(
        "--gpu-type", default=POD_GPU_TYPE_DEFAULT,
        help=f"RunPod GPU type; default '{POD_GPU_TYPE_DEFAULT}'. "
             "Try 'NVIDIA RTX 4090' for ~half the per-hour cost.",
    )
    parser.add_argument(
        "--project-id", default="acme-prod",
        help="Project id used for spend attribution in the cost dashboard.",
    )
    args = parser.parse_args()

    _line()
    print(" Sagewai — RunPod fine-tune orchestration (example 47, Gap #8a)")
    _line()
    print()

    # ── 1. Probe the environment ───────────────────────────────
    _line(" 1. Probe runtime environment ")
    print()
    env = _detect_environment()
    print(f"  RUNPOD_API_KEY in env  : {'✓' if env.has_runpod_key else '✗'}  "
          "(read from ~/.sagewai/.env via python-dotenv)")
    print(f"  runpodctl on PATH      : {'✓' if env.has_runpodctl else '✗'}"
          + (f"  ({env.runpodctl_version})" if env.runpodctl_version else ""))
    print(f"  --live flag passed     : {'✓' if args.live else '✗'}")
    print()

    will_go_live = args.live and env.can_go_live
    if args.live and not env.can_go_live:
        print("  [warn] --live requested but environment is incomplete.")
        if not env.has_runpod_key:
            print("         Set RUNPOD_API_KEY in ~/.sagewai/.env "
                  "(template: atelier/docs/v1.0/inference-provisioning-setup.md)")
        if not env.has_runpodctl:
            print("         Install runpodctl: brew install runpod/runpodctl/runpodctl")
        print("  Falling back to stub mode for this run.")
        print()

    # ── 2. Print the orchestration plan (always — stub or live) ─
    pod_name = f"sagewai-ft-{int(time.time())}"
    _line(" 2. Orchestration plan ")
    print()
    print_orchestration_plan(
        gpu_type=args.gpu_type,
        budget_usd=args.budget_usd,
        pod_name=pod_name,
    )

    # ── 3. Live or stub ────────────────────────────────────────
    if will_go_live:
        _line(" 3. Live orchestration ")
        print()
        _register_signal_handlers(dry_run=False)
        download_dir = Path(tempfile.mkdtemp(prefix="sagewai-runpod-out-"))
        success, tracker, lora_local_path = await run_live(
            gpu_type=args.gpu_type,
            budget_usd=args.budget_usd,
            project_id=args.project_id,
            pod_name=pod_name,
            download_dir=download_dir,
        )
        print()
        _line(" 4. The proof — live run ")
        print()
        print_live_proof(
            success=success,
            tracker=tracker,
            lora_local_path=lora_local_path,
            budget_usd=args.budget_usd,
            gpu_type=args.gpu_type,
        )
        gpu_spend = tracker.accrued_usd
    else:
        _line(" 3. Stub mode — no spend ")
        print()
        print("  No live orchestration requested. To run for real:")
        print("    1. Set RUNPOD_API_KEY in ~/.sagewai/.env")
        print("    2. brew install runpod/runpodctl/runpodctl")
        print("    3. python 47_runpod_finetune_orchestration.py --live")
        print()
        print("  Setup walkthrough: "
              "atelier/docs/v1.0/inference-provisioning-setup.md")
        print()
        # Use the expected spend as the cost-down baseline so the audience
        # sees the same number they'd pay live.
        gpu_spend = (
            GPU_PRICE_PER_HR_USD.get(args.gpu_type, 0.69)
            * EXPECTED_FINE_TUNE_HOURS
        )

    # ── 4/5. Cost-down (always — the headline pitch) ──────────
    _line(" Cost-down: cloud-LLM baseline vs. fine-tuned local ")
    print()
    print_costdown(
        gpu_rental_usd=gpu_spend,
        baseline_call_usd=BASELINE_COST_PER_CALL_USD,
        daily_volume=PRODUCTION_VOLUME_PER_DAY,
    )

    _line(" The training-loop pillar ")
    print()
    print("  RunPod is the default working tier — corporate card, no AWS.")
    print("  $1-2 in real spend rents an RTX 5090 long enough to fit a")
    print("  3B LoRA on YOUR data. Cleanup-on-failure is enforced three")
    print("  ways (try/finally, atexit, SIGTERM) so a stuck pod can never")
    print("  drain your budget. The downloaded LoRA deploys via Ollama")
    print("  (Example 38) and the same task costs $0/call thereafter.")
    print()
    print("  Optionality is the brand: when Anthropic raises prices, you")
    print("  already have your own model.")
    print()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        # Make sure the pod is gone even on Ctrl-C during the watchdog loop.
        _teardown_active_pod()
        sys.exit(130)
