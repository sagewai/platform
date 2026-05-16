# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""DockerBackend — per-sandbox Docker container with a tool-runner PID 1.

Each sandbox is one ``docker run sagewai/sandbox-<variant>``. Tool calls are
fresh ``docker exec sagewai-tool-runner`` against the long-running idle
container; each exec reads one JSON-RPC request from stdin and writes one
response to stdout.

aiodocker (0.9.0) handles container lifecycle via the Engine HTTP API.
The exec step uses ``asyncio.create_subprocess_exec`` against the Docker CLI
because aiodocker 0.9.0 does not expose high-level exec/stream helpers and
manually handling the TCP-upgrade hijack is fragile.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import (
    BackendHealth,
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
    SandboxMode,
    SandboxStats,
    ToolCall,
    ToolResult,
)
from sagewai.sandbox.pool_protocol import PoolStrategy

logger = logging.getLogger(__name__)


class SandboxError(RuntimeError):
    """Raised when the sandbox cannot be safely started."""

_SAGEWAI_LABEL_PREFIX = "sagewai."

# aiodocker 0.9.0 defaults to Docker API v1.30; Docker Engine ≥ 26 requires
# at least v1.44 for some features.  v1.45 is widely supported.
_AIODOCKER_API_VERSION = "v1.45"


def _docker_bin() -> str:
    """Resolve the Docker CLI binary.

    Returns the path discovered by ``shutil.which`` (typically
    ``/usr/local/bin/docker`` from Docker Desktop, not a Podman shell
    alias).  Raises ``RuntimeError`` when not found.
    """
    path = shutil.which("docker")
    if path is None:
        raise RuntimeError(
            "docker CLI not found in PATH; install Docker Desktop or set DOCKER_HOST."
        )
    return path


class DockerSandboxHandle:
    """Live handle to a running sandbox container."""

    def __init__(
        self,
        *,
        client: Any,         # aiodocker.Docker
        container: Any,      # aiodocker.containers.DockerContainer
        image: str,
        image_digest: str,
        sandbox_id: str,
        docker_bin: str,
    ) -> None:
        self._client = client
        self._container = container
        self.image = image
        self.image_digest = image_digest
        self.sandbox_id = sandbox_id
        self._docker_bin = docker_bin
        self.mode = SandboxMode.PER_RUN  # pool adjusts for other lifetimes
        self._exec_env: dict[str, str] = {}

    async def set_env(self, env: dict[str, str]) -> None:
        """Replace the exec-session env. The container's process env is not modified.

        Plan 1.5: Tier-2 env is per-exec, not per-container. cleanup_run between
        runs becomes ``set_env({})`` — an in-memory dict drop. See plan
        docs/superpowers/plans/2026-04-26-plan-1-5-sandbox-pooling.md for the
        rationale and the per-exec injection mechanics.
        """
        self._exec_env = dict(env)

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        """Run one tool call via ``docker exec sagewai-tool-runner``."""
        started = time.monotonic()
        req = {
            "jsonrpc": "2.0",
            "method": "exec",
            "params": {
                "tool": tool_call.tool,
                "args": tool_call.args,
                "call_id": tool_call.call_id,
                "timeout_s": tool_call.timeout_s,
            },
            "id": 1,
        }
        payload = (json.dumps(req) + "\n").encode()

        try:
            env_args: list[str] = []
            for k, v in self._exec_env.items():
                env_args.extend(["--env", f"{k}={v}"])

            proc = await asyncio.create_subprocess_exec(
                self._docker_bin,
                "exec",
                "-i",
                *env_args,
                self._container._id,
                "sagewai-tool-runner",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            deadline = tool_call.timeout_s + 10.0
            try:
                raw_out, raw_err = await asyncio.wait_for(
                    proc.communicate(payload), timeout=deadline
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return ToolResult(
                    call_id=tool_call.call_id,
                    ok=False,
                    error="sandbox response timeout",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            duration_ms = int((time.monotonic() - started) * 1000)

            if proc.returncode != 0 and not raw_out:
                return ToolResult(
                    call_id=tool_call.call_id,
                    ok=False,
                    error=f"docker exec exited {proc.returncode}: {raw_err.decode(errors='replace')[:256]}",
                    duration_ms=duration_ms,
                )

            # Parse the first JSON-RPC response line from stdout.
            for line in raw_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result = data.get("result") or {}
                return ToolResult(
                    call_id=tool_call.call_id,
                    ok=bool(result.get("ok")),
                    exit_code=result.get("exit_code"),
                    stdout=result.get("stdout", ""),
                    stderr=result.get("stderr", ""),
                    duration_ms=duration_ms,
                    error=result.get("error"),
                )

            return ToolResult(
                call_id=tool_call.call_id,
                ok=False,
                error="runner exited without a parseable response",
                stderr=raw_err.decode(errors="replace")[:512],
                duration_ms=duration_ms,
            )

        except Exception as exc:
            logger.debug("exec() failed for %s", self.sandbox_id, exc_info=True)
            return ToolResult(
                call_id=tool_call.call_id,
                ok=False,
                error=f"docker exec failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

    async def copy_in(self, src: Path, dst: PurePosixPath) -> None:
        raise NotImplementedError("copy_in lands in Plan 2")

    async def copy_out(self, src: PurePosixPath, dst: Path) -> None:
        raise NotImplementedError("copy_out lands in Plan 2")

    async def stats(self) -> SandboxStats:
        try:
            raw = await self._container.stats(stream=False)
            first = raw[0] if isinstance(raw, list) else raw
            return SandboxStats(
                cpu_percent=0.0,  # derived in Plan 3 observability work
                mem_bytes=int(first.get("memory_stats", {}).get("usage", 0)),
                disk_bytes=0,
                pids=int(first.get("pids_stats", {}).get("current", 0)),
            )
        except Exception:
            logger.debug("stats() failed for %s", self.sandbox_id, exc_info=True)
            return SandboxStats()

    async def stop(self, *, timeout: float = 10.0) -> None:
        try:
            await self._container.stop(timeout=int(timeout))
        except Exception:
            logger.debug("stop() failed for %s", self.sandbox_id, exc_info=True)
        try:
            await self._container.delete(force=True)
        except Exception:
            logger.debug("delete() failed for %s", self.sandbox_id, exc_info=True)


class DockerBackend:
    """Docker-backed sandbox implementation — OSS default."""

    name = "docker"
    pool_strategy = PoolStrategy.LOCAL_CACHE

    def __init__(self) -> None:
        import aiodocker

        self._client = aiodocker.Docker(api_version=_AIODOCKER_API_VERSION)
        self._docker_bin = _docker_bin()

    async def close(self) -> None:
        await self._client.close()

    async def health_check(self) -> BackendHealth:
        try:
            resp = await self._client._query("version")
            data = await resp.json()
            return BackendHealth(
                ok=True,
                backend="docker",
                detail=f"daemon OK, engine={data.get('Version', '?')}",
            )
        except Exception as exc:
            return BackendHealth(ok=False, backend="docker", detail=str(exc))

    async def _inspect_image_digest(self, image_ref: str) -> str:
        """Resolve ``image_ref`` to its RepoDigest sha256 via `docker inspect`.

        Pulls the image first if necessary. Raises SandboxError if the image
        cannot be inspected after a pull attempt.
        """
        docker_bin = _docker_bin()
        pull = await asyncio.create_subprocess_exec(
            docker_bin, "pull", image_ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        await pull.communicate()
        # A non-zero pull exit code is tolerated: the image may be locally
        # built (e.g. :dev) and simply not available in a remote registry.
        # We proceed to inspect; if the image is absent locally, inspect
        # will also fail and we raise there.

        inspect = await asyncio.create_subprocess_exec(
            docker_bin, "inspect", "--format", "{{index .RepoDigests 0}}", image_ref,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await inspect.communicate()
        if inspect.returncode != 0:
            raise SandboxError(f"docker inspect {image_ref!r} failed")
        repo_digest = stdout.decode("utf-8").strip()
        if "@" not in repo_digest:
            raise SandboxError(
                f"image {image_ref!r} has no RepoDigest — push it first"
            )
        return repo_digest.split("@", 1)[1]

    async def verify_digest(
        self, *, image_ref: str, actual_digest: str
    ) -> None:
        """Enforce the manifest's digest pin for known refs.

        - Known ref (in manifest) + matching digest: silent pass.
        - Known ref + mismatching digest: raise SandboxError.
        - Unknown ref (BYO, :dev, third-party): INFO-log the unverified
          digest and return. Auditors can reconstruct what actually ran.
        """
        expected = image_manifest.lookup_digest(image_ref)
        if expected is None:
            logger.info(
                "unverified image %s (digest %s) — not in SDK manifest",
                image_ref,
                actual_digest,
            )
            return
        if expected != actual_digest:
            raise SandboxError(
                f"digest mismatch for {image_ref!r}: "
                f"expected {expected}, got {actual_digest}"
            )

    async def probe_runner(self, handle) -> str:
        """Run `sagewai-tool-runner --version` in the sandbox; validate against
        ``image_manifest.TOOL_RUNNER_VERSION_SPEC``.

        Returns the reported version string on success. Raises SandboxError
        if the runner is missing, unresponsive, or out of spec.
        """
        docker_bin = _docker_bin()
        proc = await asyncio.create_subprocess_exec(
            docker_bin, "exec", handle.sandbox_id,
            "sagewai-tool-runner", "--version",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except asyncio.TimeoutError:
            raise SandboxError("tool-runner probe timeout (10s)")
        if proc.returncode != 0:
            raise SandboxError(
                f"tool-runner probe failed (exit={proc.returncode}): "
                f"{stderr.decode('utf-8', errors='replace').strip()}"
            )

        version_str = stdout.decode("utf-8").strip()
        try:
            version = Version(version_str)
        except Exception as exc:
            raise SandboxError(
                f"tool-runner returned unparseable version {version_str!r}"
            ) from exc

        spec = SpecifierSet(image_manifest.TOOL_RUNNER_VERSION_SPEC)
        if version not in spec:
            raise SandboxError(
                f"tool-runner version {version_str} does not satisfy {spec} — "
                f"rebuild the image against the current SDK"
            )
        return version_str

    async def start(
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        image_digest: str,
        env: Mapping[str, str],
        network_policy: NetworkPolicy,
        resource_limits: ResourceLimits,
        workdir_mount: Path | None,
        lifetime: SandboxLifetime,
    ) -> DockerSandboxHandle:
        sandbox_id = f"sgw-{uuid.uuid4().hex[:12]}"
        binds: list[str] = []
        if workdir_mount is not None:
            workdir_mount.mkdir(parents=True, exist_ok=True)
            binds.append(f"{workdir_mount}:/workspace")

        network_mode = "none" if network_policy is NetworkPolicy.NONE else "bridge"

        host_config: dict[str, Any] = {
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "NetworkMode": network_mode,
            "Memory": resource_limits.mem_bytes,
            "NanoCpus": int(resource_limits.cpu * 1_000_000_000),
            "PidsLimit": resource_limits.pids,
            "ReadonlyRootfs": True,
            "Binds": binds,
            "Tmpfs": {"/tmp": "size=512m"},
        }

        container_config: dict[str, Any] = {
            "Image": image,
            "Env": [],
            "Tty": False,
            "WorkingDir": "/workspace",
            "Labels": {
                f"{_SAGEWAI_LABEL_PREFIX}project_id": project_id,
                f"{_SAGEWAI_LABEL_PREFIX}run_id": run_id,
                f"{_SAGEWAI_LABEL_PREFIX}sandbox_id": sandbox_id,
                f"{_SAGEWAI_LABEL_PREFIX}lifetime": lifetime.value,
                f"{_SAGEWAI_LABEL_PREFIX}image": image,
                f"{_SAGEWAI_LABEL_PREFIX}started_at": datetime.now(
                    timezone.utc
                ).isoformat(),
            },
            "HostConfig": host_config,
        }

        container = await self._client.containers.create(
            config=container_config, name=sandbox_id
        )
        await container.start()
        logger.info(
            "sandbox started id=%s image=%s run=%s", sandbox_id, image, run_id
        )

        handle = DockerSandboxHandle(
            client=self._client,
            container=container,
            image=image,
            image_digest=image_digest,
            sandbox_id=sandbox_id,
            docker_bin=self._docker_bin,
        )
        await handle.set_env(dict(env))
        return handle

    async def reap(self, *, older_than: timedelta) -> int:
        """Force-remove sandboxes whose ``started_at`` label predates the cutoff."""
        cutoff = datetime.now(timezone.utc) - older_than
        filters_json = json.dumps(
            {"label": [f"{_SAGEWAI_LABEL_PREFIX}sandbox_id"]}
        )
        containers = await self._client._query_json(
            "containers/json",
            params={"all": "1", "filters": filters_json},
        )
        killed = 0
        for cdata in containers:
            cid = cdata.get("Id", "")
            try:
                labels: dict[str, str] = (
                    cdata.get("Labels") or {}
                )
                started_at_raw = labels.get(
                    f"{_SAGEWAI_LABEL_PREFIX}started_at"
                )
                if not started_at_raw:
                    continue
                started_at = datetime.fromisoformat(started_at_raw)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
                if started_at > cutoff:
                    continue

                container = self._client.containers.container(cid)
                try:
                    await container.stop(timeout=5)
                except Exception:
                    pass
                await container.delete(force=True)
                killed += 1
                logger.info(
                    "reaped orphan sandbox %s (started_at=%s)",
                    labels.get(f"{_SAGEWAI_LABEL_PREFIX}sandbox_id"),
                    started_at_raw,
                )
            except Exception:
                logger.debug(
                    "reap failed for container %s", cid[:12], exc_info=True
                )
        return killed
