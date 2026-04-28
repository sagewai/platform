# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai CLI — unified command-line interface for durable workflows.

Provides commands for managing workflows, workers, the dead letter queue,
and queue statistics via the PostgresStore backend.

Usage::

    sagewai workflow list
    sagewai workflow enqueue article-pipeline --input '{"topic": "AI"}'
    sagewai workflow inspect run-abc123
    sagewai workflow retry run-abc123
    sagewai workflow cancel run-abc123
    sagewai workflow approve run-abc123
    sagewai workflow reject run-abc123

    sagewai worker start --concurrency 4
    sagewai worker status

    sagewai dlq list
    sagewai dlq retry run-abc123
    sagewai dlq purge --older-than 30

    sagewai db stats
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

import click


def _parse_size(value: str) -> int:
    """Parse '2g', '512m', '1024k' → bytes."""
    s = value.strip().lower()
    mul = 1
    if s.endswith("g"):
        mul = 1024**3
        s = s[:-1]
    elif s.endswith("m"):
        mul = 1024**2
        s = s[:-1]
    elif s.endswith("k"):
        mul = 1024
        s = s[:-1]
    return int(float(s) * mul)


def _run_async(coro: Any) -> Any:
    """Run async function from sync CLI context."""
    return asyncio.run(coro)


async def _get_store(database_url: str | None = None):
    """Create and initialize a PostgresStore."""
    import os

    url = database_url or os.getenv("DATABASE_URL") or os.getenv(
        "SAGEWAI_DATABASE_URL"
    )
    if not url:
        click.echo(
            "Error: No database URL. Set DATABASE_URL or use --db-url",
            err=True,
        )
        sys.exit(1)
    from sagewai.core.stores.postgres import PostgresStore

    store = PostgresStore(database_url=url)
    await store.initialize()
    return store


def register_commands(
    cli: click.Group,
    workflow_group: click.Group,
    db_group: click.Group,
) -> None:
    """Register Phase-4 durable-workflow commands on existing CLI groups.

    Called from ``sagewai.cli.__init__`` after the base groups are defined.

    Args:
        cli: Root CLI group (receives ``worker`` and ``dlq`` sub-groups).
        workflow_group: Existing ``workflow`` group (receives new commands).
        db_group: Existing ``db`` group (receives ``stats`` command).
    """
    # ── Workflow commands ────────────────────────────────────────

    @workflow_group.command("list")
    @click.option("--status", type=str, default=None, help="Filter by status")
    @click.option("--limit", type=int, default=20, help="Max results")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_list(
        status: str | None, limit: int, db_url: str | None
    ) -> None:
        """List workflow executions."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            execs = await monitor.list_executions(status=status, limit=limit)
            if not execs:
                click.echo("No executions found.")
                return
            click.echo(
                f"{'RUN ID':<20} {'WORKFLOW':<25} "
                f"{'STATUS':<12} {'UPDATED':<20}"
            )
            click.echo("-" * 77)
            for ex in execs:
                click.echo(
                    f"{ex.run_id:<20} {ex.workflow_name:<25} "
                    f"{ex.status:<12} {ex.updated_at:<20}"
                )

        _run_async(_run())

    @workflow_group.command("inspect")
    @click.argument("run_id")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_inspect(run_id: str, db_url: str | None) -> None:
        """Inspect a workflow execution in detail."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            detail = await monitor.get_execution(run_id)
            if not detail:
                click.echo(f"Execution {run_id} not found.", err=True)
                sys.exit(1)
            click.echo(f"Run ID:    {detail.run_id}")
            click.echo(f"Workflow:  {detail.workflow_name}")
            click.echo(f"Status:    {detail.status}")
            click.echo(f"Created:   {detail.created_at}")
            click.echo(f"Updated:   {detail.updated_at}")
            if detail.error:
                click.echo(f"Error:     {detail.error}")
            if detail.steps:
                click.echo(f"\nSteps ({len(detail.steps)}):")
                for step in detail.steps:
                    duration = (
                        f"{step.duration_seconds:.1f}s"
                        if step.duration_seconds
                        else "-"
                    )
                    click.echo(
                        f"  {step.step_name:<25} {step.status:<12} "
                        f"attempts={step.attempts}  duration={duration}"
                    )

        _run_async(_run())

    @workflow_group.command("enqueue")
    @click.argument("workflow_name")
    @click.option(
        "--input", "input_json", type=str, default="{}", help="JSON input"
    )
    @click.option(
        "--priority",
        type=int,
        default=0,
        help="Priority (higher = more urgent)",
    )
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    @click.option(
        "--requires-sandbox-mode",
        type=click.Choice(["none", "per_tool", "per_run", "per_worker"]),
        default=None,
        help="Minimum sandbox isolation required for this run.",
    )
    @click.option(
        "--requires-image",
        default=None,
        help="Full image reference required (ghcr.io/sagewai/sandbox-ml:0.1.5 or BYO).",
    )
    @click.option(
        "--requires-network-policy",
        type=click.Choice(["none", "egress_allowlist", "full"]),
        default=None,
        help="Network policy required for this run.",
    )
    def workflow_enqueue(
        workflow_name: str,
        input_json: str,
        priority: int,
        db_url: str | None,
        requires_sandbox_mode: str | None,
        requires_image: str | None,
        requires_network_policy: str | None,
    ) -> None:
        """Enqueue a workflow for execution."""

        async def _run() -> None:
            from sagewai.sandbox.models import NetworkPolicy, SandboxMode

            store = await _get_store(db_url)
            try:
                input_data = json.loads(input_json)
            except json.JSONDecodeError:
                click.echo("Error: Invalid JSON input", err=True)
                sys.exit(1)

            mode_enum = SandboxMode(requires_sandbox_mode) if requires_sandbox_mode else None
            net_enum = NetworkPolicy(requires_network_policy) if requires_network_policy else None

            run_id, is_new = await store.enqueue_workflow(
                workflow_name,
                input_data,
                priority=priority,
                requires_sandbox_mode=mode_enum,
                requires_image=requires_image,
                requires_network_policy=net_enum,
            )
            if is_new:
                click.echo(
                    f"Enqueued: {workflow_name} "
                    f"(run_id={run_id}, priority={priority})"
                )
            else:
                click.echo(f"Already exists: {run_id}")

        _run_async(_run())

    @workflow_group.command("retry")
    @click.argument("run_id")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_retry(run_id: str, db_url: str | None) -> None:
        """Retry a failed workflow execution."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            new_id = await monitor.retry_execution(run_id)
            click.echo(f"Retried as: {new_id}")

        _run_async(_run())

    @workflow_group.command("cancel")
    @click.argument("run_id")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_cancel(run_id: str, db_url: str | None) -> None:
        """Cancel a running workflow."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            ok = await monitor.terminate_execution(run_id)
            if ok:
                click.echo(f"Cancelled: {run_id}")
            else:
                click.echo(f"Could not cancel: {run_id}", err=True)

        _run_async(_run())

    @workflow_group.command("approve")
    @click.argument("run_id")
    @click.option("--comment", type=str, default="", help="Approval comment")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_approve(
        run_id: str, comment: str, db_url: str | None
    ) -> None:
        """Approve a waiting workflow."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            ok = await monitor.signal_execution(
                run_id,
                "__approval__",
                {"approved": True, "comment": comment, "reviewer": "cli"},
            )
            if ok:
                click.echo(f"Approved: {run_id}")
            else:
                click.echo(f"Could not approve: {run_id}", err=True)

        _run_async(_run())

    @workflow_group.command("reject")
    @click.argument("run_id")
    @click.option("--reason", type=str, default="", help="Rejection reason")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def workflow_reject(
        run_id: str, reason: str, db_url: str | None
    ) -> None:
        """Reject a waiting workflow."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            ok = await monitor.signal_execution(
                run_id,
                "__approval__",
                {"approved": False, "reason": reason, "reviewer": "cli"},
            )
            if ok:
                click.echo(f"Rejected: {run_id}")
            else:
                click.echo(f"Could not reject: {run_id}", err=True)

        _run_async(_run())

    # ── Worker commands ──────────────────────────────────────────

    @cli.group()
    def worker() -> None:
        """Manage workflow workers."""

    @worker.command("start")
    @click.option(
        "--concurrency",
        type=int,
        default=4,
        help="Max concurrent workflows",
    )
    @click.option(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Poll interval (seconds)",
    )
    @click.option(
        "--project-id", type=str, default=None, help="Project ID filter"
    )
    @click.option(
        "--pool",
        type=str,
        default="default",
        help="Worker pool (like Temporal task queue)",
    )
    @click.option(
        "--labels",
        type=str,
        default=None,
        help='Worker labels as JSON (e.g. \'{"zone":"eu","gpu":true}\')',
    )
    @click.option(
        "--sandbox-mode",
        type=click.Choice(["none", "per_tool", "per_run", "per_worker"]),
        default=None,
        help="Sandbox mode (overrides project environment default).",
    )
    @click.option(
        "--sandbox-backend",
        type=click.Choice(["docker", "null"]),
        default="docker",
        help="Sandbox backend implementation.",
    )
    @click.option(
        "--sandbox-image",
        default=None,
        help="Default sandbox image (e.g., ghcr.io/sagewai/sandbox-base:dev).",
    )
    @click.option(
        "--sandbox-network",
        type=click.Choice(["none", "egress_allowlist", "full"]),
        default="none",
        help="Network policy for sandboxes.",
    )
    @click.option(
        "--sandbox-cpu",
        type=float,
        default=2.0,
        show_default=True,
        help="CPU core limit per sandbox.",
    )
    @click.option(
        "--sandbox-mem",
        default="2g",
        show_default=True,
        help="Memory limit per sandbox (e.g., 2g, 512m).",
    )
    @click.option(
        "--sandbox-pids",
        type=int,
        default=128,
        show_default=True,
        help="Max PIDs per sandbox.",
    )
    @click.option(
        "--sandbox-disk",
        default="5g",
        show_default=True,
        help="Tmpfs disk limit per sandbox (e.g., 5g).",
    )
    @click.option(
        "--sandbox-image-variants",
        default=None,
        help="Comma-separated variant names this worker accepts "
             "(e.g., base,general,ml). Default: all variants in the SDK's manifest.",
    )
    @click.option(
        "--project-environment",
        type=click.Choice(["production", "staging", "development"]),
        default=None,
        help="Project environment hint (used for mode default selection).",
    )
    @click.option(
        "--model",
        type=str,
        default=None,
        help='Default model override (e.g. "ollama/llama3.2")',
    )
    @click.option(
        "--api-base",
        type=str,
        default=None,
        help="LLM API base URL (e.g. http://localhost:11434)",
    )
    @click.option(
        "--api-key",
        type=str,
        default=None,
        help="LLM API key",
    )
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def worker_start(
        concurrency: int,
        poll_interval: float,
        project_id: str | None,
        pool: str,
        labels: str | None,
        model: str | None,
        api_base: str | None,
        api_key: str | None,
        db_url: str | None,
        sandbox_mode: str | None,
        sandbox_backend: str,
        sandbox_image: str | None,
        sandbox_network: str,
        sandbox_cpu: float,
        sandbox_mem: str,
        sandbox_pids: int,
        sandbox_disk: str,
        sandbox_image_variants: str | None,
        project_environment: str | None,
    ) -> None:
        """Start a workflow worker with optional routing and credentials."""
        import json as _json

        # Validate --sandbox-image-variants early (before async / DB connection)
        from sagewai.sandbox.models import (
            NetworkPolicy,
            ResourceLimits,
            SandboxConfig,
            SandboxImageVariant,
            SandboxMode,
        )

        parsed_variants = None
        if sandbox_image_variants:
            try:
                parsed_variants = [
                    SandboxImageVariant(v.strip())
                    for v in sandbox_image_variants.split(",")
                    if v.strip()
                ]
            except ValueError as exc:
                raise click.BadParameter(
                    f"--sandbox-image-variants: {exc}. "
                    f"Valid: base, general, ml, ops, erp, ecommerce, api"
                )

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.worker import WorkflowWorker
            from sagewai.models.inference import InferenceParams
            from sagewai.models.worker import WorkerCredentials

            # Build credentials from CLI flags
            creds: WorkerCredentials | None = None
            if model or api_base or api_key:
                model_overrides = {}
                if model:
                    model_overrides["default"] = model
                inf_overrides = None
                if api_base or api_key:
                    inf_overrides = InferenceParams(
                        api_base=api_base,
                        api_key=api_key,
                    )
                creds = WorkerCredentials(
                    model_overrides=model_overrides,
                    inference_overrides=inf_overrides,
                )

            parsed_labels = _json.loads(labels) if labels else None

            sbox_config = SandboxConfig(
                mode=SandboxMode(sandbox_mode) if sandbox_mode else None,
                backend=sandbox_backend,
                default_image=sandbox_image or "ghcr.io/sagewai/sandbox-base:dev",
                network_policy=NetworkPolicy(sandbox_network),
                resource_limits=ResourceLimits(
                    cpu=sandbox_cpu,
                    mem_bytes=_parse_size(sandbox_mem),
                    pids=sandbox_pids,
                    disk_bytes=_parse_size(sandbox_disk),
                ),
                image_variants=parsed_variants,
            )

            # Wire kubernetes-backend config from admin state (Plan SBX-K8S, T31)
            sandbox_kubernetes_config: dict | None = None
            if sandbox_backend == "kubernetes":
                import os as _os
                from pathlib import Path as _Path

                from sagewai.admin.state_file import AdminStateFile

                _state_path = _Path(
                    _os.environ.get("SAGEWAI_ADMIN_STATE")
                    or _os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
                    or (_Path.home() / ".sagewai" / "admin-state.json")
                )
                if _state_path.exists():
                    sandbox_kubernetes_config = AdminStateFile(
                        path=_state_path,
                    ).get_kubernetes_backend_config()

            w = WorkflowWorker(
                store=store,
                workflow_registry={},
                max_concurrent=concurrency,
                poll_interval=poll_interval,
                project_id=project_id,
                pool=pool,
                labels=parsed_labels,
                credentials=creds,
                sandbox_config=sbox_config,
                sandbox_kubernetes_config=sandbox_kubernetes_config,
                project_environment=project_environment,
            )
            click.echo(
                f"Worker starting (id={w.worker_id}, pool={pool}, "
                f"concurrency={concurrency}, poll={poll_interval}s)"
            )
            if creds:
                click.echo(
                    f"  Credentials: model={model or 'default'}, "
                    f"api_base={api_base or 'default'}"
                )
            await w.start()

        _run_async(_run())

    @worker.command("status")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def worker_status(db_url: str | None) -> None:
        """Show active worker status with pool and load info."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            workers = await monitor.get_worker_status()
            if not workers:
                click.echo("No active workers.")
                return
            click.echo(
                f"{'WORKER ID':<30} {'POOL':<15} "
                f"{'ACTIVE':<8} {'MAX':<5} {'LAST HEARTBEAT':<25}"
            )
            click.echo("-" * 83)
            for w in workers:
                click.echo(
                    f"{w.owner_id:<30} {w.pool:<15} "
                    f"{w.active_runs:<8} {w.max_concurrent:<5} "
                    f"{w.last_heartbeat:<25}"
                )

        _run_async(_run())

    @worker.command("list")
    @click.option(
        "--pool", type=str, default=None, help="Filter by pool"
    )
    @click.option(
        "--status",
        type=click.Choice(["active", "offline"]),
        default=None,
        help="Filter by status",
    )
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def worker_list(
        pool: str | None, status: str | None, db_url: str | None
    ) -> None:
        """List all registered workers."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            workers = await monitor.list_workers(
                pool=pool, status=status
            )
            if not workers:
                click.echo("No registered workers.")
                return
            click.echo(
                f"{'WORKER ID':<30} {'POOL':<15} {'STATUS':<10} "
                f"{'ACTIVE':<8} {'MAX':<5}"
            )
            click.echo("-" * 68)
            for w in workers:
                click.echo(
                    f"{w.owner_id:<30} {w.pool:<15} {w.status:<10} "
                    f"{w.active_runs:<8} {w.max_concurrent:<5}"
                )

        _run_async(_run())

    @worker.command("pools")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def worker_pools(db_url: str | None) -> None:
        """List worker pools with capacity info."""

        async def _run() -> None:
            store = await _get_store(db_url)
            if not hasattr(store, "list_worker_pools"):
                click.echo("Worker pools not available.")
                return
            pools = await store.list_worker_pools()
            if not pools:
                click.echo("No worker pools.")
                return
            click.echo(
                f"{'POOL':<20} {'WORKERS':<10} {'TOTAL CAPACITY':<15}"
            )
            click.echo("-" * 45)
            for p in pools:
                click.echo(
                    f"{p['pool']:<20} {p['worker_count']:<10} "
                    f"{p['total_capacity']:<15}"
                )

        _run_async(_run())

    # ── DLQ commands ─────────────────────────────────────────────

    @cli.group()
    def dlq() -> None:
        """Manage the dead letter queue."""

    @dlq.command("list")
    @click.option("--limit", type=int, default=20, help="Max results")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def dlq_list(limit: int, db_url: str | None) -> None:
        """List dead letter queue entries."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.dlq import DeadLetterQueue

            q = DeadLetterQueue(store=store)
            entries = await q.list_entries(limit=limit)
            if not entries:
                click.echo("DLQ is empty.")
                return
            click.echo(
                f"{'RUN ID':<25} {'WORKFLOW':<25} "
                f"{'RETRIES':<10} {'ERROR':<30}"
            )
            click.echo("-" * 90)
            for e in entries:
                error_short = (
                    (e.error[:27] + "...")
                    if len(e.error) > 30
                    else e.error
                )
                click.echo(
                    f"{e.run_id:<25} {e.workflow_name:<25} "
                    f"{e.retry_count:<10} {error_short:<30}"
                )

        _run_async(_run())

    @dlq.command("retry")
    @click.argument("run_id")
    @click.option(
        "--priority", type=int, default=0, help="Priority for retry"
    )
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def dlq_retry(run_id: str, priority: int, db_url: str | None) -> None:
        """Retry a DLQ entry."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.dlq import DeadLetterQueue

            q = DeadLetterQueue(store=store)
            new_id = await q.retry(run_id, priority=priority)
            click.echo(f"Retried as: {new_id}")

        _run_async(_run())

    @dlq.command("purge")
    @click.option(
        "--older-than",
        type=int,
        default=30,
        help="Purge entries older than N days",
    )
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def dlq_purge(older_than: int, db_url: str | None) -> None:
        """Purge old DLQ entries."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.dlq import DeadLetterQueue

            q = DeadLetterQueue(store=store)
            count = await q.purge(older_than_days=older_than)
            click.echo(
                f"Purged {count} entries older than {older_than} days."
            )

        _run_async(_run())

    # ── DB stats command ─────────────────────────────────────────

    @db_group.command("stats")
    @click.option(
        "--db-url", envvar="DATABASE_URL", help="PostgreSQL connection URL"
    )
    def db_stats(db_url: str | None) -> None:
        """Show queue statistics."""

        async def _run() -> None:
            store = await _get_store(db_url)
            from sagewai.core.monitor import WorkflowMonitor

            monitor = WorkflowMonitor(store=store)
            stats = await monitor.get_queue_stats()
            click.echo(f"Pending:   {stats.pending}")
            click.echo(f"Running:   {stats.running}")
            click.echo(f"Completed: {stats.completed}")
            click.echo(f"Failed:    {stats.failed}")
            click.echo(f"Waiting:   {stats.waiting}")
            click.echo(f"Total:     {stats.total}")

        _run_async(_run())
