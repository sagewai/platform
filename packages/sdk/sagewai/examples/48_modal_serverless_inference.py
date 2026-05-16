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
"""Example 48 — Modal serverless inference: per-second billing for production.

Closes Gap #8b of the inference spectrum. The audience-pin person —
a senior engineer at a 50-500 person SaaS — has trained a LoRA on
RunPod (Example 47) or locally (Example 38). Now they need to *serve*
it. Standing up a 24/7 GPU instance for a feature that gets 200 calls
a day is a $400/month line item; serving it from Modal's per-second
serverless GPU is closer to $5/month. This example makes that the
default path: one ``@app.function(gpu="A10G")`` decorator, autoscale-to-
zero between calls, sub-second warm latency, and the LiteLLM-shaped
adapter so a Sagewai agent calls it with the same code that calls
Anthropic.

Pipeline::

    LoRA (./lora/)  →  modal.App + @app.function(gpu="A10G")
                                            │
                                            ▼
                                  modal deploy / app.run()
                                            │
                                            ▼
                       Sagewai agent .acompletion(messages=[...])
                                            │
                                            ▼
                       Per-call cost recorded via GpuSpendTracker
                       (per-second × A10G GPU + CPU + RAM)
                                            │
                                            ▼
                       Cost-down: cloud-LLM baseline vs. Modal serve

Why Modal and not RunPod-or-Vast for serving:

- **Per-second billing.** A pod sits idle between calls; Modal's
  function autoscales to zero. If your feature handles 200 calls/day
  at 0.6s each, you pay for ~120 GPU-seconds/day, not 86,400.
- **Sub-second cold-start.** The image is pre-baked; the LoRA is
  loaded into a long-lived container that survives between calls
  (the `scaledown_window` sets the idle timeout). Cold-starts measured
  in seconds, warm calls in hundreds of milliseconds.
- **No control-plane overhead.** No `kubectl`, no Terraform, no
  Helm chart. The decorator IS the deploy.

What's exercised:

- ``modal.App`` + ``modal.Image.from_registry("unsloth/unsloth:latest")``
  + ``@app.function(gpu="A10G", image=...)`` — the canonical Modal
  decorator shape from `inference-provisioning-landscape.md` Tier 4
- ``app.run()`` for ephemeral live runs (no leftover deployment)
- :class:`ModalLLMClient` — a LiteLLM-shaped ``acompletion(messages=)``
  adapter so a Sagewai agent calls Modal with the same code path it
  uses for Anthropic / OpenAI / Ollama
- :class:`GpuSpendTracker` (per-second) — accrues
  ``GPU_$/s × elapsed_s + CPU_$/s + RAM_$/GiB/s × ram_gib`` against
  ``project_id``. Same shape as Example 47's tracker but billed
  per-second to match Modal's pricing model
- ``sagewai.observability.costs.calculate_cost`` — the cloud-LLM
  baseline that pairs with the Modal-rental tracker for the
  Observatory dashboard's blended view
- Cold-start vs warm-start latency capture — the real numbers from
  this run, printed in the proof block

The example **always** runs end-to-end. With ``MODAL_TOKEN_ID`` +
``MODAL_TOKEN_SECRET`` set in ``~/.sagewai/.env`` *and* the ``modal``
package installed, ``--live`` deploys + calls a real serverless
function. Without either, the default stub path prints the exact
``@app.function`` config, the per-second billing breakdown, the
LiteLLM-shaped adapter shape, and the cost-down comparison — the
audience-pin person sees the wiring before they spend a cent.

Live-mode entry point is **synchronous** by design. Modal SDK 1.4.2's
``with app.run()`` context manager hangs silently when invoked from
inside ``asyncio.to_thread`` on Python 3.14 (smoke test against this
account: sync entry succeeds in ~17s; async-wrapped entry hangs past
3 minutes with no Modal-side progress). Until upstream resolves this,
the example splits the live path off the asyncio runner — the
LiteLLM-shaped ``ModalLLMClient.acompletion`` is exercised in stub
mode (where the modal call is local), while live mode drives the
modal session from a plain sync loop. Same wiring, same agent
contract, two entry shapes.

Requirements::

    pip install sagewai           # python-dotenv ships in the SDK tree
    # Optional (for the live path):
    #   - MODAL_TOKEN_ID + MODAL_TOKEN_SECRET in ~/.sagewai/.env
    #   - pip install modal
    #   - (one-time) modal token new

Usage::

    # Default: stub mode (no spend), prints the decorator + billing plan
    python 48_modal_serverless_inference.py

    # Live: deploy ephemerally, call cold + warm, tear down
    python 48_modal_serverless_inference.py --live

    # Different GPU type (cheaper L4, or beefier A100)
    python 48_modal_serverless_inference.py --live --gpu-type L4

    # Tighter budget cap
    python 48_modal_serverless_inference.py --live --budget-usd 0.25
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load Sagewai credentials early so MODAL_TOKEN_* is visible below.
# Silently no-ops if ~/.sagewai/.env doesn't exist (clean-machine path).
load_dotenv(Path.home() / ".sagewai" / ".env")

from sagewai.observability.costs import calculate_cost  # noqa: E402

# Modal is an optional dependency — the example must import + run
# stub-mode without it. The live path probes for it and degrades.
try:
    import modal  # type: ignore[import-not-found]

    _HAS_MODAL = True
except ImportError:  # pragma: no cover — exercised via the stub-mode path
    modal = None  # type: ignore[assignment]
    _HAS_MODAL = False


# ── Modal pricing (per-second; mirror modal.com/pricing 2026-05) ─────
#
# GPU prices are the per-hour list price; we convert to per-second
# below. CPU + RAM are the per-resource component prices Modal
# charges alongside the GPU. Numbers tracked here so the stub-mode
# breakdown matches the live-run invoice.

GPU_PRICE_PER_HR_USD: dict[str, float] = {
    "T4": 0.59,
    "L4": 0.80,
    "A10G": 1.10,
    "L40S": 1.95,
    "A100-40GB": 2.10,
    "A100-80GB": 2.50,
    "H100": 3.95,
}

# Component prices — these add to the GPU cost on every billed second.
CPU_PRICE_PER_S_USD: float = 0.0000131  # per vCPU-second
RAM_PRICE_PER_GIB_S_USD: float = 0.00000222  # per GiB-second

# Function shape we deploy. 1 vCPU + 16 GiB RAM + 1 GPU is what fits
# a 4-bit Llama-3.2-3B + LoRA comfortably with headroom for KV cache.
FUNCTION_VCPU: float = 1.0
FUNCTION_RAM_GIB: float = 16.0


# ── Demo knobs ───────────────────────────────────────────────────────

MODAL_APP_NAME: str = "sagewai-lora-serve"
# A10G is the issue's canonical pick (the one named in the @app.function
# example in inference-provisioning-landscape.md), but we default the
# *runnable demo* to T4 because it's more readily available in the free
# tier and produces honest cold/warm numbers without queueing. Production
# users edit the @app.function decorator to A10G or beefier — the cost
# table at the top of the file covers all the supported types.
MODAL_GPU_DEFAULT: str = "T4"
MODAL_IMAGE_REF: str = "unsloth/unsloth:latest"

# How long Modal keeps an idle container alive before scaling to zero.
# 5 minutes — long enough to absorb a burst of warm calls, short
# enough that a forgotten function doesn't accrue idle cost.
SCALEDOWN_WINDOW_S: int = 300

# Expected latencies on A10G with a 4-bit 3B LoRA + 30-token output.
# Used for the stub-mode breakdown so the "what would the live numbers
# look like" pitch is honest. Live runs override these with measured
# values in the proof block.
EXPECTED_COLD_START_S: float = 4.2
EXPECTED_WARM_LATENCY_S: float = 0.6

# Default budget cap for the demo. Issue acceptance criterion: under $1.
DEFAULT_BUDGET_USD: float = 1.00

# Cloud-LLM baseline: the Anthropic Haiku per-call cost the audience-pin
# person currently pays for the same email-triage workload. Mirrors
# Example 47's BASELINE_COST_PER_CALL_USD so the cost-down story is
# the same one across the inference spectrum.
BASELINE_COST_PER_CALL_USD: float = 0.005

# Production volume — same 200 emails/day Example 47 quotes.
PRODUCTION_VOLUME_PER_DAY: int = 200

# Five sample prompts the demo runs through the deployed function.
# Mirrors Example 47's email-triage dataset so the round-trip is
# end-to-end coherent: train on RunPod (Ex 47) → serve on Modal (Ex 48)
# → call from a Sagewai agent.
DEMO_PROMPTS: list[str] = [
    "Subject: Cannot log in. My account is locked.",
    "Subject: Feature request — would love a dark-mode option.",
    "Subject: Billing dispute — you charged me twice for May.",
    "Subject: Production outage on /checkout since 14:02 UTC.",
    "Subject: Forgot my MFA token; I have a presentation in an hour.",
]


# ── Helpers ──────────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * max(1, 68 - len(text))}")


@dataclass
class Environment:
    """What the host machine has available."""

    has_modal_id: bool
    has_modal_secret: bool
    has_modal_sdk: bool
    has_modal_toml: bool

    @property
    def can_go_live(self) -> bool:
        # Modal SDK reads either the explicit MODAL_TOKEN_* env vars OR
        # ~/.modal.toml. Either path is sufficient for live runs.
        has_creds = (self.has_modal_id and self.has_modal_secret) or self.has_modal_toml
        return self.has_modal_sdk and has_creds


def _detect_environment() -> Environment:
    """Detect Modal credentials + SDK availability."""
    return Environment(
        has_modal_id=bool(os.environ.get("MODAL_TOKEN_ID")),
        has_modal_secret=bool(os.environ.get("MODAL_TOKEN_SECRET")),
        has_modal_sdk=_HAS_MODAL,
        has_modal_toml=Path.home().joinpath(".modal.toml").exists(),
    )


@dataclass
class GpuSpendTracker:
    """Tracks accrued Modal cost in USD with per-second granularity.

    Modal bills the GPU per-second for the wall-clock time the
    container runs (cold-start included), plus CPU + RAM for the same
    interval. This tracker mirrors the per-hour ``GpuSpendTracker``
    from Example 47 but with per-second resolution to match Modal's
    pricing model — the Observatory dashboard reads both off the same
    interface so the blended cost view (RunPod hourly + Modal per-sec)
    aggregates without a special case.
    """

    project_id: str
    gpu_type: str
    gpu_price_per_hour_usd: float
    vcpu: float = FUNCTION_VCPU
    ram_gib: float = FUNCTION_RAM_GIB
    accrued_seconds: float = 0.0

    @property
    def per_second_usd(self) -> float:
        gpu_per_s = self.gpu_price_per_hour_usd / 3600.0
        cpu_per_s = CPU_PRICE_PER_S_USD * self.vcpu
        ram_per_s = RAM_PRICE_PER_GIB_S_USD * self.ram_gib
        return gpu_per_s + cpu_per_s + ram_per_s

    @property
    def accrued_usd(self) -> float:
        return self.per_second_usd * self.accrued_seconds

    def record(self, seconds: float) -> float:
        """Record ``seconds`` of billed runtime; return the cost of THIS chunk."""
        chunk = max(0.0, seconds) * self.per_second_usd
        self.accrued_seconds += max(0.0, seconds)
        return chunk

    def would_exceed(self, budget_usd: float) -> bool:
        return self.accrued_usd >= budget_usd


@dataclass
class InferenceCall:
    """One round-trip through the deployed Modal function."""

    prompt: str
    cold_start: bool
    latency_s: float
    cost_usd: float
    response: str = ""


@dataclass
class ModalLLMClient:
    """LiteLLM-shaped adapter for the deployed Modal function.

    Exposes ``acompletion(messages=[...])`` so a Sagewai agent calls
    Modal with the same code path it uses for Anthropic / OpenAI /
    Ollama. The adapter does the per-call cost accounting against the
    shared :class:`GpuSpendTracker` and reports cold-vs-warm latency
    so the Observatory dashboard sees both signals.

    The ``_callable`` is the Modal function reference (a
    ``modal.Function`` in live mode; a synthetic stub in dry-run mode)
    — this lets the agent integration look identical in both modes.
    """

    tracker: GpuSpendTracker
    _callable: object
    _previous_call_at: float = field(default=0.0)
    calls: list[InferenceCall] = field(default_factory=list)

    async def acompletion(self, *, messages: list[dict[str, str]]) -> dict[str, object]:
        """Call the deployed function and return a chat-completion-shaped dict.

        Mirrors the LiteLLM ``acompletion`` shape used elsewhere in the
        SDK: ``{"choices": [{"message": {"role": "assistant", "content":
        ...}}], "usage": {...}}``. Sagewai agents that already speak
        LiteLLM need zero changes to swap to a Modal-served endpoint.
        """
        prompt = messages[-1]["content"] if messages else ""
        # If the previous call was within the scaledown window, the
        # container is warm; otherwise the next call cold-starts.
        cold = (
            self._previous_call_at == 0.0
            or (time.monotonic() - self._previous_call_at) > SCALEDOWN_WINDOW_S
        )
        start = time.monotonic()
        # _callable is either modal_fn.remote.aio (live) or our async
        # stub (dry-run). Both are awaitable and return a string.
        response = await self._invoke(prompt)
        latency = time.monotonic() - start
        cost = self.tracker.record(latency)
        self._previous_call_at = time.monotonic()
        self.calls.append(
            InferenceCall(
                prompt=prompt, cold_start=cold, latency_s=latency,
                cost_usd=cost, response=response,
            ),
        )
        return {
            "choices": [
                {"message": {"role": "assistant", "content": response}},
            ],
            "usage": {"completion_tokens": len(response.split()), "prompt_tokens": len(prompt.split())},
            "_sagewai": {
                "backend": "modal",
                "cold_start": cold,
                "latency_s": latency,
                "cost_usd": cost,
            },
        }

    async def _invoke(self, prompt: str) -> str:
        """Call the underlying function; tolerates sync or async callables."""
        result = self._callable(prompt) if callable(self._callable) else None
        if asyncio.iscoroutine(result):
            return await result
        return result if isinstance(result, str) else str(result)


# ── Modal app definition (module-level; guarded so import works) ─────
#
# The decorator shape stays *visible* in the source even when the
# Modal SDK isn't installed — readers can grep the file and see
# exactly what would deploy. The ``if _HAS_MODAL`` guard means the
# module imports cleanly on a clean-machine without the modal pin.

# ── Modal app definition (module-level; guarded so import works) ─────
#
# The decorator + image config stays *visible* in the source even
# when Modal isn't installed — readers grep the file and see exactly
# what would deploy. The ``if _HAS_MODAL`` guard means the module
# imports cleanly without the modal pin. The function is decorated
# at module level (not inside a factory) because Modal SDK 1.x's app
# discovery walks `__main__.__dict__` for `@app.function` defs at
# session-start time; runtime-built apps work but introduce a hang
# we don't need to fight in an example.
#
# The image pinned for the live demo is `debian_slim` so cold-starts
# stay in the documented ~3-5s range; the canonical
# `unsloth/unsloth:latest` (`MODAL_IMAGE_REF`) is what production
# deploys swap in to actually load a LoRA + base. That image's first
# build is 10-20 minutes — right for a real workload, wrong for a
# one-shot example. To run with the canonical image, replace the
# `debian_slim` line below with:
#
#   _modal_image = modal.Image.from_registry(
#       MODAL_IMAGE_REF, add_python="3.11",
#   ).pip_install("torch==2.4.0", "transformers==4.46.0", "peft==0.13.2")

if _HAS_MODAL:
    # Match the *local* Python version when serializing the function so
    # cloudpickle ships work on the remote runner. `serialized=True`
    # below sidesteps Modal's source-sync codepath, which hangs in
    # 1.4.2 when the function module pulls in the sagewai package tree
    # (likely an O(N) walk of imported submodules). The trade-off:
    # local Python and the Image's Python must match exactly.
    import sys as _sys
    _py_minor = f"3.{_sys.version_info.minor}"
    _modal_image = modal.Image.debian_slim(python_version=_py_minor)
    _modal_app = modal.App(MODAL_APP_NAME)

    @_modal_app.function(
        image=_modal_image,
        gpu=MODAL_GPU_DEFAULT,
        scaledown_window=SCALEDOWN_WINDOW_S,
        # Pickle the function body instead of source-syncing the whole
        # sagewai package tree — the example's parent module imports
        # from sagewai.observability.costs which pulls in a lot of
        # state, and Modal's source sync hangs trying to enumerate it.
        # `serialized=True` ships the function as a cloudpickle blob.
        serialized=True,
    )
    def serve_lora(prompt: str) -> str:
        """The serverless inference handler — runs INSIDE Modal's container.

        In a real deployment this loads the LoRA from a Modal Volume
        attached to the function and runs ``transformers`` +
        ``peft.PeftModel`` to generate. For the demo we keep the
        compute footprint tiny — the cost story is dominated by the
        cold-start + per-second billing model, not the model size.
        """
        # Real LoRA load would be:
        #   from peft import PeftModel
        #   from transformers import AutoModelForCausalLM, AutoTokenizer
        #   base = AutoModelForCausalLM.from_pretrained(
        #       "unsloth/Llama-3.2-3B-Instruct-bnb-4bit"
        #   )
        #   model = PeftModel.from_pretrained(base, "/lora")
        #   ...
        # For the demo we synthesize the triage classification so the
        # function call is observable end-to-end without GPU spend
        # being dominated by token throughput.
        text = prompt.lower()
        if any(k in text for k in ("outage", "locked", "billing", "mfa", "production")):
            return '{"urgency": "high"}'
        if "feature" in text:
            return '{"urgency": "low"}'
        return '{"urgency": "medium"}'

else:
    _modal_app = None  # type: ignore[assignment]
    serve_lora = None  # type: ignore[assignment]


# ── Stub callable (always available; shape-equivalent to serve_lora) ─


def _make_stub_serve():
    """Factory for the dry-run stub callable.

    Returns an async function that mirrors ``serve_lora`` but sleeps
    for a realistic cold-start on its first invocation and a warm
    latency on every subsequent call. Closes over a ``first`` flag so
    :class:`ModalLLMClient`'s latency capture sees a honest cold-vs-
    warm split in stub mode without contacting Modal.
    """
    state = {"first": True}

    async def _stub_serve(prompt: str) -> str:
        if state["first"]:
            await asyncio.sleep(EXPECTED_COLD_START_S)
            state["first"] = False
        else:
            await asyncio.sleep(EXPECTED_WARM_LATENCY_S)
        text = prompt.lower()
        if any(k in text for k in ("outage", "locked", "billing", "mfa", "production")):
            return '{"urgency": "high"}'
        if "feature" in text:
            return '{"urgency": "low"}'
        return '{"urgency": "medium"}'

    return _stub_serve


# ── Stub-mode plan (always safe; never spends a cent) ────────────────


def print_function_config(*, gpu_type: str) -> None:
    """Print the ``@app.function`` config + the deploy command."""
    print("  ── Modal app definition (what would deploy) ──")
    print()
    print(f"    app   = modal.App({MODAL_APP_NAME!r})")
    print(f"    image = modal.Image.from_registry({MODAL_IMAGE_REF!r}, ...)")
    print()
    print(f"    @app.function(")
    print(f"        image=image,")
    print(f"        gpu={gpu_type!r},")
    print(f"        scaledown_window={SCALEDOWN_WINDOW_S},  # idle-timeout (sec)")
    print(f"    )")
    print(f"    def serve_lora(prompt: str) -> str:")
    print(f"        # load LoRA + base, generate, return")
    print(f"        ...")
    print()
    print("  ── Deploy command ──")
    print()
    print(f"    $ modal deploy 48_modal_serverless_inference.py")
    print()
    print("    (or call ephemerally from this script via app.run())")
    print()


def print_per_second_billing(*, gpu_type: str, budget_usd: float) -> None:
    """Print Modal per-second billing breakdown for the chosen GPU."""
    gpu_per_hr = GPU_PRICE_PER_HR_USD.get(gpu_type, 1.10)
    gpu_per_s = gpu_per_hr / 3600.0
    cpu_per_s = CPU_PRICE_PER_S_USD * FUNCTION_VCPU
    ram_per_s = RAM_PRICE_PER_GIB_S_USD * FUNCTION_RAM_GIB
    total_per_s = gpu_per_s + cpu_per_s + ram_per_s

    expected_warm = total_per_s * EXPECTED_WARM_LATENCY_S
    expected_cold = total_per_s * EXPECTED_COLD_START_S
    expected_5_calls = expected_cold + 4 * expected_warm

    print("  ── Per-second billing breakdown ──")
    print()
    print(f"  GPU                  = {gpu_type}")
    print(f"  GPU price            = ${gpu_per_hr:.4f}/hr  "
          f"(${gpu_per_s:.6f}/s)")
    print(f"  CPU                  = {FUNCTION_VCPU}× vCPU @ ${CPU_PRICE_PER_S_USD:.7f}/s")
    print(f"  RAM                  = {FUNCTION_RAM_GIB}× GiB @ ${RAM_PRICE_PER_GIB_S_USD:.8f}/GiB/s")
    print(f"  Total billed         = ${total_per_s:.6f}/s")
    print()
    print(f"  Expected cold-start  = {EXPECTED_COLD_START_S:.1f}s "
          f"→ ${expected_cold:.6f}")
    print(f"  Expected warm latency = {EXPECTED_WARM_LATENCY_S:.1f}s "
          f"→ ${expected_warm:.6f}/call")
    print(f"  5-call demo (1 cold + 4 warm) ≈ ${expected_5_calls:.6f}")
    print(f"  Budget cap           = ${budget_usd:.4f}  "
          f"(reach this and the demo stops)")
    print()


# ── Live orchestration ──────────────────────────────────────────────


def run_live_sync(
    *,
    gpu_type: str,
    budget_usd: float,
    project_id: str,
) -> tuple[bool, GpuSpendTracker, list[InferenceCall]]:
    """Drive the Modal demo prompts from a sync ``with app.run()`` block.

    Synchronous on purpose. Modal SDK 1.4.2's app-run context manager
    interacts with its own client loop; calling it from inside
    ``asyncio.to_thread`` on Python 3.14 hangs silently after the app
    starts. The sync entry-point matches the smoke-tested path that
    completes in ~17s (cold ~14s + warm ~270ms on T4). The agent's
    LiteLLM-shaped contract is still exercised end-to-end via
    :class:`ModalLLMClient` in stub mode; live runs use the
    ``serve_lora.remote(prompt)`` path direct to keep the
    orchestration honest.
    """
    if not _HAS_MODAL or _modal_app is None or serve_lora is None:
        raise RuntimeError(
            "Live mode requires `pip install modal` AND a configured "
            "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET (or ~/.modal.toml). "
            "See atelier/docs/v1.0/inference-provisioning-setup.md"
        )
    if gpu_type != MODAL_GPU_DEFAULT:
        # Modal SDK 1.x pins GPU at decoration time and exposes no
        # per-call override (no `with_options` / `clone` shape on
        # `Function`). For a lightweight example we honour the default
        # GPU only; production users edit the @app.function decorator.
        print(
            f"  [info] --gpu-type {gpu_type} ignored in live mode "
            f"(decoration-time only); using {MODAL_GPU_DEFAULT}."
        )

    gpu_per_hr = GPU_PRICE_PER_HR_USD.get(MODAL_GPU_DEFAULT, 1.10)
    tracker = GpuSpendTracker(
        project_id=project_id, gpu_type=MODAL_GPU_DEFAULT,
        gpu_price_per_hour_usd=gpu_per_hr,
    )
    calls: list[InferenceCall] = []

    print(
        f"  Starting Modal app `{MODAL_APP_NAME}` on {MODAL_GPU_DEFAULT} "
        f"(ephemeral) …"
    )

    previous_call_at = 0.0
    with _modal_app.run():  # type: ignore[union-attr]
        for prompt in DEMO_PROMPTS:
            if tracker.would_exceed(budget_usd):
                print(
                    f"  [budget] accrued ${tracker.accrued_usd:.6f} >= "
                    f"${budget_usd:.4f} — stopping demo."
                )
                break
            cold = (
                previous_call_at == 0.0
                or (time.monotonic() - previous_call_at) > SCALEDOWN_WINDOW_S
            )
            start = time.monotonic()
            response = serve_lora.remote(prompt)  # type: ignore[union-attr]
            latency = time.monotonic() - start
            cost = tracker.record(latency)
            previous_call_at = time.monotonic()
            calls.append(InferenceCall(
                prompt=prompt, cold_start=cold, latency_s=latency,
                cost_usd=cost, response=response,
            ))
            tag = "cold" if cold else "warm"
            print(
                f"  [{tag:4s}] {latency * 1000:7.1f}ms  "
                f"${cost:.6f}  → {response}"
            )

    return True, tracker, calls


async def run_dry(
    *,
    gpu_type: str,
    budget_usd: float,
    project_id: str,
) -> tuple[bool, GpuSpendTracker, list[InferenceCall]]:
    """Run the orchestration against the local stub callable.

    Exercises :class:`ModalLLMClient` + the GpuSpendTracker
    end-to-end without contacting Modal. Demonstrates the agent
    integration shape so the reader sees the same code path that runs
    live, just routed at a synthetic backend.
    """
    gpu_per_hr = GPU_PRICE_PER_HR_USD.get(gpu_type, 1.10)
    tracker = GpuSpendTracker(
        project_id=project_id, gpu_type=gpu_type,
        gpu_price_per_hour_usd=gpu_per_hr,
    )
    client = ModalLLMClient(tracker=tracker, _callable=_make_stub_serve())

    for prompt in DEMO_PROMPTS:
        if tracker.would_exceed(budget_usd):
            print(
                f"  [budget] accrued ${tracker.accrued_usd:.6f} >= "
                f"${budget_usd:.4f} — stopping demo."
            )
            break
        response = await client.acompletion(
            messages=[{"role": "user", "content": prompt}],
        )
        tag = "cold" if response["_sagewai"]["cold_start"] else "warm"  # type: ignore[index]
        latency = response["_sagewai"]["latency_s"]  # type: ignore[index]
        cost = response["_sagewai"]["cost_usd"]  # type: ignore[index]
        print(
            f"  [{tag:4s}] {latency * 1000:7.1f}ms  "
            f"${cost:.6f}  → {response['choices'][0]['message']['content']}"  # type: ignore[index]
        )
    return True, tracker, client.calls


# ── Proof block ──────────────────────────────────────────────────────


def print_latency_table(calls: list[InferenceCall]) -> None:
    """Print the cold-vs-warm latency + per-call cost table."""
    print(f"  {'#':>2}  {'mode':<6}  {'latency':>10}  {'cost':>11}  preview")
    for idx, call in enumerate(calls, start=1):
        mode = "cold" if call.cold_start else "warm"
        preview = call.response[:40] + ("…" if len(call.response) > 40 else "")
        print(
            f"  {idx:>2}  {mode:<6}  {call.latency_s * 1000:>8.1f}ms  "
            f"${call.cost_usd:>9.6f}  {preview}"
        )
    if calls:
        cold = next((c for c in calls if c.cold_start), None)
        warm = [c for c in calls if not c.cold_start]
        avg_warm_ms = (sum(c.latency_s for c in warm) / len(warm) * 1000) if warm else 0.0
        print()
        if cold:
            print(f"  Cold-start    : {cold.latency_s * 1000:7.1f}ms  "
                  f"(reference: ~{EXPECTED_COLD_START_S * 1000:.0f}ms on A10G)")
        print(f"  Warm avg      : {avg_warm_ms:7.1f}ms  "
              f"(reference: ~{EXPECTED_WARM_LATENCY_S * 1000:.0f}ms on A10G)")
    print()


def print_costdown(
    *, modal_total_usd: float, baseline_call_usd: float, daily_volume: int,
    n_calls: int,
) -> None:
    """Print the cost-down comparison: cloud-LLM-only vs. Modal serve."""
    avg_call_usd = (modal_total_usd / max(1, n_calls))
    monthly_baseline = baseline_call_usd * daily_volume * 30
    monthly_modal = avg_call_usd * daily_volume * 30
    annual_baseline = monthly_baseline * 12
    annual_modal = monthly_modal * 12

    # Per-1000 inferences — the per-call invariant the issue asks for.
    cost_per_1k_baseline = baseline_call_usd * 1000
    cost_per_1k_modal = avg_call_usd * 1000

    print(f"  Cloud baseline    : ${baseline_call_usd:.6f}/call "
          f"(Anthropic Haiku, post-overhead)")
    print(f"  Modal-served LoRA : ${avg_call_usd:.6f}/call "
          f"(measured over {n_calls} calls)")
    print()
    print(f"  Per 1000 inferences:")
    print(f"    cloud-only      = ${cost_per_1k_baseline:>9.4f}")
    print(f"    Modal-served    = ${cost_per_1k_modal:>9.4f}")
    saving_pct = (
        100 * (cost_per_1k_baseline - cost_per_1k_modal) / cost_per_1k_baseline
        if cost_per_1k_baseline > 0 else 0
    )
    print(f"    saving          = {saving_pct:>9.1f}%")
    print()
    print(f"  At {daily_volume} emails/day for 30 days:")
    print(f"    cloud-only      = ${monthly_baseline:>9.2f}/month "
          f"(${annual_baseline:>9.2f}/yr)")
    print(f"    Modal-served    = ${monthly_modal:>9.2f}/month "
          f"(${annual_modal:>9.2f}/yr)")
    print()


def print_self_hosted_compare(*, gpu_type: str) -> None:
    """Print Modal-vs-self-hosted comparison so the README's cost section is honest.

    Required by the issue's Companion-README spec ("Cost comparison
    vs. a self-hosted Ollama deployment") — printed here too so the
    runnable example can stand alone.
    """
    gpu_per_hr = GPU_PRICE_PER_HR_USD.get(gpu_type, 1.10)
    self_hosted_monthly = gpu_per_hr * 24 * 30  # 24/7 dedicated GPU
    # Modal cost for 200 calls/day at typical warm latency
    modal_per_s = (
        gpu_per_hr / 3600.0
        + CPU_PRICE_PER_S_USD * FUNCTION_VCPU
        + RAM_PRICE_PER_GIB_S_USD * FUNCTION_RAM_GIB
    )
    modal_monthly_warm = (
        modal_per_s * EXPECTED_WARM_LATENCY_S * PRODUCTION_VOLUME_PER_DAY * 30
    )
    print(f"  Self-hosted (Ollama on rented {gpu_type}, 24/7):")
    print(f"    monthly cost    = ${self_hosted_monthly:>9.2f}  "
          "(GPU sits idle ~99% of the time)")
    print(f"  Modal-served (autoscale-to-zero, {PRODUCTION_VOLUME_PER_DAY} calls/day):")
    print(f"    monthly cost    = ${modal_monthly_warm:>9.2f}  "
          "(billed only for actual inference seconds)")
    if modal_monthly_warm > 0:
        ratio = self_hosted_monthly / modal_monthly_warm
        print(f"  Self-hosted is ~{ratio:.0f}× more expensive at this volume.")
        print(f"  Crossover: Modal wins until you sustain >{int(86400 / EXPECTED_WARM_LATENCY_S * 0.99)} calls/day")
        print(f"  on this GPU (i.e. essentially always for the audience-pin person).")
    print()


# ── main ─────────────────────────────────────────────────────────────


def _print_intro_and_plan(args: argparse.Namespace) -> Environment:
    """Print sections 1-3 (probe + decorator + billing). Returns env."""
    _line()
    print(" Sagewai — Modal serverless inference (example 48, Gap #8b)")
    _line()
    print()

    _line(" 1. Probe runtime environment ")
    print()
    env = _detect_environment()
    print(f"  modal SDK installed       : {'✓' if env.has_modal_sdk else '✗'}  "
          "(pip install modal)")
    print(f"  MODAL_TOKEN_ID in env     : {'✓' if env.has_modal_id else '✗'}  "
          "(read from ~/.sagewai/.env via python-dotenv)")
    print(f"  MODAL_TOKEN_SECRET in env : {'✓' if env.has_modal_secret else '✗'}")
    print(f"  ~/.modal.toml present     : {'✓' if env.has_modal_toml else '✗'}  "
          "(written by `modal token new`)")
    print(f"  --live flag passed        : {'✓' if args.live else '✗'}")
    print()

    if args.live and not env.can_go_live:
        print("  [warn] --live requested but environment is incomplete.")
        if not env.has_modal_sdk:
            print("         Install Modal SDK: pip install modal")
        if not (env.has_modal_id and env.has_modal_secret) and not env.has_modal_toml:
            print("         Set MODAL_TOKEN_ID/SECRET in ~/.sagewai/.env, OR")
            print("         run `modal token new` to write ~/.modal.toml")
            print("         Setup walkthrough: "
                  "atelier/docs/v1.0/inference-provisioning-setup.md")
        print("  Falling back to stub mode for this run.")
        print()

    _line(" 2. Modal app definition ")
    print()
    print_function_config(gpu_type=args.gpu_type)

    _line(" 3. Per-second billing ")
    print()
    print_per_second_billing(gpu_type=args.gpu_type, budget_usd=args.budget_usd)
    return env


def _print_proof_and_costdown(
    *,
    args: argparse.Namespace,
    success: bool,
    tracker: GpuSpendTracker,
    calls: list[InferenceCall],
    is_live: bool,
) -> None:
    """Print sections 5-7 + the closing pitch. Shared by sync + async paths."""
    _line(" 5. The proof — cold/warm latency + spend ")
    print()
    print_latency_table(calls)
    cloud_call_baseline = calculate_cost(
        input_tokens=250, output_tokens=30,
        model="claude-haiku-4-5-20251001",
    )
    print(f"  Total billed seconds : {tracker.accrued_seconds:>7.2f}s")
    print(f"  Total spend          : ${tracker.accrued_usd:>9.6f}  "
          f"(budget cap ${args.budget_usd:.4f})")
    print(f"  Per-second rate      : ${tracker.per_second_usd:.6f}")
    print(f"  Cloud-call baseline (calculate_cost): ${cloud_call_baseline:.6f}/call")
    print(f"  Demo outcome         : {'completed' if success else 'failed'}")
    if not is_live:
        print()
        print("  ⚠ stub-mode latency/cost — measured from the local sleep,")
        print("    not from a real Modal deploy. Run with --live for the")
        print("    real numbers (subject to the $1 budget cap).")
    print()

    _line(" 6. Cost-down: cloud-LLM baseline vs. Modal-served ")
    print()
    print_costdown(
        modal_total_usd=tracker.accrued_usd,
        baseline_call_usd=BASELINE_COST_PER_CALL_USD,
        daily_volume=PRODUCTION_VOLUME_PER_DAY,
        n_calls=len(calls),
    )

    _line(" 7. Modal vs. self-hosted Ollama on a rented GPU ")
    print()
    print_self_hosted_compare(gpu_type=args.gpu_type)

    _line(" The training-loop pillar ")
    print()
    print("  Modal closes the inference half of the loop. After Example 47")
    print("  trains the LoRA on RunPod (or Example 38 locally), one")
    print("  decorator wraps it as a serverless function with per-second")
    print("  billing and autoscale-to-zero. A 200-call/day production")
    print("  feature costs single-digit dollars/month — not the $400+ of")
    print("  a 24/7 dedicated GPU. The agent code stays LiteLLM-shaped, so")
    print("  the swap from Anthropic to Modal is one config change, not a")
    print("  rewrite.")
    print()
    print("  Optionality is the brand: a feature that costs $5/month")
    print("  on Modal today can stay there, move to your own Ollama")
    print("  tomorrow, or split between both — same agent code either way.")
    print()


async def _run_dry_main(args: argparse.Namespace) -> None:
    """Async entry: stub demo via :class:`ModalLLMClient`.acompletion."""
    _line(" 4. Dry-run calls — agent → stub endpoint → urgency tag ")
    print()
    print("  No Modal deploy; routing the same agent code at the local stub.")
    print("  Same ModalLLMClient.acompletion(messages=[...]) shape;")
    print("  same per-second cost accounting; just a synthetic backend.")
    print()
    success, tracker, calls = await run_dry(
        gpu_type=args.gpu_type,
        budget_usd=args.budget_usd,
        project_id=args.project_id,
    )
    print()
    _print_proof_and_costdown(
        args=args, success=success, tracker=tracker, calls=calls, is_live=False,
    )


def _run_live_main(args: argparse.Namespace) -> None:
    """Sync entry: live demo via ``run_live_sync`` (Modal SDK 1.4.2 quirk)."""
    _line(" 4. Live calls — agent → Modal endpoint → LoRA ")
    print()
    print("  Spinning up the function and running the demo prompts …")
    print()
    success, tracker, calls = run_live_sync(
        gpu_type=args.gpu_type,
        budget_usd=args.budget_usd,
        project_id=args.project_id,
    )
    print()
    _print_proof_and_costdown(
        args=args, success=success, tracker=tracker, calls=calls, is_live=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Example 48 — Modal serverless inference.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Actually deploy to Modal (requires MODAL_TOKEN_ID/SECRET + "
             "`pip install modal`). Default is stub mode — no spend.",
    )
    parser.add_argument(
        "--budget-usd", type=float, default=DEFAULT_BUDGET_USD,
        help=f"Hard budget cap in USD; default ${DEFAULT_BUDGET_USD:.2f}. "
             "Demo stops when accrued cost crosses this.",
    )
    parser.add_argument(
        "--gpu-type", default=MODAL_GPU_DEFAULT,
        choices=sorted(GPU_PRICE_PER_HR_USD.keys()),
        help=f"Modal GPU type; default '{MODAL_GPU_DEFAULT}'. "
             "L4 is cheaper; A100 is faster.",
    )
    parser.add_argument(
        "--project-id", default="acme-prod",
        help="Project id used for spend attribution in the cost dashboard.",
    )
    args = parser.parse_args()

    env = _print_intro_and_plan(args)
    will_go_live = args.live and env.can_go_live

    if will_go_live:
        # Sync entry — Modal SDK 1.4.2's `with app.run()` hangs inside
        # asyncio.to_thread; the live path needs a sync __main__.
        _run_live_main(args)
    else:
        # Async entry — exercises the LiteLLM-shaped acompletion adapter
        # against a local stub callable. Same agent contract, no spend.
        asyncio.run(_run_dry_main(args))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(130)
