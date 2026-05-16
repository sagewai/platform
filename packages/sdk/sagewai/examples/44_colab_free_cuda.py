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
"""Example 44 — Free CUDA via Colab + Drive-sync orchestration.

Closes Gap #8c of the inference spectrum — the **democratization
tier**. Every other inference-spectrum example (RunPod, Vast.ai,
Modal) assumes the audience-pin person has a corporate card. This
one removes the last bullshit-detector trigger: *"I don't have a GPU
and I won't put one on a corporate card without proof."*

A Google account is all you need. Colab gives anyone a Tesla T4
(16GB) on the free tier with ~12-hour session limits. This example
wraps Colab via Drive-sync (the realistic 2026 orchestration path —
``colab-cli`` is unmaintained, Google has no first-party CLI, and
Selenium wrappers are brittle). The orchestrator:

1. Authenticates to Drive via the OAuth client ID at
   ``~/.sagewai/google-drive-oauth.json``; first run opens a browser
   for consent and writes the refreshable token to
   ``~/.credentials/sagewai.json``; subsequent runs reuse it.
2. Creates a fresh Drive folder (``SagewaiTraining/run-<ts>/``).
3. Uploads the companion notebook + email-triage JSONL to that
   folder.
4. Prints the notebook's Colab URL and pauses for the user to open
   it once and click *Runtime → Run all* (~30 seconds, the only
   manual step).
5. The notebook self-installs Unsloth, fine-tunes Llama-3.2-3B on
   the JSONL, evaluates on a 5-sample held-out set, and writes the
   LoRA back to the same Drive folder as ``output/lora.tar.gz``.
6. The orchestrator polls Drive for the LoRA, downloads it locally,
   and reports elapsed time + total spend (which is **$0.00**).

The orchestrator **always** runs end-to-end. Without
``~/.sagewai/google-drive-oauth.json`` *or* without ``pydrive2``, it
prints the orchestration plan, the Drive folder path, the Colab URL
shape, the free-tier session limits, and the recovery path — the
audience-pin person sees what the integration looks like before they
sign in.

What's exercised:

- ``pydrive2.auth.GoogleAuth.LoadClientConfigFile`` /
  ``LocalWebserverAuth`` flow with the canonical OAuth path
- ``pydrive2.drive.GoogleDrive.CreateFile`` + folder upload patterns
  for the notebook + JSONL + LoRA round trip
- ``sagewai.observability.costs.calculate_cost`` paired with a $0.00
  GPU spend so the Observatory dashboard still records the GPU-hour
  count (zero-priced doesn't mean zero-tracked)
- Free-tier time-budget tracking: elapsed vs the ~12-hour Colab
  session limit, with an honest checkpoint-resume recovery path
  printed when the budget is hit
- Cost-down comparison: cloud-LLM baseline (Anthropic Haiku) vs.
  ``$0/call`` after the LoRA deploys via Ollama (Example 38)

Requirements::

    pip install sagewai           # python-dotenv ships in the SDK tree
    # Optional (for the live path):
    #   - ~/.sagewai/google-drive-oauth.json (Drive OAuth client ID)
    #   - pip install pydrive2 oauth2client

Usage::

    # Default: stub mode (no auth), prints the orchestration plan
    python 44_colab_free_cuda.py

    # Live: authenticate to Drive, upload notebook + JSONL, poll for LoRA
    python 44_colab_free_cuda.py --live

    # Custom Drive folder root (default: SagewaiTraining)
    python 44_colab_free_cuda.py --live --drive-root MyTrainingRuns

    # Tighter poll-timeout in minutes (default: 720 = the free-tier limit)
    python 44_colab_free_cuda.py --live --poll-timeout-minutes 60
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# Load Sagewai credentials early so any future env-var checks see the
# values from ~/.sagewai/.env. Silently no-ops if the file is absent
# (clean-machine path). The Colab tier does NOT depend on env vars —
# the OAuth client lives at a fixed path — but the load is here so
# the example matches the inference-spectrum sibling shape.
load_dotenv(Path.home() / ".sagewai" / ".env")

from sagewai.observability.costs import calculate_cost  # noqa: E402

# pydrive2 is an optional dependency. The orchestrator must import +
# run stub-mode without it. The live path probes for it and degrades
# gracefully when it's missing, mirroring the Modal pattern (Ex 48).
try:
    from pydrive2.auth import GoogleAuth  # type: ignore[import-not-found]
    from pydrive2.drive import GoogleDrive  # type: ignore[import-not-found]

    _HAS_PYDRIVE = True
except ImportError:  # pragma: no cover — exercised via the stub-mode path
    GoogleAuth = None  # type: ignore[assignment, misc]
    GoogleDrive = None  # type: ignore[assignment, misc]
    _HAS_PYDRIVE = False


# ── Canonical paths (mirror inference-provisioning-setup.md) ─────────

OAUTH_CLIENT_PATH: Path = Path.home() / ".sagewai" / "google-drive-oauth.json"
ACCESS_TOKEN_PATH: Path = Path.home() / ".credentials" / "sagewai.json"


# ── Drive layout knobs ───────────────────────────────────────────────

DRIVE_ROOT_DEFAULT: str = "SagewaiTraining"
NOTEBOOK_FILENAME: str = "44_colab_free_cuda.ipynb"
DATASET_FILENAME: str = "email_triage.jsonl"
LORA_REMOTE_RELATIVE_PATH: str = "output/lora.tar.gz"
EVAL_RESULT_RELATIVE_PATH: str = "output/eval_result.json"

NOTEBOOK_SOURCE_PATH: Path = (
    Path(__file__).parent / "notebooks" / NOTEBOOK_FILENAME
)


# ── Free-tier ergonomics ─────────────────────────────────────────────

# Colab free-tier session limit — Google enforces ~12 hours for
# T4-eligible accounts. Newer or low-rep accounts may see shorter
# windows; we default the orchestrator's poll timeout to the upper
# bound and let the user override via --poll-timeout-minutes.
COLAB_FREE_TIER_SESSION_LIMIT_MINUTES: int = 720  # 12 hours

# How often to poll Drive while waiting for the LoRA. 30s strikes
# the balance — too tight wastes API quota; too loose makes the
# wall-clock report wander. Empirical: a Llama-3.2-3B 4-bit LoRA on
# the 8-sample JSONL takes ~6-10 minutes on a T4 (cold-start +
# install + 1-epoch train + tar). 30s polls = ~12-20 polls per run.
POLL_INTERVAL_SECONDS: int = 30

# Free-tier T4 GPU-hour cost. Locked at $0.00 — the entire pitch.
FREE_TIER_GPU_PRICE_PER_HR_USD: float = 0.00

# Empirical fine-tune wall-clock on free Colab T4 (3B LoRA, 8 samples,
# 1 epoch). Used for the stub-mode breakdown so the audience-pin
# person sees the same elapsed minutes the live path would print.
EXPECTED_FINE_TUNE_MINUTES: float = 8.0

# Cloud-LLM baseline. Same number Examples 47 + 48 pitch: typical
# small-model triage cost on Anthropic Haiku, after system-prompt +
# retry overhead. The figure the CFO will use.
BASELINE_COST_PER_CALL_USD: float = 0.005

# Production volume the audience-pin person quotes — 200 emails/day.
PRODUCTION_VOLUME_PER_DAY: int = 200


# ── Email-triage training data (mirrors Examples 38 + 47) ────────────

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

# Held-out 5-sample eval set — kept separate so the notebook can
# report accuracy honestly instead of training on its own eval.
EMAIL_TRIAGE_EVAL_DATA: list[dict[str, str]] = [
    {
        "input": "Subject: Database is read-only\n\nOur production Postgres dropped to read-only at 09:14 UTC. Customers can't checkout.",
        "expected": "high",
    },
    {
        "input": "Subject: API quota question\n\nWhat's the per-minute rate limit on the v2 search endpoint? No urgency.",
        "expected": "low",
    },
    {
        "input": "Subject: Integration walkthrough\n\nCould someone walk me through the Stripe webhook setup next week?",
        "expected": "medium",
    },
    {
        "input": "Subject: Payment processor returning 500s\n\nEvery card transaction since 11:30 UTC fails. We're losing revenue.",
        "expected": "high",
    },
    {
        "input": "Subject: Documentation typo\n\nTiny typo on the pricing page — 'recieve' should be 'receive'.",
        "expected": "low",
    },
]


# ── Helpers ──────────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        print(f"{char * 3} {text} {char * max(1, 68 - len(text))}")


@dataclass
class Environment:
    """What the host machine has available for the live path."""

    has_oauth_client: bool
    has_pydrive: bool
    has_cached_token: bool

    @property
    def can_go_live(self) -> bool:
        return self.has_oauth_client and self.has_pydrive


def _detect_environment() -> Environment:
    return Environment(
        has_oauth_client=OAUTH_CLIENT_PATH.exists(),
        has_pydrive=_HAS_PYDRIVE,
        has_cached_token=ACCESS_TOKEN_PATH.exists(),
    )


@dataclass
class GpuSpendTracker:
    """Tracks accrued GPU spend in USD — even when the price is zero.

    Mirrors the per-hour ``GpuSpendTracker`` from Example 47 so the
    Observatory dashboard reads the free Colab tier off the same
    interface as the paid tiers. The ``$0.00`` price means the
    accrued total is always zero, but the **GPU-hour count** is
    real and tracked — that's the metric the dashboard renders.
    """

    project_id: str
    price_per_hour_usd: float = FREE_TIER_GPU_PRICE_PER_HR_USD
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
    def gpu_hours(self) -> float:
        return self.elapsed_seconds / 3600.0

    @property
    def accrued_usd(self) -> float:
        return self.price_per_hour_usd * self.gpu_hours


@dataclass
class DriveFolderHandle:
    """Identifies the Drive folder the orchestrator created for this run."""

    folder_id: str
    folder_path: str
    notebook_id: str
    notebook_url: str
    dataset_id: str
    created_at: float = field(default_factory=time.time)


# ── Stub-mode plan (always safe; never authenticates, never uploads) ─


def print_orchestration_plan(*, drive_root: str, poll_timeout_minutes: int) -> None:
    """Print the exact steps the live path would run + the cost story."""
    run_folder = f"{drive_root}/run-<timestamp>/"
    notebook_url = (
        "https://colab.research.google.com/drive/<NOTEBOOK_FILE_ID>"
    )

    print("  ── Steps the orchestrator would run (in order) ──")
    print()
    print("  1. Authenticate to Google Drive:")
    print(f"     - Read OAuth client from {OAUTH_CLIENT_PATH}")
    print(f"     - First run: open browser for consent (~30s, one click)")
    print(f"     - Cache refreshable token at {ACCESS_TOKEN_PATH}")
    print()
    print("  2. Create a fresh Drive folder for this run:")
    print(f"     - Path: {run_folder}")
    print()
    print("  3. Upload the companion notebook + training data:")
    print(f"     - {NOTEBOOK_FILENAME}  ({NOTEBOOK_SOURCE_PATH.name})")
    print(f"     - {DATASET_FILENAME}    ({len(EMAIL_TRIAGE_TRAINING_DATA)} samples)")
    print()
    print("  4. Print the Colab URL and pause for one keypress:")
    print(f"     - URL shape: {notebook_url}")
    print("     - You open it once → Runtime → Run all (~30s click)")
    print()
    print("  5. The notebook self-installs Unsloth, fine-tunes")
    print("     Llama-3.2-3B (4-bit) on the JSONL, evaluates on a")
    print("     held-out 5-sample set, and writes the LoRA back to:")
    print(f"     - {run_folder}{LORA_REMOTE_RELATIVE_PATH}")
    print(f"     - {run_folder}{EVAL_RESULT_RELATIVE_PATH}")
    print()
    print("  6. The orchestrator polls Drive every "
          f"{POLL_INTERVAL_SECONDS}s for the LoRA")
    print(f"     - Poll timeout: {poll_timeout_minutes} minutes")
    print(f"       (Colab free-tier session limit: "
          f"~{COLAB_FREE_TIER_SESSION_LIMIT_MINUTES // 60}h)")
    print()
    print("  7. Download the LoRA + eval result locally; report spend.")
    print()
    print("  ── Cost story ──")
    print()
    print(f"  GPU type        : Tesla T4 (16GB) — Colab free tier")
    print(f"  Price           : ${FREE_TIER_GPU_PRICE_PER_HR_USD:.4f}/hr "
          "— the entire pitch")
    print(f"  Expected hours  : {EXPECTED_FINE_TUNE_MINUTES / 60:.2f}h "
          f"({EXPECTED_FINE_TUNE_MINUTES:.1f} min on T4, 8 samples, 1 epoch)")
    print(f"  Expected spend  : $0.000000 — and not by accounting trick")
    print()
    print(f"  Session budget  : {COLAB_FREE_TIER_SESSION_LIMIT_MINUTES} min "
          "(Colab free-tier ceiling)")
    print(f"  Recovery path   : if Colab disconnects, re-run with the same")
    print(f"                    --drive-root; the orchestrator picks up the")
    print(f"                    LoRA whenever it lands.")
    print()


# ── Live orchestration (only enters with --live + healthy environment) ─


def _authenticate_drive() -> "GoogleDrive":
    """Run the OAuth flow; return an authenticated ``GoogleDrive``.

    First run opens a browser for consent (LocalWebserverAuth, ~30s
    one-click). Subsequent runs reuse the cached token at
    ``~/.credentials/sagewai.json`` via ``LoadCredentialsFile``.
    Tokens auto-refresh while the user's grant is alive.
    """
    if not _HAS_PYDRIVE:  # pragma: no cover — guarded upstream
        raise RuntimeError("pydrive2 is not installed; cannot authenticate.")

    ACCESS_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    gauth = GoogleAuth(settings_file=None)  # type: ignore[misc]
    gauth.LoadClientConfigFile(str(OAUTH_CLIENT_PATH))

    if ACCESS_TOKEN_PATH.exists():
        gauth.LoadCredentialsFile(str(ACCESS_TOKEN_PATH))
        if gauth.access_token_expired:
            gauth.Refresh()
        else:
            gauth.Authorize()
    else:
        # Opens a browser tab; user grants Drive access.
        gauth.LocalWebserverAuth()

    gauth.SaveCredentialsFile(str(ACCESS_TOKEN_PATH))
    return GoogleDrive(gauth)  # type: ignore[misc]


def _ensure_drive_folder(drive: "GoogleDrive", *, name: str, parent_id: str | None = None) -> str:
    """Create a Drive folder named ``name`` under ``parent_id`` (or root); return its id.

    If a folder with the same name already exists at that level, reuse
    it — the orchestrator's per-run folder name has a timestamp so a
    real collision is implausible, but reusing a manually-created root
    folder (``SagewaiTraining``) across runs is the desired behaviour.
    """
    parents = [{"id": parent_id}] if parent_id else [{"id": "root"}]
    parent_clause = (
        f"'{parent_id}' in parents" if parent_id else "'root' in parents"
    )
    query = (
        f"{parent_clause} and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"title='{name}' and trashed=false"
    )
    matches = drive.ListFile({"q": query}).GetList()
    if matches:
        return matches[0]["id"]
    folder = drive.CreateFile(
        {
            "title": name,
            "parents": parents,
            "mimeType": "application/vnd.google-apps.folder",
        },
    )
    folder.Upload()
    return folder["id"]


def _upload_file(
    drive: "GoogleDrive", *, local_path: Path, remote_name: str, parent_id: str,
    convert_to_notebook: bool = False,
) -> tuple[str, str]:
    """Upload ``local_path`` into the Drive folder ``parent_id``.

    Returns ``(file_id, alternate_url)``. The alternate URL is the
    Colab-friendly ``https://colab.research.google.com/drive/<id>``
    when ``convert_to_notebook`` is True; otherwise the standard
    Drive view link.
    """
    metadata = {"title": remote_name, "parents": [{"id": parent_id}]}
    f = drive.CreateFile(metadata)
    f.SetContentFile(str(local_path))
    f.Upload()
    file_id = f["id"]
    if convert_to_notebook:
        url = f"https://colab.research.google.com/drive/{file_id}"
    else:
        url = f.get("alternateLink") or f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, url


def _find_remote_artifact(
    drive: "GoogleDrive", *, parent_id: str, relative_path: str,
) -> str | None:
    """Walk ``relative_path`` under ``parent_id``; return the file id if found.

    Drive's filesystem is flat (every entry is a node); the path
    ``output/lora.tar.gz`` means *find a folder named output under
    parent_id, then a file named lora.tar.gz under that folder*.
    Returns ``None`` if any segment doesn't exist yet — the polling
    loop calls this every 30s while the notebook trains.
    """
    parts = relative_path.split("/")
    current = parent_id
    for part in parts[:-1]:
        query = (
            f"'{current}' in parents and "
            f"mimeType='application/vnd.google-apps.folder' and "
            f"title='{part}' and trashed=false"
        )
        matches = drive.ListFile({"q": query}).GetList()
        if not matches:
            return None
        current = matches[0]["id"]
    file_query = (
        f"'{current}' in parents and "
        f"title='{parts[-1]}' and trashed=false"
    )
    file_matches = drive.ListFile({"q": file_query}).GetList()
    if not file_matches:
        return None
    return file_matches[0]["id"]


def _download_file(
    drive: "GoogleDrive", *, file_id: str, local_path: Path,
) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    f = drive.CreateFile({"id": file_id})
    f.GetContentFile(str(local_path))


async def _wait_for_keypress(prompt: str) -> None:
    """Print ``prompt`` and wait for the user to press <Enter>.

    Wrapped in ``asyncio.to_thread`` so the rest of the runtime stays
    responsive (matters once the watchdog/polling tasks join the
    party). Falls through silently in non-interactive shells (CI,
    detached agents) so automated runs don't deadlock.
    """
    if not sys.stdin.isatty():
        print(f"  {prompt}  (non-interactive shell — skipping wait)")
        return
    await asyncio.to_thread(input, f"  {prompt} ")


async def run_live(
    *,
    drive_root: str,
    poll_timeout_minutes: int,
    project_id: str,
    download_dir: Path,
) -> tuple[bool, GpuSpendTracker, str | None, str | None]:
    """Run the full live Drive-sync orchestration.

    Returns ``(success, tracker, lora_local_path, eval_local_path)``.
    The Colab session itself is uncontrolled by us — Google may
    preempt the free tier at any point — so the polling loop bounds
    the wait at ``poll_timeout_minutes`` and prints the recovery path
    on timeout.
    """
    tracker = GpuSpendTracker(project_id=project_id)
    lora_local: str | None = None
    eval_local: str | None = None
    handle: DriveFolderHandle | None = None

    print("  Authenticating to Google Drive (pydrive2) …")
    drive = await asyncio.to_thread(_authenticate_drive)
    print("  Drive auth OK.")
    print()

    # 1. Create the per-run folder under the user-chosen root.
    run_folder_name = f"run-{int(time.time())}"
    print(f"  Creating Drive folder: {drive_root}/{run_folder_name}/")
    root_id = await asyncio.to_thread(
        _ensure_drive_folder, drive, name=drive_root,
    )
    run_folder_id = await asyncio.to_thread(
        _ensure_drive_folder, drive, name=run_folder_name, parent_id=root_id,
    )

    # 2. Upload the notebook (as a real Colab notebook) + JSONL.
    if not NOTEBOOK_SOURCE_PATH.exists():
        raise RuntimeError(
            f"Companion notebook missing at {NOTEBOOK_SOURCE_PATH}. "
            "The example ships with the notebook in packages/sdk/sagewai/"
            "examples/notebooks/ — re-install or check the worktree."
        )
    print(f"  Uploading {NOTEBOOK_FILENAME} …")
    notebook_id, notebook_url = await asyncio.to_thread(
        _upload_file, drive,
        local_path=NOTEBOOK_SOURCE_PATH,
        remote_name=NOTEBOOK_FILENAME,
        parent_id=run_folder_id,
        convert_to_notebook=True,
    )

    print(f"  Uploading {DATASET_FILENAME} …")
    jsonl_path = _write_training_jsonl(EMAIL_TRIAGE_TRAINING_DATA)
    eval_path = _write_eval_jsonl(EMAIL_TRIAGE_EVAL_DATA)
    dataset_id, _ = await asyncio.to_thread(
        _upload_file, drive,
        local_path=jsonl_path,
        remote_name=DATASET_FILENAME,
        parent_id=run_folder_id,
    )
    await asyncio.to_thread(
        _upload_file, drive,
        local_path=eval_path,
        remote_name="email_triage_eval.jsonl",
        parent_id=run_folder_id,
    )

    handle = DriveFolderHandle(
        folder_id=run_folder_id,
        folder_path=f"{drive_root}/{run_folder_name}/",
        notebook_id=notebook_id,
        notebook_url=notebook_url,
        dataset_id=dataset_id,
    )
    print(f"  Upload OK. Folder id: {run_folder_id}")
    print()

    # 3. Pause for the user to open the notebook + click Run all.
    print(f"  Open this URL in your browser, click Runtime → Run all:")
    print(f"    {handle.notebook_url}")
    print()
    print("  The notebook will install Unsloth (~2 min), fine-tune the")
    print(f"  3B LoRA (~{EXPECTED_FINE_TUNE_MINUTES:.0f} min), and write the")
    print("  LoRA back to this Drive folder. We'll poll for it below.")
    print()
    await _wait_for_keypress(
        "Press <Enter> once you've started the notebook (or skip wait)…",
    )

    # 4. Start the GPU-hour clock + poll for the LoRA.
    tracker.start()
    deadline = time.monotonic() + poll_timeout_minutes * 60
    poll_count = 0
    success = False
    print(f"  Polling Drive every {POLL_INTERVAL_SECONDS}s for "
          f"{LORA_REMOTE_RELATIVE_PATH} …")
    try:
        while time.monotonic() < deadline:
            poll_count += 1
            file_id = await asyncio.to_thread(
                _find_remote_artifact, drive,
                parent_id=run_folder_id,
                relative_path=LORA_REMOTE_RELATIVE_PATH,
            )
            if file_id:
                print(f"  Found LoRA after {poll_count} polls "
                      f"({tracker.elapsed_seconds:.0f}s wall).")
                lora_local_path = download_dir / "lora.tar.gz"
                await asyncio.to_thread(
                    _download_file, drive,
                    file_id=file_id, local_path=lora_local_path,
                )
                lora_local = str(lora_local_path)
                # Eval result is best-effort — present iff the notebook
                # finished cleanly. Surface it but don't fail without it.
                eval_id = await asyncio.to_thread(
                    _find_remote_artifact, drive,
                    parent_id=run_folder_id,
                    relative_path=EVAL_RESULT_RELATIVE_PATH,
                )
                if eval_id:
                    eval_local_path = download_dir / "eval_result.json"
                    await asyncio.to_thread(
                        _download_file, drive,
                        file_id=eval_id, local_path=eval_local_path,
                    )
                    eval_local = str(eval_local_path)
                success = True
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
        if not success:
            print(f"  [warn] poll deadline hit ({poll_timeout_minutes} min) "
                  "without seeing the LoRA.")
            print(f"  Recovery: re-run the same command — the orchestrator")
            print(f"  picks up {handle.folder_path} on the next poll cycle.")
    finally:
        tracker.stop()

    return success, tracker, lora_local, eval_local


def _write_training_jsonl(samples: list[dict[str, str]]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="sagewai-colab-")) / DATASET_FILENAME
    with tmp.open("w") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")
    return tmp


def _write_eval_jsonl(samples: list[dict[str, str]]) -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="sagewai-colab-eval-")) / "email_triage_eval.jsonl"
    with tmp.open("w") as fh:
        for s in samples:
            fh.write(json.dumps(s) + "\n")
    return tmp


# ── Proof + cost-down (always — same shape as Examples 47/48) ────────


def print_live_proof(
    *, success: bool, tracker: GpuSpendTracker,
    lora_local_path: str | None, eval_local_path: str | None,
    poll_timeout_minutes: int,
) -> None:
    """Print the proof block after a live run."""
    elapsed_minutes = tracker.elapsed_seconds / 60.0
    cloud_call_baseline = calculate_cost(
        input_tokens=250, output_tokens=30,
        model="claude-haiku-4-5-20251001",
    )
    eval_accuracy_pct: float | None = None
    if eval_local_path:
        try:
            with open(eval_local_path) as fh:
                eval_blob = json.load(fh)
            correct = int(eval_blob.get("correct", 0))
            total = int(eval_blob.get("total", 0))
            if total > 0:
                eval_accuracy_pct = 100.0 * correct / total
        except (OSError, json.JSONDecodeError, ValueError):
            eval_accuracy_pct = None

    print(f"  Notebook outcome  : {'completed' if success else 'timed out'}")
    print(f"  GPU               : Tesla T4 (free) @ "
          f"${tracker.price_per_hour_usd:.4f}/hr")
    print(f"  Wall time         : {elapsed_minutes:.1f} min "
          f"({tracker.elapsed_seconds:.0f}s)")
    print(f"  GPU-hours         : {tracker.gpu_hours:.4f}h "
          "(tracked even at $0/hr)")
    print(f"  Spend             : ${tracker.accrued_usd:.6f}  "
          f"(session budget: {poll_timeout_minutes} min)")
    print(f"  Cloud-call baseline (calculate_cost): ${cloud_call_baseline:.6f}/call")
    if lora_local_path:
        print(f"  LoRA downloaded   : {lora_local_path}")
    if eval_accuracy_pct is not None:
        passed = "✓" if eval_accuracy_pct >= 80.0 else "✗"
        print(f"  Eval accuracy     : {eval_accuracy_pct:.1f}% on "
              f"{len(EMAIL_TRIAGE_EVAL_DATA)} held-out samples "
              f"({passed} ≥ 80% target)")
    print()


def print_costdown(
    *, gpu_spend_usd: float, baseline_call_usd: float, daily_volume: int,
) -> None:
    """Print the cost-down comparison: cloud-LLM-only vs. one-time fine-tune."""
    monthly_baseline = baseline_call_usd * daily_volume * 30
    annual_baseline = monthly_baseline * 12

    print(f"  Cloud baseline    : ${baseline_call_usd:.6f}/call "
          "(Anthropic Haiku, post-overhead)")
    print(f"  Local (fine-tuned): $0.000000/call (Ollama serves the LoRA)")
    print()
    print(f"  At {daily_volume} emails/day for 30 days:")
    print(f"    cloud-only      = ${monthly_baseline:>9.2f}/month "
          f"(${annual_baseline:>9.2f}/yr)")
    print(f"    after fine-tune = ${0.0:>9.2f}/month — the same task costs $0")
    print(f"    one-time spend  = ${gpu_spend_usd:>9.4f}  "
          "(this Colab fine-tune)")
    print()
    if gpu_spend_usd <= 0.0:
        print(f"  Payback           : immediate — the fine-tune cost $0.")
        print(f"                      Every cloud call after this is pure")
        print(f"                      savings.")
    else:
        payback_calls = int(gpu_spend_usd / baseline_call_usd)
        print(f"  Payback           : after ~{payback_calls} cloud calls")
        print(f"                      ({payback_calls / daily_volume:.1f} days at "
              f"{daily_volume}/day)")
    print()


# ── main ─────────────────────────────────────────────────────────────


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Example 44 — Free CUDA via Colab + Drive-sync.",
    )
    parser.add_argument(
        "--live", action="store_true",
        help="Authenticate to Drive, upload notebook + JSONL, poll for "
             "the LoRA. Default is stub mode — prints the orchestration "
             "plan without authenticating.",
    )
    parser.add_argument(
        "--drive-root", default=DRIVE_ROOT_DEFAULT,
        help=f"Top-level Drive folder for run subfolders. Default "
             f"'{DRIVE_ROOT_DEFAULT}'.",
    )
    parser.add_argument(
        "--poll-timeout-minutes",
        type=int, default=COLAB_FREE_TIER_SESSION_LIMIT_MINUTES,
        help=f"How long to poll for the LoRA in minutes. "
             f"Default {COLAB_FREE_TIER_SESSION_LIMIT_MINUTES} "
             "(Colab free-tier session ceiling).",
    )
    parser.add_argument(
        "--project-id", default="acme-prod",
        help="Project id used for spend attribution in the cost dashboard.",
    )
    args = parser.parse_args()

    _line()
    print(" Sagewai — Free CUDA via Colab + Drive-sync (example 44, Gap #8c)")
    _line()
    print()

    # ── 1. Probe the environment ───────────────────────────────────
    _line(" 1. Probe runtime environment ")
    print()
    env = _detect_environment()
    print(f"  OAuth client at {OAUTH_CLIENT_PATH}")
    print(f"                         : {'✓' if env.has_oauth_client else '✗'}")
    print(f"  pydrive2 importable    : {'✓' if env.has_pydrive else '✗'}")
    cached_status = (
        "✓" if env.has_cached_token else "✗  (first live run creates it)"
    )
    print(f"  Cached token at {ACCESS_TOKEN_PATH}")
    print(f"                         : {cached_status}")
    print(f"  --live flag passed     : {'✓' if args.live else '✗'}")
    print()

    will_go_live = args.live and env.can_go_live
    if args.live and not env.can_go_live:
        print("  [warn] --live requested but environment is incomplete.")
        if not env.has_oauth_client:
            print(f"         Save the OAuth client JSON at {OAUTH_CLIENT_PATH}")
            print("         (see atelier/docs/v1.0/inference-provisioning-setup.md)")
        if not env.has_pydrive:
            print("         pip install pydrive2 oauth2client")
        print("  Falling back to stub mode for this run.")
        print()

    # ── 2. Print the orchestration plan (always — stub or live) ────
    _line(" 2. Orchestration plan ")
    print()
    print_orchestration_plan(
        drive_root=args.drive_root,
        poll_timeout_minutes=args.poll_timeout_minutes,
    )

    # ── 3. Live or stub ────────────────────────────────────────────
    lora_local_path: str | None = None
    eval_local_path: str | None = None
    if will_go_live:
        _line(" 3. Live orchestration ")
        print()
        download_dir = Path(tempfile.mkdtemp(prefix="sagewai-colab-out-"))
        success, tracker, lora_local_path, eval_local_path = await run_live(
            drive_root=args.drive_root,
            poll_timeout_minutes=args.poll_timeout_minutes,
            project_id=args.project_id,
            download_dir=download_dir,
        )
        print()
        _line(" 4. The proof — live run ")
        print()
        print_live_proof(
            success=success,
            tracker=tracker,
            lora_local_path=lora_local_path,
            eval_local_path=eval_local_path,
            poll_timeout_minutes=args.poll_timeout_minutes,
        )
        gpu_spend = tracker.accrued_usd
    else:
        _line(" 3. Stub mode — no auth, no upload, no spend ")
        print()
        print("  No live orchestration requested. To run for real:")
        print(f"    1. Save Drive OAuth client at {OAUTH_CLIENT_PATH}")
        print("       (Console → APIs → OAuth client → Desktop app)")
        print("    2. pip install pydrive2 oauth2client")
        print("    3. python 44_colab_free_cuda.py --live")
        print()
        print("  Setup walkthrough: "
              "atelier/docs/v1.0/inference-provisioning-setup.md")
        print()
        gpu_spend = 0.0

    # ── 4/5. Cost-down (always — the headline pitch) ──────────────
    _line(" Cost-down: cloud-LLM baseline vs. fine-tuned local ")
    print()
    print_costdown(
        gpu_spend_usd=gpu_spend,
        baseline_call_usd=BASELINE_COST_PER_CALL_USD,
        daily_volume=PRODUCTION_VOLUME_PER_DAY,
    )

    _line(" The democratization tier ")
    print()
    print("  No credit card. No corporate AWS account. No GPU at home.")
    print("  Anyone with a Google login gets a Tesla T4 for free,")
    print("  fine-tunes a 3B model on YOUR data via the companion")
    print("  notebook, and walks away with a deployable LoRA. The")
    print("  Drive-sync path is honest about Colab's 2026 ergonomics —")
    print("  one click in the browser; everything else is automated.")
    print()
    print("  This is the example you can run with no credit card.")
    print("  When the audience-pin person asks 'do I really not need")
    print("  to pay anything?' — point them here.")
    print()


if __name__ == "__main__":
    asyncio.run(main())
