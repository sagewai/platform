# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fleet CLI commands — manage fleet workers and enrollment keys.

Provides the ``sagewai fleet`` command group with subcommands for
worker registration, listing, and enrollment key management.

Usage::

    sagewai fleet register --name my-gpu-box --org acme --models gpt-4o,llama3-70b
    sagewai fleet list-workers --org acme
    sagewai fleet create-key --name onboarding-key --max-uses 10
    sagewai fleet list-keys
    sagewai fleet revoke-key <key-id>
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import click
import httpx

from sagewai.fleet.models import WorkerApprovalStatus, WorkerCapabilities, WorkerRecord
from sagewai.fleet.normalizer import ModelNormalizer
from sagewai.fleet.runner import RegistrationError, TerminalAuthError, WorkerRunner
from sagewai.harness.discovery import openai_base_url
from sagewai.work.profiles.software.fleet_worker import (
    SoftwareFleetTaskHandler,
    SoftwareFleetWorkspaceResolver,
)
from sagewai.work.runtime_capabilities import (
    RefreshingClaudeRuntime,
    RefreshingCodexRuntime,
    RuntimeCapabilityProbeError,
    probe_runtime_capabilities,
    select_codex_task_configuration,
    select_runtime_configuration,
)
from sagewai.work.tasks.models import HarnessTier

# ---------------------------------------------------------------------------
# In-memory registry for local/demo use (gateway will use Postgres)
# ---------------------------------------------------------------------------


class _LocalFleetRegistry:
    """Singleton in-memory registry for CLI demo/local use."""

    _instance: _LocalFleetRegistry | None = None

    def __init__(self) -> None:
        self.workers: dict[str, WorkerRecord] = {}

    @classmethod
    def get(cls) -> _LocalFleetRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------


def _parse_duration(value: str) -> timedelta:
    """Parse a human-friendly duration string into a timedelta.

    Supported formats: ``7d``, ``24h``, ``30m``, ``90s``, or combinations
    like ``1d12h``.

    Raises:
        click.BadParameter: If the format is unrecognized.
    """
    pattern = re.compile(r"(\d+)([dhms])")
    matches = pattern.findall(value.lower())
    if not matches:
        raise click.BadParameter(
            f"Invalid duration '{value}'. Use format like '7d', '24h', '30m'."
        )

    total = timedelta()
    for amount, unit in matches:
        n = int(amount)
        if unit == "d":
            total += timedelta(days=n)
        elif unit == "h":
            total += timedelta(hours=n)
        elif unit == "m":
            total += timedelta(minutes=n)
        elif unit == "s":
            total += timedelta(seconds=n)
    return total


def _parse_runtime_value(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    if not value or value != value.strip():
        raise click.BadParameter("must be non-empty and trimmed")
    return value


def _parse_positive_budget(
    _ctx: click.Context,
    _param: click.Parameter,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    try:
        parsed = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise click.BadParameter("must be a positive number") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise click.BadParameter("must be a positive number")
    return value


def _parse_harness_tiers(values: tuple[str, ...]) -> dict[str, HarnessTier]:
    tiers: dict[str, HarnessTier] = {}
    for value in values:
        name, separator, remainder = value.partition("=")
        backend, backend_separator, model = remainder.partition(":")
        if (
            separator != "="
            or backend_separator != ":"
            or name not in {"simple", "medium", "complex"}
            or not backend
            or not model
        ):
            raise click.UsageError(
                "--harness-tier must use NAME=BACKEND:MODEL with NAME in simple, medium, complex."
            )
        tiers[name] = HarnessTier(backend=backend, model=model)
    return tiers


def _parse_harness_backends(values: tuple[str, ...]) -> dict[str, str]:
    backends: dict[str, str] = {}
    for value in values:
        name, separator, url = value.partition("=")
        if separator != "=" or not name or not url:
            raise click.UsageError("--harness-backend must use NAME=URL.")
        backends[name] = openai_base_url(url)
    return backends


def _fleet_gateway_url(gateway_url: str | None) -> str:
    return gateway_url or os.environ.get("SAGEWAI_ADMIN_URL", "http://localhost:8000")


def _fleet_gateway_headers(project: str | None) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("SAGEWAI_ADMIN_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if project:
        headers["X-Project-ID"] = project
    return headers


def _raise_fleet_gateway_error(action: str, response: httpx.Response) -> None:
    hint = " — set SAGEWAI_ADMIN_TOKEN" if response.status_code == 401 else ""
    message = f"{action} failed: {response.status_code} {response.text[:200]}{hint}"
    raise click.ClickException(message)


def _fleet_key_status(key: dict) -> str:
    if key["revoked"]:
        return "revoked"
    if key["expires_at"] is not None:
        expires_at = datetime.fromisoformat(str(key["expires_at"]).replace("Z", "+00:00"))
        if datetime.now(timezone.utc) >= expires_at:
            return "expired"
    return "active"


# ---------------------------------------------------------------------------
# Fleet command group
# ---------------------------------------------------------------------------


@click.group("fleet")
def fleet_group() -> None:
    """Manage distributed fleet workers and enrollment keys.

    \b
    Examples:
      sagewai fleet register --name gpu-box --org acme --models gpt-4o,llama3
      sagewai fleet list-workers --org acme --status approved
      sagewai fleet create-key --name onboarding --max-uses 10
      sagewai fleet list-keys
      sagewai fleet revoke-key <key-id>
    """


@fleet_group.command("register")
@click.option("--name", required=True, help="Worker name.")
@click.option("--org", required=True, help="Organization ID.")
@click.option("--models", required=True, help="Comma-separated model list.")
@click.option("--pool", default="default", help="Worker pool.")
@click.option("--labels", default=None, help="Comma-separated key=value labels.")
@click.option("--capabilities", default=None, help="Comma-separated worker capabilities.")
@click.option("--enrollment-key", default=None, help="Enrollment key for auto-approval.")
@click.option("--cloud-url", default=None, help="Cloud gateway URL (for remote registration).")
def register(
    name: str,
    org: str,
    models: str,
    pool: str,
    labels: str | None,
    capabilities: str | None,
    enrollment_key: str | None,
    cloud_url: str | None,
) -> None:
    """Register this machine as a fleet worker."""
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    canonical = ModelNormalizer.canonical_list(model_list)
    capability_names = [name.strip() for name in (capabilities or "").split(",") if name.strip()]

    parsed_labels: dict[str, str] = {}
    if labels:
        for pair in labels.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                parsed_labels[k.strip()] = v.strip()

    caps = WorkerCapabilities(
        models_supported=model_list,
        models_canonical=canonical,
        capability_names=capability_names,
        max_concurrent=1,
        labels=parsed_labels,
        pool=pool,
    )

    if cloud_url:
        click.echo(f"Remote registration to {cloud_url} is not yet implemented.")
        click.echo("Use local registration (omit --cloud-url) for now.")
        return

    worker_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    approval = WorkerApprovalStatus.PENDING

    record = WorkerRecord(
        id=worker_id,
        name=name,
        org_id=org,
        capabilities=caps,
        approval_status=approval,
        registered_at=now,
        approved_at=now if approval == WorkerApprovalStatus.APPROVED else None,
    )

    registry = _LocalFleetRegistry.get()
    registry.workers[worker_id] = record

    click.echo(f"Registered worker '{name}' (id: {worker_id[:12]}...)")
    click.echo(f"  Organization : {org}")
    click.echo(f"  Pool         : {pool}")
    click.echo(f"  Models       : {', '.join(canonical)}")
    click.echo(f"  Status       : {approval.value}")
    if capability_names:
        click.echo(f"  Capabilities : {', '.join(capability_names)}")
    if parsed_labels:
        click.echo(f"  Labels       : {parsed_labels}")


@fleet_group.command("run")
@click.option("--name", default=None, help="Worker name (required unless --worker-id).")
@click.option("--models", default=None, help="Comma-separated model list (required unless --worker-id).")
@click.option("--pool", default="default", help="Worker pool.")
@click.option("--labels", default=None, help="Comma-separated key=value labels.")
@click.option("--capabilities", default=None, help="Comma-separated worker capabilities.")
@click.option("--max-concurrent", default=1, type=int, help="Max in-flight tasks.")
@click.option("--project", default=None, help="Project scope (X-Project-ID).")
@click.option(
    "--work-repository",
    type=click.Path(
        path_type=Path,
        exists=True,
        file_okay=False,
        resolve_path=True,
    ),
    default=None,
    help="Trusted local repository for native Work operator tasks.",
)
@click.option(
    "--claude-analysis-model",
    default=None,
    help="Claude model for analysis and design stages.",
)
@click.option(
    "--claude-analysis-effort",
    default=None,
    callback=_parse_runtime_value,
    help="Claude effort for analysis and design stages; validated by the live CLI.",
)
@click.option(
    "--claude-analysis-max-budget-usd",
    default=None,
    callback=_parse_positive_budget,
    metavar="USD",
    help="Positive Claude CLI max budget for analysis and design stages.",
)
@click.option(
    "--claude-review-model",
    default=None,
    help="Claude model for review stages.",
)
@click.option(
    "--claude-review-effort",
    default=None,
    callback=_parse_runtime_value,
    help="Claude effort for review stages; validated by the live CLI.",
)
@click.option(
    "--claude-review-max-budget-usd",
    default=None,
    callback=_parse_positive_budget,
    metavar="USD",
    help="Positive Claude CLI max budget for review stages.",
)
@click.option(
    "--codex-model",
    default=None,
    help=(
        "Preferred worker-local Codex model for bounded implementation; "
        "complex/design-required Work and repairs use the live provider default."
    ),
)
@click.option(
    "--codex-reasoning-effort",
    default=None,
    callback=_parse_runtime_value,
    help=(
        "Worker-local Codex reasoning effort for implementation and repair stages; "
        "model support is determined by that worker's Codex CLI."
    ),
)
@click.option(
    "--harness-tier",
    "harness_tier_options",
    multiple=True,
    help="Harness tier mapping NAME=BACKEND:MODEL (repeatable).",
)
@click.option(
    "--harness-backend",
    "harness_backend_options",
    multiple=True,
    help="Harness backend mapping NAME=URL (repeatable); /v1 is appended when missing.",
)
@click.option("--enrollment-key", default=None, help="Enrollment key for auto-approval.")
@click.option("--worker-id", default=None, help="Reuse an approved worker; skip registration.")
@click.option(
    "--worker-secret",
    default=None,
    help="Worker secret (else $SAGEWAI_WORKER_SECRET / creds file).",
)
@click.option("--creds-file", default=None, help="Path to the worker credentials file.")
@click.option("--exec", "exec_cmd", default=None, help="Shell command to run per task.")
@click.option("--exec-timeout", default=300.0, type=float, help="Per-task kill (seconds).")
@click.option("--env", "envs", multiple=True, help="Task env var KEY=VALUE (repeatable).")
@click.option("--env-file", default=None, help="File of KEY=VALUE task env vars.")
@click.option("--image", default=None, help="Run each task in a fresh container of this image.")
@click.option(
    "--docker-arg",
    "docker_args",
    multiple=True,
    help="Extra `docker run` args (repeatable).",
)
@click.option("--register-only", is_flag=True, help="Register (appear in the screen), then exit.")
@click.option("--once", is_flag=True, help="Claim/execute/report one task, then exit.")
@click.option(
    "--gateway-url",
    default=None,
    help="Gateway base URL (default $SAGEWAI_ADMIN_URL).",
)
@click.option("--poll-timeout", default=30.0, type=float, help="Claim long-poll seconds.")
@click.option(
    "--heartbeat-interval",
    default=10.0,
    type=float,
    help="Heartbeat cadence seconds.",
)
def run(
    name,
    models,
    pool,
    labels,
    capabilities,
    max_concurrent,
    project,
    work_repository,
    claude_analysis_model,
    claude_analysis_effort,
    claude_analysis_max_budget_usd,
    claude_review_model,
    claude_review_effort,
    claude_review_max_budget_usd,
    codex_model,
    codex_reasoning_effort,
    harness_tier_options,
    harness_backend_options,
    enrollment_key,
    worker_id,
    worker_secret,
    creds_file,
    exec_cmd,
    exec_timeout,
    envs,
    env_file,
    image,
    docker_args,
    register_only,
    once,
    gateway_url,
    poll_timeout,
    heartbeat_interval,
):
    """Run this machine as a fleet worker (register + claim/execute/report loop)."""
    if worker_id is None and (not name or (not models and not capabilities)):
        raise click.UsageError(
            "--name and either --models or --capabilities are required unless --worker-id is given."
        )

    model_list = [m.strip() for m in (models or "").split(",") if m.strip()]
    capability_names = [name.strip() for name in (capabilities or "").split(",") if name.strip()]
    native_runtime_capabilities = {
        name for name in capability_names
        if name in {"runtime.codex", "runtime.claude", "runtime.harness"}
    }
    has_native_runtime_options = any(
        value is not None
        for value in (
            claude_analysis_model,
            claude_analysis_effort,
            claude_analysis_max_budget_usd,
            claude_review_model,
            claude_review_effort,
            claude_review_max_budget_usd,
            codex_model,
            codex_reasoning_effort,
        )
    ) or bool(harness_tier_options or harness_backend_options)
    harness_tiers = _parse_harness_tiers(harness_tier_options)
    harness_backends = _parse_harness_backends(harness_backend_options)
    task_handler = None
    if native_runtime_capabilities:
        if not project:
            raise click.UsageError(
                "--project is required for native Work operator capabilities."
            )
        if work_repository is None:
            raise click.UsageError(
                "--work-repository is required for native Work operator capabilities."
            )
        if exec_cmd is not None or image is not None:
            raise click.UsageError(
                "native Work operator capabilities cannot be combined with --exec or --image."
            )
        claude_options = (
            claude_analysis_model,
            claude_analysis_effort,
            claude_analysis_max_budget_usd,
            claude_review_model,
            claude_review_effort,
            claude_review_max_budget_usd,
        )
        if "runtime.codex" not in native_runtime_capabilities and (
            codex_model is not None or codex_reasoning_effort is not None
        ):
            raise click.UsageError("Codex runtime options require runtime.codex capability.")
        if "runtime.claude" not in native_runtime_capabilities and any(
            value is not None for value in claude_options
        ):
            raise click.UsageError("Claude runtime options require runtime.claude capability.")
        if "runtime.harness" not in native_runtime_capabilities and (
            harness_tiers or harness_backends
        ):
            raise click.UsageError("Harness runtime options require runtime.harness capability.")
        if "runtime.harness" in native_runtime_capabilities and "complex" not in harness_tiers:
            raise click.UsageError(
                "runtime.harness capability requires --harness-tier complex=BACKEND:MODEL "
                "(the control plane dispatches the complex tier)."
            )
        if "runtime.harness" in native_runtime_capabilities:
            missing = {tier.backend for tier in harness_tiers.values()} - harness_backends.keys()
            if missing:
                raise click.UsageError(
                    f"--harness-tier backends need --harness-backend: {', '.join(sorted(missing))}"
                )

        async def resolve_native_handler():
            handler_kwargs = {}
            discovered_models: list[str] = []
            probe_kinds = tuple(
                runtime
                for runtime in ("runtime.codex", "runtime.claude")
                if runtime in native_runtime_capabilities
            )
            snapshots = {
                runtime: snapshot
                for runtime, snapshot in zip(
                    probe_kinds,
                    await asyncio.gather(
                        *(probe_runtime_capabilities(runtime) for runtime in probe_kinds)
                    ),
                    strict=True,
                )
            }
            if "runtime.codex" in snapshots:
                snapshot = snapshots["runtime.codex"]
                bounded_selection = select_codex_task_configuration(
                    snapshot,
                    stage="implement",
                    risk="low",
                    design_required=False,
                    bounded_model=codex_model,
                    requested_effort=codex_reasoning_effort,
                )
                complex_selection = select_codex_task_configuration(
                    snapshot,
                    stage="implement",
                    risk="high",
                    design_required=True,
                    bounded_model=codex_model,
                    requested_effort=codex_reasoning_effort,
                )
                discovered_models.extend(model.model for model in snapshot.models)
                click.echo(
                    f"Resolved bounded {bounded_selection.verification_text()}"
                )
                click.echo(
                    f"Resolved complex {complex_selection.verification_text()}"
                )
                handler_kwargs["codex_runtime"] = RefreshingCodexRuntime(
                    snapshot=snapshot,
                    requested_model=codex_model,
                    requested_effort=codex_reasoning_effort,
                )
            if "runtime.claude" in snapshots:
                snapshot = snapshots["runtime.claude"]
                analysis_selection = select_runtime_configuration(
                    snapshot,
                    requested_model=claude_analysis_model,
                    requested_effort=claude_analysis_effort,
                )
                review_selection = select_runtime_configuration(
                    snapshot,
                    requested_model=claude_review_model,
                    requested_effort=claude_review_effort,
                )
                discovered_models.extend(model.model for model in snapshot.models)
                click.echo(
                    f"Resolved analysis {analysis_selection.verification_text()}"
                )
                click.echo(f"Resolved review {review_selection.verification_text()}")
                handler_kwargs["claude_analysis_runtime"] = RefreshingClaudeRuntime(
                    snapshot=snapshot,
                    requested_model=claude_analysis_model,
                    requested_effort=claude_analysis_effort,
                    max_budget_usd=claude_analysis_max_budget_usd,
                )
                handler_kwargs["claude_review_runtime"] = RefreshingClaudeRuntime(
                    snapshot=snapshot,
                    requested_model=claude_review_model,
                    requested_effort=claude_review_effort,
                    max_budget_usd=claude_review_max_budget_usd,
                )
            if "runtime.harness" in native_runtime_capabilities:
                handler_kwargs["harness_tiers"] = harness_tiers
                handler_kwargs["harness_backends"] = harness_backends
            return (
                SoftwareFleetTaskHandler(
                    workspace_resolver=SoftwareFleetWorkspaceResolver(
                        repository=work_repository,
                    ),
                    **handler_kwargs,
                ),
                discovered_models,
            )

        try:
            task_handler, discovered_models = asyncio.run(resolve_native_handler())
        except RuntimeCapabilityProbeError as exc:
            raise click.ClickException(str(exc)) from exc
        model_list = list(dict.fromkeys((*model_list, *discovered_models)))
    elif work_repository is not None:
        raise click.UsageError(
            "--work-repository requires runtime.codex, runtime.claude, or runtime.harness capability."
        )
    elif has_native_runtime_options:
        raise click.UsageError(
            "native runtime options require runtime.codex, runtime.claude, or runtime.harness capability."
        )
    parsed_labels: dict[str, str] = {}
    if labels:
        for pair in labels.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                parsed_labels[k.strip()] = v.strip()

    task_env: dict[str, str] = {}
    if env_file:
        with open(env_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                task_env[k.strip()] = v.strip()
    for pair in envs:
        if "=" in pair:
            k, v = pair.split("=", 1)
            task_env[k.strip()] = v.strip()

    base_url = gateway_url or os.environ.get("SAGEWAI_ADMIN_URL", "http://localhost:8000")
    runner = WorkerRunner(
        base_url=base_url,
        token=os.environ.get("SAGEWAI_ADMIN_TOKEN", ""),
        project=project,
        name=name or "worker",
        models=model_list,
        capability_names=capability_names,
        pool=pool,
        labels=parsed_labels,
        max_concurrent=max_concurrent,
        enrollment_key=enrollment_key,
        worker_id=worker_id,
        worker_secret=worker_secret or os.environ.get("SAGEWAI_WORKER_SECRET"),
        creds_file=creds_file,
        exec_cmd=exec_cmd,
        exec_timeout=exec_timeout,
        task_env=task_env,
        image=image,
        docker_args=list(docker_args),
        poll_timeout=poll_timeout,
        heartbeat_interval=heartbeat_interval,
        task_handler=task_handler,
    )

    async def _drive():
        try:
            if register_only:
                wid, status = await runner.register()
                click.echo(f"Registered worker {wid} (status: {status})")
                if status == "pending":
                    click.echo("Approve it in the Workers screen, or pass --enrollment-key.")
                return
            if once:
                result = await runner.run_once()
                if result.get("claimed"):
                    click.echo(
                        f"Task {result['run_id']}: {result['status']} "
                        f"(reported={result['reported']})"
                    )
                    if not result["reported"]:
                        raise SystemExit(2)  # executed but the report was rejected
                else:
                    reason = result.get("reason", "no_task")
                    detail = result.get("detail", "")
                    click.echo(f"No task claimed ({reason}). {detail}".rstrip())
                    if reason == "terminal":
                        # rejected / revoked / unknown / unauthorized — not transient
                        raise SystemExit(2)
                return
            await runner.run()
        except httpx.ConnectError as exc:
            raise click.ClickException(
                f"Could not connect to Sagewai gateway at {base_url}. "
                "Check SAGEWAI_ADMIN_URL and that the backend is running."
            ) from exc
        except RegistrationError as exc:
            hint = ""
            if exc.status_code == 401:
                hint = " — set SAGEWAI_ADMIN_TOKEN (and SAGEWAI_ADMIN_URL) for this gateway"
            raise click.ClickException(f"Worker registration failed ({exc.status_code}){hint}")
        except TerminalAuthError as exc:
            # rejected / revoked / unknown worker — the daemon cannot recover.
            click.echo(f"Worker stopped: {exc}", err=True)
            raise SystemExit(2)
        finally:
            await runner.aclose()

    asyncio.run(_drive())


@fleet_group.command("enqueue")
@click.option("--agent", default="worker-agent", help="Agent name to run.")
@click.option("--message", "-m", required=True, help="Message/prompt for the agent.")
@click.option("--model", default=None, help="Model for the task (matched to a worker).")
@click.option("--pool", default="default", help="Target pool.")
@click.option("--project", default=None, help="Project scope (X-Project-ID).")
@click.option("--gateway-url", default=None, help="Gateway base URL.")
def enqueue(agent, message, model, pool, project, gateway_url):
    """Enqueue an agent task onto the fleet for a worker to claim and run."""
    import os

    import httpx

    base_url = gateway_url or os.environ.get("SAGEWAI_ADMIN_URL", "http://localhost:8000")
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("SAGEWAI_ADMIN_TOKEN", "")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if project:
        headers["X-Project-ID"] = project
    body: dict = {"pool": pool, "payload": {"agent": agent, "message": message, "model": model}}
    if model:
        body["model"] = model
    r = httpx.post(base_url + "/api/v1/fleet/tasks", json=body, headers=headers, timeout=30.0)
    if r.status_code not in (200, 201):
        raise click.ClickException(f"enqueue failed: {r.status_code} {r.text[:200]}")
    click.echo(f"Enqueued task {r.json().get('run_id')} (pool={pool}, model={model or 'any'})")


@fleet_group.command("list-workers")
@click.option("--org", required=True, help="Organization ID.")
@click.option(
    "--status",
    default=None,
    type=click.Choice(["pending", "approved", "rejected", "revoked"]),
    help="Filter by status.",
)
@click.option("--pool", default=None, help="Filter by pool.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def list_workers(org: str, status: str | None, pool: str | None, as_json: bool) -> None:
    """List registered fleet workers."""
    import json

    registry = _LocalFleetRegistry.get()
    workers = [w for w in registry.workers.values() if w.org_id == org]

    if status:
        workers = [w for w in workers if w.approval_status.value == status]
    if pool:
        workers = [w for w in workers if w.capabilities.pool == pool]

    if as_json:
        click.echo(json.dumps([w.model_dump(mode="json") for w in workers], indent=2))
        return

    if not workers:
        click.echo("No workers found.")
        return

    for w in workers:
        hb = w.last_heartbeat.isoformat()[:19] if w.last_heartbeat else "never"
        click.echo(
            f"  {w.id[:12]}  {w.name:<20s}  {w.approval_status.value:<10s}  "
            f"{w.capabilities.pool:<12s}  {', '.join(w.capabilities.models_canonical):<30s}  "
            f"heartbeat={hb}"
        )


@fleet_group.command("create-key")
@click.option("--name", required=True, help="Key name.")
@click.option("--max-uses", default=None, type=int, help="Maximum registrations.")
@click.option("--expires", default=None, help="Expiration duration (e.g. '7d', '24h').")
@click.option("--pools", default=None, help="Comma-separated allowed pools.")
@click.option("--models", default=None, help="Comma-separated allowed models.")
@click.option("--project", default=None, help="Project scope (X-Project-ID).")
@click.option("--gateway-url", default=None, help="Gateway base URL.")
def create_key(
    name: str,
    max_uses: int | None,
    expires: str | None,
    pools: str | None,
    models: str | None,
    project: str | None,
    gateway_url: str | None,
) -> None:
    """Create an enrollment key for fleet worker registration."""
    expires_at: str | None = None
    if expires:
        delta = _parse_duration(expires)
        expires_at = (datetime.now(timezone.utc) + delta).isoformat()

    allowed_pools = [p.strip() for p in pools.split(",") if p.strip()] if pools else []
    allowed_models = [m.strip() for m in models.split(",") if m.strip()] if models else []
    body = {
        "name": name,
        "max_uses": max_uses,
        "expires_at": expires_at,
        "allowed_pools": allowed_pools,
        "allowed_models": allowed_models,
    }
    base_url = _fleet_gateway_url(gateway_url)
    response = httpx.post(
        base_url + "/api/v1/fleet/enrollment-keys",
        json=body,
        headers=_fleet_gateway_headers(project),
        timeout=30.0,
    )
    if response.status_code not in (200, 201):
        _raise_fleet_gateway_error("create-key", response)
    data = response.json()

    click.echo(f"Enrollment key created: {data['raw_key']}")
    click.echo("Save this key - it will not be shown again.")
    click.echo(f"  ID      : {data['id'][:12]}...")
    click.echo(f"  Name    : {data['name']}")
    if data["max_uses"]:
        click.echo(f"  Max uses: {data['max_uses']}")
    if data["expires_at"]:
        click.echo(f"  Expires : {data['expires_at']}")
    if data["allowed_pools"]:
        click.echo(f"  Pools   : {', '.join(data['allowed_pools'])}")
    if data["allowed_models"]:
        click.echo(f"  Models  : {', '.join(data['allowed_models'])}")


@fleet_group.command("list-keys")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
@click.option("--project", default=None, help="Project scope (X-Project-ID).")
@click.option("--gateway-url", default=None, help="Gateway base URL.")
def list_keys(as_json: bool, project: str | None, gateway_url: str | None) -> None:
    """List enrollment keys."""
    base_url = _fleet_gateway_url(gateway_url)
    response = httpx.get(
        base_url + "/api/v1/fleet/enrollment-keys",
        headers=_fleet_gateway_headers(project),
        timeout=30.0,
    )
    if response.status_code != 200:
        _raise_fleet_gateway_error("list-keys", response)
    keys = response.json()["keys"]

    if as_json:
        click.echo(json.dumps(keys, indent=2))
        return

    if not keys:
        click.echo("No enrollment keys found.")
        return

    for k in keys:
        status = _fleet_key_status(k)
        uses = (
            f"{k['current_uses']}/{k['max_uses']}"
            if k["max_uses"]
            else f"{k['current_uses']}/unlimited"
        )
        expires = str(k["expires_at"])[:19] if k["expires_at"] else "never"
        click.echo(
            f"  {k['id'][:12]}  {k['name']:<20s}  {status:<10s}  "
            f"uses={uses:<15s}  expires={expires}"
        )


@fleet_group.command("revoke-key")
@click.argument("key_id")
@click.option("--project", default=None, help="Project scope (X-Project-ID).")
@click.option("--gateway-url", default=None, help="Gateway base URL.")
def revoke_key(key_id: str, project: str | None, gateway_url: str | None) -> None:
    """Revoke an enrollment key."""
    base_url = _fleet_gateway_url(gateway_url)
    response = httpx.delete(
        base_url + f"/api/v1/fleet/enrollment-keys/{key_id}",
        headers=_fleet_gateway_headers(project),
        timeout=30.0,
    )
    if response.status_code not in (200, 204):
        _raise_fleet_gateway_error("revoke-key", response)
    click.echo(f"Revoked enrollment key {key_id}.")
