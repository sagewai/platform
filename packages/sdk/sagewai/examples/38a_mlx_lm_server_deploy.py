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
"""Example 38a — Mac-native deploy via ``mlx_lm.server`` (zero llama.cpp).

Sister example to ``38_unsloth_finetune.py`` — the ``a`` suffix marks
the alternative deploy path for the same training loop. Same Curator
→ FineTuneExecutor → adapter pipeline, but serves via mlx-lm's own
HTTP server instead of GGUF + Ollama. Eliminates the llama.cpp
dependency entirely on Apple Silicon.

When to pick which:

- **Example 38 (Ollama + GGUF)** — your team already runs Ollama; you
  want a portable Modelfile + GGUF artefact you can move to a Linux
  box; you care about k-quants for disk-size reasons. Needs
  ``LLAMA_CPP_DIR`` for the double-hop bridge.
- **Example 38a (mlx_lm.server, this file)** — you're deploying on
  Apple Silicon and want an OpenAI-compat HTTP endpoint with zero
  external bridge tools. The server is an mlx-lm console script;
  ``pip install sagewai mlx-tune`` is the entire dependency chain.

What's exercised:

- :class:`sagewai.autopilot.curator.Curator` — sample collection +
  fine-tune-job emission (same as Example 38)
- :class:`sagewai.autopilot.curator.FineTuneExecutor` — backend
  dispatch + LoRA training via mlx-tune
- ``mlx_lm.server`` — Mac-native HTTP serving with native MLX inference
  on the saved adapter. OpenAI-compatible
  ``/v1/chat/completions`` endpoint
- Same held-out 8-sample eval as Example 38, this time through
  the HTTP server instead of Ollama

Why no Docker on Mac:

The whole point of MLX is Apple's Metal GPU. Docker on Mac runs
inside a Linux VM that does **not** get Metal access — your fp16 3B
inference would silently drop to CPU and run 20× slower. The right
"managed service" pattern on Mac is launchd / brew services / a Tauri
desktop wrapper, not Docker. On Linux + CUDA you'd containerise
Unsloth deployments instead; that's a different example.

Requirements::

    pip install sagewai mlx-tune
    # Optional cloud baseline:
    export ANTHROPIC_API_KEY=...

Usage::

    python 38a_mlx_lm_server_deploy.py
    # Force backend or override base model (same env vars as Example 38):
    SAGEWAI_FT_BACKEND=mlx_tune python 38a_mlx_lm_server_deploy.py
    SAGEWAI_FT_MODEL=mlx-community/Llama-3.2-1B-Instruct \\
      python 38a_mlx_lm_server_deploy.py
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import socket
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sagewai.autopilot.agent_graph import Agent, AgentGraph
from sagewai.autopilot.blueprint import Blueprint
from sagewai.autopilot.controller.types import MissionRunResult, StepResult
from sagewai.autopilot.curator import (
    Curator,
    CuratorConfig,
    FineTuneConfig,
    FineTuneExecutor,
    FineTuneResult,
    TrainingDataset,
)
from sagewai.autopilot.models import (
    EvalRef,
    LearningLoopConfig,
    Metric,
    TrainingHook,
)

# ── Knobs (mostly mirroring Example 38) ───────────────────────────


BASE_MODEL_BY_BACKEND: dict[str, str] = {
    "unsloth": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",
    "mlx_tune": "mlx-community/Llama-3.2-3B-Instruct",
}
DEFAULT_BASE_MODEL: str = BASE_MODEL_BY_BACKEND["mlx_tune"]
PROJECT_ID: str = "acme-prod"
BASELINE_COST_PER_CALL_USD: float = 0.005

# Reuse Example 38's training samples + held-out eval set so the two
# examples are directly comparable. We re-define them here to keep the
# example self-contained (the conventions doc favours that over
# cross-example imports).
SYNTHETIC_RUNS: list[tuple[dict[str, Any], dict[str, Any]]] = [
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Cannot log in\n\nMy account is locked. Deadline at 5pm.",
            "output": '{"urgency": "high", "reason": "account-lockout-deadline"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Feature request\n\nWould love a dark-mode option. No rush.",
            "output": '{"urgency": "low", "reason": "feature-request"}',
        },
        {"user_rating": 4, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Billing dispute\n\nYou charged me twice for May.",
            "output": '{"urgency": "high", "reason": "billing-dispute"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Slack connector\n\nDoes it support threaded replies?",
            "output": '{"urgency": "medium", "reason": "integration-question"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Production outage\n\n/checkout returns 500 since 14:02 UTC.",
            "output": '{"urgency": "high", "reason": "production-outage"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Thanks!\n\nNew dashboard is great.",
            "output": '{"urgency": "low", "reason": "thank-you-note"}',
        },
        {"user_rating": 4, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Renewal\n\nWe grew to 35 seats this quarter.",
            "output": '{"urgency": "medium", "reason": "renewal-question"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: SSO outage\n\nNo one in our org can sign in.",
            "output": '{"urgency": "high", "reason": "auth-outage"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: spam\n\nbuy now click here",
            "output": '{"urgency": "low", "reason": "spam"}',
        },
        {"user_rating": 2, "human_override": True},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: half-finished\n\nUmmm",
            "output": '{"urgency": "high", "reason": "test"}',
        },
        {"user_rating": 3, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: MFA token\n\nDoesn't work. Presentation in an hour.",
            "output": '{"urgency": "high", "reason": "mfa-deadline"}',
        },
        {"user_rating": 5, "human_override": False},
    ),
    (
        {
            "instruction": "Classify the urgency of this customer-support email.",
            "input": "Subject: Doc typo\n\nAPI docs say `/v2/users/get` but actually `/find`.",
            "output": '{"urgency": "low", "reason": "documentation-feedback"}',
        },
        {"user_rating": 4, "human_override": False},
    ),
]

EVAL_SAMPLES: list[dict[str, str]] = [
    {
        "subject": "Database migration rolled back",
        "body": "Our prod migration rolled back at 03:14. Customers seeing stale data.",
        "expected": "high",
    },
    {
        "subject": "Sales question",
        "body": "Does enterprise plan include SOC2 reports out of the box?",
        "expected": "medium",
    },
    {
        "subject": "Loving the new release",
        "body": "v0.9 is excellent. Audit log view is exactly what we needed.",
        "expected": "low",
    },
    {
        "subject": "Webhook deliveries failing",
        "body": "429s on every webhook for 20 minutes. Pipeline backing up.",
        "expected": "high",
    },
    {
        "subject": "Feature request: keyboard shortcuts",
        "body": "Would be lovely to have 'g d' jump to dashboard. No rush.",
        "expected": "low",
    },
    {
        "subject": "Connector: HubSpot",
        "body": "Does HubSpot connector handle custom properties on contacts?",
        "expected": "medium",
    },
    {
        "subject": "URGENT: data leak risk",
        "body": "Production API keys visible in audit log. Possible data leak.",
        "expected": "high",
    },
    {
        "subject": "Quick price question",
        "body": "How much for Pro plan annual instead of monthly?",
        "expected": "medium",
    },
]


# ── Helpers ───────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * max(1, 68 - len(text))}")


def _build_blueprint(base_model: str) -> Blueprint:
    return Blueprint(
        id="email-triage-v1",
        version="1.0.0",
        title="Email Triage",
        description="Classifies incoming emails into urgency tiers.",
        category="customer-success",
        mode="event_driven",
        example_goals=("Triage incoming customer-support emails by urgency",),
        required_slots={},
        optional_slots={},
        providers_required=(),
        agent_graph=AgentGraph(
            nodes=(
                Agent(
                    id="classifier",
                    kind="llm",
                    role="classifier",
                    prompt_ref="email_triage.classifier.v1",
                    tools=(),
                    output_schema_ref=None,
                    max_steps=1,
                    deterministic_fallback=False,
                ),
            ),
            edges=(),
            branches={},
            entry="classifier",
        ),
        success_criteria=EvalRef(
            dataset_id="email-triage-eval-v1",
            metrics=(Metric(name="accuracy", op=">=", value=0.85),),
        ),
        training_data_hooks=(
            TrainingHook(
                event="classifier.completed",
                dataset="email-triage-{project_id}",
                format="alpaca",
                quality_filter="user_rating >= 4 AND human_override == False",
            ),
        ),
        learning_loop_target=LearningLoopConfig(
            trigger_after_labeled_samples=8,
            base_model=base_model,
            eval_gate_dataset_id="email-triage-eval-v1",
            promotion_criteria="accuracy >= 0.80 AND cost_per_call <= 0.001",
            fine_tune_method="unsloth",
            deploy_as="mlx_lm_server",
        ),
    )


def _synthetic_run(mission_id: str, alpaca_output: str) -> MissionRunResult:
    return MissionRunResult(
        mission_id=mission_id,
        status="completed",
        steps=(
            StepResult(
                node_id="classifier",
                status="completed",
                output=alpaca_output,
                output_preview=alpaca_output[:200],
                model_used="claude-haiku-4-5-20251001",
            ),
        ),
        duration_seconds=1.4,
    )


def _feed_curator(curator: Curator, blueprint: Blueprint) -> tuple[int, int]:
    accepted = 0
    rejected = 0
    for idx, (alpaca_sample, ctx) in enumerate(SYNTHETIC_RUNS):
        run = _synthetic_run(
            mission_id=f"m-{idx + 1:03d}",
            alpaca_output=alpaca_sample["output"],
        )
        ctx_full: dict[str, Any] = {"project_id": PROJECT_ID, **ctx}
        added = curator.process(run, blueprint, ctx_full)
        if added:
            ds_id = added[0]
            ds = curator.datasets[ds_id]
            patched = list(ds.samples)
            patched[-1] = alpaca_sample
            curator.datasets[ds_id] = ds.model_copy(update={"samples": patched})
            accepted += 1
        else:
            rejected += 1
    return accepted, rejected


def _check_environment() -> dict[str, Any]:
    env: dict[str, Any] = {
        "has_unsloth": False,
        "has_mlx_tune": False,
        "has_cuda": False,
        "has_anthropic_key": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "mlx_lm_server_bin": shutil.which("mlx_lm.server"),
    }
    try:
        import unsloth  # noqa: F401

        env["has_unsloth"] = True
    except ImportError:
        pass
    try:
        import mlx_tune  # noqa: F401

        env["has_mlx_tune"] = True
    except ImportError:
        pass
    try:
        import torch

        env["has_cuda"] = bool(torch.cuda.is_available())
    except ImportError:
        pass
    return env


def _resolve_backend(env: dict[str, Any]) -> str | None:
    requested = os.environ.get("SAGEWAI_FT_BACKEND", "auto").lower().strip()
    if requested == "unsloth":
        return "unsloth" if env["has_unsloth"] and env["has_cuda"] else None
    if requested == "mlx_tune":
        return "mlx_tune" if env["has_mlx_tune"] else None
    if env["has_unsloth"] and env["has_cuda"]:
        return "unsloth"
    if env["has_mlx_tune"]:
        return "mlx_tune"
    return None


def _free_port() -> int:
    """Pick an unused TCP port for the server."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@contextlib.contextmanager
def _spawn_mlx_lm_server(
    *, base_model: str, adapter_path: str, port: int,
) -> Any:
    """Start ``mlx_lm.server`` as a subprocess, yield once it's reachable.

    The server provides an OpenAI-compatible HTTP API at
    ``/v1/chat/completions``. We probe ``GET /v1/models`` until it
    responds (typically ~5-10s for a 3B fp16 model on M-series).

    Args:
        base_model: HF repo or local path to the base.
        adapter_path: Directory containing the saved LoRA adapter.
        port: Local TCP port to bind.

    Yields:
        The subprocess.Popen handle while the server is alive.
    """
    cmd = [
        "mlx_lm.server",
        "--model", base_model,
        "--adapter-path", adapter_path,
        "--host", "127.0.0.1",
        "--port", str(port),
        "--log-level", "WARNING",
    ]
    print(f"  spawning: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 60
        last_err: str | None = None
        while time.time() < deadline:
            if proc.poll() is not None:
                tail = (proc.stdout.read() if proc.stdout else "")[-400:]
                raise RuntimeError(
                    f"mlx_lm.server exited early ({proc.returncode}): {tail}",
                )
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/v1/models", timeout=2,
                ) as resp:
                    if resp.status == 200:
                        print(f"  server ready on http://127.0.0.1:{port}")
                        break
            except (urllib.error.URLError, OSError) as exc:
                last_err = str(exc)
                time.sleep(1)
        else:
            raise RuntimeError(
                f"mlx_lm.server did not become ready in 60s; last probe: {last_err}",
            )
        yield proc
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


_URGENCY_RE = re.compile(r'"urgency"\s*:\s*"(low|medium|high)"', re.IGNORECASE)


def _parse_urgency(text: str) -> str | None:
    m = _URGENCY_RE.search(text)
    return m.group(1).lower() if m else None


async def _classify_via_mlx_server(
    *, port: int, model_id: str, subject: str, body: str,
) -> tuple[str | None, float]:
    """Call the local mlx_lm.server's ``/v1/chat/completions`` endpoint."""
    payload = json.dumps({
        "model": model_id,
        "messages": [
            {"role": "system", "content":
                'Classify the urgency of customer-support emails. '
                'Respond with JSON only: '
                '{"urgency": "low|medium|high", "reason": "..."}'},
            {"role": "user", "content": f"Subject: {subject}\n\n{body}"},
        ],
        "max_tokens": 60,
        # Deterministic for the demo — set higher only when you want
        # sampling diversity. Matches Example 38's eval temp.
        "temperature": 0.0,
    }).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        print(f"    [mlx_lm.server call failed: {type(exc).__name__}: {exc}]")
        return (None, 0.0)
    text = data["choices"][0]["message"]["content"].strip()
    return (_parse_urgency(text), 0.0)


async def _classify_with_cloud(
    subject: str, body: str,
) -> tuple[str | None, float]:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return (None, 0.0)
    try:
        from litellm import acompletion
    except ImportError:
        return (None, 0.0)
    response = await acompletion(
        model="anthropic/claude-haiku-4-5-20251001",
        messages=[
            {"role": "system", "content":
                'Classify the urgency of customer-support emails. '
                'Respond with JSON only: '
                '{"urgency": "low|medium|high", "reason": "..."}'},
            {"role": "user", "content": f"Subject: {subject}\n\n{body}"},
        ],
        max_tokens=60,
        temperature=0.0,
    )
    text = response["choices"][0]["message"]["content"].strip()
    return (_parse_urgency(text), BASELINE_COST_PER_CALL_USD)


# ── main ──────────────────────────────────────────────────────────


async def main() -> None:
    _line()
    print(" Sagewai — Mac-native deploy via mlx_lm.server (example 38a)")
    _line()
    print()

    # 1. Probe runtime + select backend
    _line(" 1. Runtime + backend ")
    print()
    env = _check_environment()
    for key in (
        "has_unsloth", "has_mlx_tune", "has_cuda",
        "has_anthropic_key", "mlx_lm_server_bin",
    ):
        flag = "✓" if env[key] else "✗"
        print(f"    {flag} {key:<22} = {env[key]}")
    backend = _resolve_backend(env)
    base_model = (
        os.environ.get("SAGEWAI_FT_MODEL")
        or BASE_MODEL_BY_BACKEND.get(backend or "mlx_tune", DEFAULT_BASE_MODEL)
    )
    print()
    print(f"  selected backend = {backend or '(none — fallback recipe)'}")
    print(f"  base model       = {base_model}")
    print()
    if not env["mlx_lm_server_bin"]:
        print("  Note: mlx_lm.server is not on PATH. Install with")
        print("      pip install mlx-lm")
        print("  (mlx-tune already pulls it in as a dep.) Without it the")
        print("  deploy + eval sections fall back to a recipe-only print.")
        print()

    # 2. Curator builds the dataset
    _line(" 2. Curator builds the dataset ")
    print()
    blueprint = _build_blueprint(base_model)
    curator = Curator(config=CuratorConfig())
    accepted, rejected = _feed_curator(curator, blueprint)
    dataset_id = f"email-triage-{PROJECT_ID}"
    dataset: TrainingDataset = curator.datasets[dataset_id]
    print(f"  dataset_id        = {dataset.dataset_id}")
    print(f"  accepted samples  = {accepted}")
    print(f"  rejected (filter) = {rejected}")
    pending_jobs = curator.clear_pending_jobs()
    if not pending_jobs:
        print("  [warn] no FineTuneJob emitted")
        return
    job = pending_jobs[0]
    print(f"  FineTuneJob       = {job.job_id}")
    print()

    # 3. Train (no GGUF — just adapter)
    _line(" 3. Train via mlx-tune (or skip recipe) ")
    print()
    fine_tune_result: FineTuneResult | None = None
    work_dir: Path | None = None
    if backend is not None:
        work_dir = Path(tempfile.mkdtemp(prefix="sagewai-ft-39-"))
        executor = FineTuneExecutor(
            config=FineTuneConfig(
                output_dir=str(work_dir),
                lora_r=16,
                lora_alpha=32,
                epochs=1,
                batch_size=2,
                learning_rate=2e-4,
                backend=backend,  # type: ignore[arg-type]
                produce_gguf=False,  # ← deliberately off; we don't need GGUF
            ),
        )
        print(f"  Calling FineTuneExecutor (backend={backend}, no GGUF) …")
        fine_tune_result = await asyncio.to_thread(executor.execute, job, dataset)
        print(f"  status     = {fine_tune_result.status}")
        if fine_tune_result.status == "completed":
            print(f"  model_path = {fine_tune_result.model_path}")
    else:
        print("  Skipping the live fine-tune — no backend installed.")
        print("    pip install mlx-tune trl peft datasets")
        print("  Section 4 will show the deploy command for reference.")
        print()

    # 4. Deploy via mlx_lm.server (no GGUF, no Ollama, no llama.cpp)
    _line(" 4. Deploy via mlx_lm.server ")
    print()
    server_port: int | None = None
    server_ctx = None
    adapter_dir: Path | None = None
    if (
        fine_tune_result is not None
        and fine_tune_result.status == "completed"
        and fine_tune_result.model_path
        and env["mlx_lm_server_bin"]
    ):
        adapter_dir = Path(fine_tune_result.model_path) / "adapters"
        if not (adapter_dir / "adapters.safetensors").exists():
            print(f"  Adapter not found at {adapter_dir}; skipping deploy.")
        else:
            server_port = _free_port()
            try:
                server_ctx = _spawn_mlx_lm_server(
                    base_model=base_model,
                    adapter_path=str(adapter_dir),
                    port=server_port,
                )
                server_ctx.__enter__()
                print(f"  Serving on http://127.0.0.1:{server_port}/v1")
                print("  (OpenAI-compatible — same interface as the cloud API)")
            except Exception as exc:  # noqa: BLE001
                print(f"  Spawn failed: {type(exc).__name__}: {exc}")
                server_ctx = None
                server_port = None
    elif fine_tune_result is None or fine_tune_result.status != "completed":
        print("  No trained adapter to serve.")
    else:
        print("  mlx_lm.server is not installed. Deploy command for reference:")
        print("    mlx_lm.server \\")
        print(f"      --model {base_model} \\")
        print(f"      --adapter-path {fine_tune_result.model_path}/adapters \\")
        print("      --host 127.0.0.1 --port 8080")
    print()

    # 5. Held-out eval — through the HTTP server
    _line(" 5. Held-out eval — cloud baseline vs mlx_lm.server ")
    print()
    print(f"  Eval set: {len(EVAL_SAMPLES)} held-out emails (never trained on)")
    print()
    eval_results: list[dict[str, Any]] = []
    cloud_correct = local_correct = cloud_calls = local_calls = 0
    print(f"  {'#':>2}  {'expected':>8}  {'cloud':>8}  {'local':>8}  subject")
    print(f"  {'-'*2}  {'-'*8}  {'-'*8}  {'-'*8}  {'-'*40}")
    try:
        for i, ex in enumerate(EVAL_SAMPLES, start=1):
            cloud_label, _ = await _classify_with_cloud(ex["subject"], ex["body"])
            if cloud_label is not None:
                cloud_calls += 1
                if cloud_label == ex["expected"]:
                    cloud_correct += 1
            local_label: str | None = None
            if server_port is not None:
                local_label, _ = await _classify_via_mlx_server(
                    port=server_port,
                    model_id=base_model,
                    subject=ex["subject"],
                    body=ex["body"],
                )
            if local_label is not None:
                local_calls += 1
                if local_label == ex["expected"]:
                    local_correct += 1
            eval_results.append({
                "subject": ex["subject"],
                "body": ex["body"],
                "expected": ex["expected"],
                "predicted": local_label,
                "correct": local_label is not None and local_label == ex["expected"],
            })
            print(
                f"  {i:>2}  {ex['expected']:>8}  "
                f"{(cloud_label or '—'):>8}  "
                f"{(local_label or '—'):>8}  "
                f"{ex['subject'][:40]}",
            )
    finally:
        if server_ctx is not None:
            try:
                server_ctx.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
    print()

    cloud_acc = (cloud_correct / cloud_calls) if cloud_calls else 1.00
    local_acc = (local_correct / local_calls) if local_calls else 0.0
    print(f"  Cloud baseline      : {cloud_correct}/{cloud_calls or '—'}  "
          f"= {cloud_acc:.1%}")
    print(f"  mlx_lm.server local : {local_correct}/{local_calls or '—'}  "
          f"= {local_acc:.1%}")
    print()

    # 6. Cost-down
    _line(" 6. The cost-down number ")
    print()
    cloud_per_1000 = BASELINE_COST_PER_CALL_USD * 1000
    print(f"  Cloud (Anthropic Haiku):    ${BASELINE_COST_PER_CALL_USD:.6f}/call  "
          f"→ ${cloud_per_1000:>7.2f}/1k calls")
    print("  mlx_lm.server (local):      $0.000000/call  → $   0.00/1k calls")
    print(f"  Cost-down delta:            ${cloud_per_1000:>7.2f}/1k calls")
    print()
    print("  Same delta as Example 38; different deploy plumbing. The")
    print("  audience-pin person picks: Ollama Modelfile (Example 38, needs")
    print("  llama.cpp) vs mlx_lm.server (this file, zero external deps).")
    print()

    # 7. Loop closes — same Section 7 idea as Example 38
    _line(" 7. Cycle 2 — production runs feed the next fine-tune ")
    print()
    cycle1_size = dataset.sample_count
    if any(r["predicted"] is not None for r in eval_results):
        accepted_2 = rejected_2 = 0
        for idx, r in enumerate(eval_results, start=1):
            if r["predicted"] is None:
                continue
            alpaca_sample = {
                "instruction": "Classify the urgency of this customer-support email.",
                "input": f"Subject: {r['subject']}\n\n{r['body']}",
                "output": (
                    f'{{"urgency": "{r["predicted"]}", '
                    f'"reason": "production-run-cycle-2"}}'
                ),
            }
            ctx_full = {
                "project_id": PROJECT_ID,
                "user_rating": 5 if r["correct"] else 2,
                "human_override": not r["correct"],
            }
            run = _synthetic_run(
                mission_id=f"cycle2-{idx:03d}",
                alpaca_output=alpaca_sample["output"],
            )
            added = curator.process(run, blueprint, ctx_full)
            if added:
                ds_id_now = added[0]
                ds_now = curator.datasets[ds_id_now]
                patched = list(ds_now.samples)
                patched[-1] = alpaca_sample
                curator.datasets[ds_id_now] = ds_now.model_copy(
                    update={"samples": patched},
                )
                accepted_2 += 1
            else:
                rejected_2 += 1
        curator._last_job_threshold_hit[dataset_id] = 0  # noqa: SLF001
        curator._maybe_enqueue_job(  # noqa: SLF001
            dataset_id=dataset_id,
            project_id=PROJECT_ID,
            loop_config=blueprint.learning_loop_target,
        )
        cycle2_jobs = curator.clear_pending_jobs()
        dataset_after = curator.datasets[dataset_id]
        print(f"  Cycle 1 dataset   : {cycle1_size} samples")
        print(f"  Cycle 2 captures  : {accepted_2} accepted, "
              f"{rejected_2} filtered")
        print(f"  Cycle 2 dataset   : {dataset_after.sample_count} "
              f"(+{dataset_after.sample_count - cycle1_size})")
        if cycle2_jobs:
            print()
            print(f"  ▶ FineTuneJob queued for cycle 2: {cycle2_jobs[0].job_id}")
            print("    Same executor + blueprint, larger and more")
            print("    domain-specific dataset. Re-run this script and the")
            print("    next cycle trains on real production decisions.")
    else:
        print("  No live local predictions captured — install mlx_lm.server")
        print("  to see cycle 2 wire up.")
    print()

    _line(" The training-loop pillar (no llama.cpp edition) ")
    print()
    print("  Same Curator + FineTuneExecutor + Sagewai surface as Example 38.")
    print("  Different deploy: mlx_lm.server's OpenAI-compatible HTTP endpoint")
    print("  serves the LoRA-adapted model directly from MLX. No GGUF, no")
    print("  Ollama, no llama.cpp, no Docker. The cost-down delta is the")
    print("  same; the deploy story is friction-free for Mac-native stacks.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
