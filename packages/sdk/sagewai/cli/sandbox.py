# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""`sagewai sandbox` — runtime inspection and maintenance commands."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import click


@click.group("sandbox")
def sandbox_cli() -> None:
    """Sandbox runtime inspection and maintenance."""


@sandbox_cli.command("doctor")
def sandbox_doctor() -> None:
    """Report health of configured sandbox backend(s)."""

    async def _run() -> None:
        from sagewai.sandbox.null_backend import NullBackend

        null = NullBackend()
        nh = await null.health_check()
        click.echo(f"null: ok={nh.ok} {nh.detail}")

        try:
            from sagewai.sandbox.docker_backend import DockerBackend

            docker = DockerBackend()
            dh = await docker.health_check()
            click.echo(f"docker: ok={dh.ok} {dh.detail}")
            await docker.close()
        except Exception as exc:
            click.echo(f"docker: ok=False (import failed: {exc})")

    asyncio.run(_run())


@sandbox_cli.command("list")
def sandbox_list() -> None:
    """List live sandboxes on this host (Docker)."""

    async def _run() -> None:
        try:
            import aiodocker
        except Exception as exc:
            click.echo(f"aiodocker not installed: {exc}", err=True)
            raise SystemExit(1)
        client = aiodocker.Docker()
        try:
            containers = await client.containers.list(
                all=False, filters={"label": ["sagewai.sandbox_id"]}
            )
            if not containers:
                click.echo("(no live sandboxes)")
                return
            click.echo("SANDBOX_ID\tRUN_ID\tPROJECT_ID\tIMAGE")
            for c in containers:
                details = await c.show()
                labels = details.get("Config", {}).get("Labels", {}) or {}
                click.echo(
                    "\t".join(
                        [
                            labels.get("sagewai.sandbox_id", ""),
                            labels.get("sagewai.run_id", ""),
                            labels.get("sagewai.project_id", ""),
                            labels.get("sagewai.image", ""),
                        ]
                    )
                )
        finally:
            await client.close()

    asyncio.run(_run())


@sandbox_cli.command("reap")
@click.option(
    "--older-than",
    default="10m",
    show_default=True,
    help="Reap sandboxes older than this (e.g., 10m, 1h).",
)
def sandbox_reap(older_than: str) -> None:
    """Force-kill orphaned sandboxes older than the cutoff."""

    def _parse_duration(s: str) -> timedelta:
        s = s.strip().lower()
        if s.endswith("h"):
            return timedelta(hours=float(s[:-1]))
        if s.endswith("m"):
            return timedelta(minutes=float(s[:-1]))
        if s.endswith("s"):
            return timedelta(seconds=float(s[:-1]))
        return timedelta(seconds=float(s))

    async def _run() -> None:
        from sagewai.sandbox.docker_backend import DockerBackend

        backend = DockerBackend()
        try:
            n = await backend.reap(older_than=_parse_duration(older_than))
            click.echo(f"reaped {n} sandbox(es)")
        finally:
            await backend.close()

    asyncio.run(_run())
