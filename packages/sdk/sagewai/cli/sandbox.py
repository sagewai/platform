# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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

        # Kubernetes — Plan SBX-K8S
        try:
            import os
            from pathlib import Path

            from sagewai.admin.state_file import AdminStateFile

            state_path = Path(
                os.environ.get("SAGEWAI_ADMIN_STATE")
                or os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
                or (Path.home() / ".sagewai" / "admin-state.json")
            )
            sf = AdminStateFile(path=state_path)
            cfg = sf.get_kubernetes_backend_config()
            from sagewai.sandbox.kubernetes_backend import KubernetesBackend

            kb = KubernetesBackend(
                kubeconfig_path=cfg["kubeconfig_path"],
                use_in_cluster=cfg["use_in_cluster"],
                namespace=cfg["namespace"],
                egress_allowlist=cfg["egress_allowlist"],
            )
            kh = await kb.health_check()
            click.echo(f"kubernetes: ok={kh.ok} {kh.detail}")
            await kb.close()
        except ImportError as exc:
            click.echo(
                f"kubernetes: ok=False (extra not installed: pip install sagewai[kubernetes]) — {exc}"
            )
        except Exception as exc:
            click.echo(f"kubernetes: ok=False (probe failed: {exc})")

    asyncio.run(_run())


@sandbox_cli.group("config")
def sandbox_config_cli() -> None:
    """Configure sandbox backends (kubernetes, etc.)."""


@sandbox_config_cli.command("k8s")
@click.option("--kubeconfig", "kubeconfig_path", default=None,
              help="Explicit kubeconfig path (overrides default chain).")
@click.option("--namespace", default="sagewai", show_default=True,
              help="Kubernetes namespace for sandbox pods.")
@click.option("--egress-allowlist", default="",
              help="Comma-separated CIDRs for EGRESS_ALLOWLIST policy.")
@click.option("--use-in-cluster/--no-use-in-cluster", default=True,
              show_default=True,
              help="Auto-detect in-cluster service account when /var/run/secrets exists.")
@click.option("--verify", is_flag=True, default=False,
              help="Run a health_check after writing config.")
def sandbox_config_k8s(
    kubeconfig_path: str | None,
    namespace: str,
    egress_allowlist: str,
    use_in_cluster: bool,
    verify: bool,
) -> None:
    """Write KubernetesBackend config to ~/.sagewai/admin-state.json."""
    import os
    from pathlib import Path

    from sagewai.admin.state_file import AdminStateFile

    state_path = Path(
        os.environ.get("SAGEWAI_ADMIN_STATE")
        or os.environ.get("SAGEWAI_ADMIN_STATE_FILE")
        or (Path.home() / ".sagewai" / "admin-state.json")
    )
    state_path.parent.mkdir(parents=True, exist_ok=True)

    cidrs = [c.strip() for c in egress_allowlist.split(",") if c.strip()]
    sf = AdminStateFile(path=state_path)
    sf.set_kubernetes_backend_config(
        kubeconfig_path=kubeconfig_path,
        namespace=namespace,
        egress_allowlist=cidrs,
        use_in_cluster=use_in_cluster,
    )
    click.echo(f"wrote sandbox_backends.kubernetes to {state_path}")

    if verify:
        from sagewai.sandbox.kubernetes_backend import KubernetesBackend

        async def _verify() -> None:
            backend = KubernetesBackend(
                kubeconfig_path=kubeconfig_path,
                use_in_cluster=use_in_cluster,
                namespace=namespace,
                egress_allowlist=cidrs,
            )
            try:
                health = await backend.health_check()
                click.echo(f"health: ok={health.ok} {health.detail}")
            finally:
                await backend.close()

        asyncio.run(_verify())


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


@sandbox_cli.command("validate")
@click.argument("image")
def sandbox_validate(image: str) -> None:
    """Pull ``image``, probe the tool-runner, and run a bash round-trip.

    Checks:
      - image pulls and has a RepoDigest
      - container starts
      - sagewai-tool-runner --version is in the SDK's accepted spec
      - bash echo round-trip returns the expected value
      - /workspace is writable
      - env scrub: host HOST_SECRET is not visible in the sandbox
    """
    import os
    import tempfile
    from pathlib import Path

    async def _run() -> None:
        from sagewai.sandbox.docker_backend import DockerBackend, SandboxError
        from sagewai.sandbox.models import (
            NetworkPolicy,
            ResourceLimits,
            SandboxLifetime,
            ToolCall,
        )

        backend = DockerBackend()
        failures: list[str] = []
        workdir = Path(tempfile.mkdtemp(prefix="sagewai-validate-"))
        prev_host_secret = os.environ.get("HOST_SECRET")
        os.environ.setdefault("HOST_SECRET", "leak-me-please")

        try:
            try:
                digest = await backend._inspect_image_digest(image)
                click.echo(f"  \u2713 image pulled (digest {digest})")
            except SandboxError as exc:
                click.echo(f"  \u2717 image pull/inspect failed: {exc}", err=True)
                failures.append("pull")
                return

            handle = await backend.start(
                project_id="validate",
                run_id="r-validate",
                image=image,
                image_digest=digest,
                env={"PROJECT_ONLY": "ok"},
                network_policy=NetworkPolicy.NONE,
                resource_limits=ResourceLimits(),
                workdir_mount=workdir,
                lifetime=SandboxLifetime.PER_RUN,
            )
            try:
                click.echo("  \u2713 container starts")

                try:
                    version = await backend.probe_runner(handle)
                    click.echo(
                        f"  \u2713 sagewai-tool-runner --version \u2192 {version} (compatible)"
                    )
                except SandboxError as exc:
                    click.echo(f"  \u2717 runner probe failed: {exc}", err=True)
                    failures.append("probe")

                # bash round-trip
                r = await handle.exec(
                    ToolCall(
                        tool="bash",
                        args={"command": "echo sagewai-ok"},
                        call_id="v1",
                        timeout_s=10,
                    )
                )
                if r.ok and r.stdout.strip() == "sagewai-ok":
                    click.echo(f"  \u2713 bash echo round-trip \u2192 {r.duration_ms} ms")
                else:
                    click.echo(f"  \u2717 bash round-trip broken: {r}", err=True)
                    failures.append("bash")

                # writable workspace
                r = await handle.exec(
                    ToolCall(
                        tool="bash",
                        args={
                            "command": "touch /workspace/x && test -f /workspace/x && echo ok"
                        },
                        call_id="v2",
                        timeout_s=10,
                    )
                )
                if r.ok and r.stdout.strip() == "ok":
                    click.echo("  \u2713 /workspace writable")
                else:
                    click.echo(f"  \u2717 /workspace not writable: {r}", err=True)
                    failures.append("workspace")

                # env scrub
                r = await handle.exec(
                    ToolCall(
                        tool="bash",
                        args={"command": 'echo "${HOST_SECRET:-absent}"'},
                        call_id="v3",
                        timeout_s=10,
                    )
                )
                if r.ok and r.stdout.strip() == "absent":
                    click.echo("  \u2713 env scrub: HOST_SECRET not visible")
                else:
                    click.echo(f"  \u2717 env leaked: {r.stdout!r}", err=True)
                    failures.append("env-scrub")
            finally:
                await handle.stop()
        finally:
            await backend.close()
            if prev_host_secret is None:
                os.environ.pop("HOST_SECRET", None)

        if failures:
            click.echo(
                f"\nFAIL \u2014 {len(failures)} check(s) failed: {', '.join(failures)}",
                err=True,
            )
            raise SystemExit(1)
        click.echo("\nPASS \u2014 image is compatible with this SDK")

    asyncio.run(_run())
