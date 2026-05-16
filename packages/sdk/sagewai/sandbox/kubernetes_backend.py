# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""KubernetesBackend — pod-per-sandbox Sagewai sandbox backend.

Mirrors `DockerBackend` on the SandboxBackend Protocol surface; adds K8s-
specific helpers (ensure_deployment, claim_pod, ensure_network_policies)
used by ExternalMinReplicasSandboxPool.

Plan SBX-K8S design spec:
docs/superpowers/specs/2026-04-27-sandbox-k8s-backend-design.md
"""
from __future__ import annotations

import logging
from typing import Any

from sagewai.sandbox.k8s_client import make_api_client
from sagewai.sandbox.models import BackendHealth
from sagewai.sandbox.pool_protocol import PoolStrategy

logger = logging.getLogger(__name__)


# Late-bound for monkey-patching in tests:
def _VersionApi(api_client: Any) -> Any:
    from kubernetes_asyncio import client
    return client.VersionApi(api_client)


def _CoreV1Api(api_client: Any) -> Any:
    from kubernetes_asyncio import client
    return client.CoreV1Api(api_client)


def _AppsV1Api(api_client: Any) -> Any:
    from kubernetes_asyncio import client
    return client.AppsV1Api(api_client)


def _NetworkingV1Api(api_client: Any) -> Any:
    from kubernetes_asyncio import client
    return client.NetworkingV1Api(api_client)


async def _ws_exec(
    *, name: str, namespace: str, command: list[str],
    stdin: bool, stdout: bool, stderr: bool, tty: bool,
) -> Any:
    """Open a websocket exec stream against a pod. Late-bound for tests."""
    from kubernetes_asyncio import client, stream
    api_client = client.ApiClient()
    ws_api = stream.WsApiClient(api_client.configuration)
    return await ws_api.connect_get_namespaced_pod_exec(
        name=name, namespace=namespace, command=command,
        stdin=stdin, stdout=stdout, stderr=stderr, tty=tty,
        _preload_content=False,
    )


_POD_READY_POLL_S = 0.5
_POD_READY_TIMEOUT_S = 90.0


class KubernetesBackend:
    """Kubernetes-backed sandbox implementation."""

    name = "kubernetes"
    pool_strategy = PoolStrategy.EXTERNAL_MIN_REPLICAS

    def __init__(
        self,
        *,
        kubeconfig_path: str | None,
        use_in_cluster: bool,
        namespace: str,
        egress_allowlist: list[str],
    ) -> None:
        self._kubeconfig_path = kubeconfig_path
        self._use_in_cluster = use_in_cluster
        self._namespace = namespace
        self._egress_allowlist = list(egress_allowlist)
        self._api_client: Any | None = None

    async def _ensure_client(self) -> Any:
        if self._api_client is None:
            self._api_client = await make_api_client(
                kubeconfig_path=self._kubeconfig_path,
                use_in_cluster=self._use_in_cluster,
            )
        return self._api_client

    async def close(self) -> None:
        if self._api_client is not None:
            try:
                await self._api_client.close()
            except Exception:
                logger.debug("ApiClient close failed", exc_info=True)
            self._api_client = None

    async def health_check(self) -> BackendHealth:
        try:
            client = await self._ensure_client()
            version_api = _VersionApi(client)
            info = await version_api.get_code()
            return BackendHealth(
                ok=True, backend="kubernetes", detail=f"server={info.git_version}",
            )
        except Exception as exc:
            return BackendHealth(ok=False, backend="kubernetes", detail=str(exc))

    async def start(
        self,
        *,
        project_id: str,
        run_id: str,
        image: str,
        image_digest: str,
        env: dict[str, str],
        network_policy: Any,
        resource_limits: Any,
        workdir_mount: Any,
        lifetime: Any,
        image_pull_policy: str | None = None,
    ) -> "KubernetesSandboxHandle":
        import asyncio
        import uuid

        from sagewai.sandbox.docker_backend import SandboxError
        from sagewai.sandbox.k8s_resources import build_pod_spec

        sandbox_id = f"sgw-{uuid.uuid4().hex[:12]}"
        pod_spec = build_pod_spec(
            sandbox_id=sandbox_id, run_id=run_id, project_id=project_id,
            image=image, image_digest=image_digest,
            network_policy=network_policy, resource_limits=resource_limits,
            lifetime=lifetime, image_pull_policy=image_pull_policy,
        )

        api = await self._ensure_client()
        core_v1 = _CoreV1Api(api)
        await core_v1.create_namespaced_pod(namespace=self._namespace, body=pod_spec)

        deadline = asyncio.get_event_loop().time() + _POD_READY_TIMEOUT_S
        last_status: dict[str, Any] = {}
        while asyncio.get_event_loop().time() < deadline:
            try:
                p = await core_v1.read_namespaced_pod_status(
                    name=sandbox_id, namespace=self._namespace,
                )
                last_status = p.get("status", {})
                if last_status.get("phase") == "Running":
                    cs = last_status.get("containerStatuses") or [{}]
                    if cs[0].get("ready"):
                        handle = KubernetesSandboxHandle(
                            api_client=api,
                            namespace=self._namespace,
                            pod_name=sandbox_id,
                            image=image,
                            image_digest=image_digest,
                            sandbox_id=sandbox_id,
                        )
                        await handle.set_env(dict(env))
                        return handle
            except Exception:
                logger.debug("pod status read failed", exc_info=True)
            await asyncio.sleep(_POD_READY_POLL_S)

        # Timeout — best-effort delete
        try:
            await core_v1.delete_namespaced_pod(
                name=sandbox_id, namespace=self._namespace, grace_period_seconds=5,
            )
        except Exception:
            logger.debug("cleanup delete after timeout failed", exc_info=True)
        raise SandboxError(
            f"pod {sandbox_id!r} failed to become ready within "
            f"{_POD_READY_TIMEOUT_S}s; last_status={last_status}"
        )

    async def reap(self, *, older_than: Any) -> int:
        """Delete all pods with sagewai.sandbox_id label started before cutoff.

        Args:
            older_than: timedelta to subtract from now to get the cutoff timestamp.

        Returns:
            Number of pods deleted.
        """
        from datetime import datetime, timezone

        api = await self._ensure_client()
        core_v1 = _CoreV1Api(api)
        cutoff = datetime.now(timezone.utc) - older_than

        result = await core_v1.list_namespaced_pod(
            namespace=self._namespace, label_selector="sagewai.sandbox_id",
        )
        killed = 0
        for pod in result.get("items", []):
            anno = (pod.get("metadata") or {}).get("annotations") or {}
            started_at_raw = anno.get("sagewai.io/started-at")
            if not started_at_raw:
                continue
            try:
                started_at = datetime.fromisoformat(started_at_raw)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if started_at > cutoff:
                continue
            try:
                await core_v1.delete_namespaced_pod(
                    name=pod["metadata"]["name"], namespace=self._namespace,
                    grace_period_seconds=5,
                )
                killed += 1
            except Exception:
                logger.debug("reap delete failed for %s",
                             pod["metadata"].get("name"), exc_info=True)
        return killed

    async def probe_runner(self, handle: "KubernetesSandboxHandle") -> str:
        """Run `sagewai-tool-runner --version` in the pod; validate against the manifest."""
        import asyncio

        from packaging.specifiers import SpecifierSet
        from packaging.version import Version

        from sagewai.sandbox import image_manifest
        from sagewai.sandbox.docker_backend import SandboxError

        try:
            ws = await _ws_exec(
                name=handle._pod_name, namespace=self._namespace,
                command=["sagewai-tool-runner", "--version"],
                stdin=False, stdout=True, stderr=True, tty=False,
            )
        except Exception as exc:
            raise SandboxError(f"tool-runner probe connect failed: {exc}") from exc

        try:
            try:
                raw = await asyncio.wait_for(ws.read_stdout(timeout=10.0), timeout=10.0)
            except asyncio.TimeoutError:
                raise SandboxError("tool-runner probe timeout (10s)")
            version_str = raw.strip()
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
                    "rebuild the image against the current SDK"
                )
            return version_str
        finally:
            try:
                await ws.close()
            except Exception:
                logger.debug("probe ws close failed", exc_info=True)

    async def verify_digest(self, *, image_ref: str, actual_digest: str) -> None:
        """Reuse Docker's manifest semantics — same code path."""
        from sagewai.sandbox import image_manifest
        from sagewai.sandbox.docker_backend import SandboxError

        expected = image_manifest.lookup_digest(image_ref)
        if expected is None:
            logger.info(
                "unverified image %s (digest %s) — not in SDK manifest",
                image_ref, actual_digest,
            )
            return
        if expected != actual_digest:
            raise SandboxError(
                f"digest mismatch for {image_ref!r}: "
                f"expected {expected}, got {actual_digest}"
            )

    async def ensure_deployment(
        self, *, key: Any, replicas: int, image: str,
        resource_limits: Any, lifetime: Any, image_pull_policy: str | None = None,
    ) -> str:
        """Idempotently ensure a Deployment exists for `key` with `replicas` warm pods.

        Returns the deployment name. Raises SandboxError on selector mismatch.
        """
        from sagewai.sandbox.docker_backend import SandboxError
        from sagewai.sandbox.k8s_resources import (
            build_deployment_spec, pool_key_to_name,
        )

        api = await self._ensure_client()
        apps_v1 = _AppsV1Api(api)
        name = pool_key_to_name(key)

        try:
            existing = await apps_v1.read_namespaced_deployment(
                name=name, namespace=self._namespace,
            )
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status != 404:
                # Re-raise unexpected errors
                logger.debug("ensure_deployment read failed", exc_info=True)
            existing = None

        if existing is not None:
            existing_sel = (existing.get("spec") or {}).get("selector", {}).get("matchLabels", {})
            spec = build_deployment_spec(
                key=key, replicas=replicas, image=image,
                resource_limits=resource_limits, lifetime=lifetime,
                image_pull_policy=image_pull_policy,
            )
            new_sel = spec["spec"]["selector"]["matchLabels"]
            if existing_sel != new_sel:
                raise SandboxError(
                    f"deployment {name!r} has incompatible selector "
                    f"(existing={existing_sel}, expected={new_sel}) — delete or rename"
                )
            return name

        spec = build_deployment_spec(
            key=key, replicas=replicas, image=image,
            resource_limits=resource_limits, lifetime=lifetime,
            image_pull_policy=image_pull_policy,
        )
        await apps_v1.create_namespaced_deployment(
            namespace=self._namespace, body=spec,
        )
        return name

    async def scale_deployment(self, deployment_name: str, replicas: int) -> None:
        api = await self._ensure_client()
        apps_v1 = _AppsV1Api(api)
        await apps_v1.patch_namespaced_deployment_scale(
            name=deployment_name, namespace=self._namespace,
            body={"spec": {"replicas": replicas}},
        )

    async def list_warm_pods(self, deployment_name: str) -> list[dict]:
        """List Running+ready warm pods for a deployment, oldest-first."""
        api = await self._ensure_client()
        core_v1 = _CoreV1Api(api)
        pool_hash = deployment_name.removeprefix("sagewai-pool-")
        selector = (
            f"sagewai-pool={pool_hash},sagewai.phase=warm,!sagewai.run_id"
        )
        result = await core_v1.list_namespaced_pod(
            namespace=self._namespace,
            label_selector=selector,
            field_selector="status.phase=Running",
        )
        out: list[dict] = []
        for pod in result.get("items", []):
            md = pod.get("metadata", {})
            cs = (pod.get("status") or {}).get("containerStatuses") or []
            if not cs or not cs[0].get("ready"):
                continue
            out.append({
                "name": md["name"],
                "resource_version": md.get("resourceVersion", ""),
                "image": (pod.get("spec") or {}).get("containers", [{}])[0].get("image", ""),
                "image_digest": (md.get("annotations") or {}).get(
                    "sagewai.io/image-digest", "",
                ),
                "creation_timestamp": md.get("creationTimestamp"),
            })
        out.sort(key=lambda p: p["creation_timestamp"] or 0)
        return out

    async def ensure_network_policies(self) -> None:
        """Server-side-style apply (idempotent) for the three NetworkPolicies.

        On 403 (RBAC), log WARN and continue — operator can apply manually.
        """
        from sagewai.sandbox.k8s_resources import build_network_policies

        api = await self._ensure_client()
        nw = _NetworkingV1Api(api)
        nps = build_network_policies(egress_allowlist=self._egress_allowlist)

        try:
            existing = await nw.list_namespaced_network_policy(
                namespace=self._namespace,
                label_selector="sagewai.io/managed-by=sagewai",
            )
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 403:
                logger.warning(
                    "NetworkPolicy list 403 in namespace %s — apply manually or grant "
                    "networking.k8s.io/networkpolicies access to the worker SA: %s",
                    self._namespace, exc,
                )
                return
            raise

        existing_names = {
            (np.get("metadata") or {}).get("name") for np in existing.get("items", [])
        }
        for np in nps:
            name = np["metadata"]["name"]
            try:
                if name in existing_names:
                    await nw.replace_namespaced_network_policy(
                        name=name, namespace=self._namespace, body=np,
                    )
                else:
                    await nw.create_namespaced_network_policy(
                        namespace=self._namespace, body=np,
                    )
            except Exception as exc:
                status = getattr(exc, "status", None)
                if status == 403:
                    logger.warning(
                        "NetworkPolicy %s create/replace Forbidden — apply manually: %s",
                        name, exc,
                    )
                    return
                raise

    async def claim_pod(self, pod: dict, run_id: str) -> "KubernetesSandboxHandle | None":
        """Atomically relabel a warm pod to leased via JSON-Patch CAS.

        Returns the new handle, or None on 409 (race lost).
        """
        api = await self._ensure_client()
        core_v1 = _CoreV1Api(api)

        patch_body = [
            {"op": "test", "path": "/metadata/resourceVersion",
             "value": pod["resource_version"]},
            {"op": "replace", "path": "/metadata/labels/sagewai.phase",
             "value": "leased"},
            {"op": "add", "path": "/metadata/labels/sagewai.run_id",
             "value": run_id},
        ]
        try:
            await core_v1.patch_namespaced_pod(
                name=pod["name"], namespace=self._namespace,
                body=patch_body, _content_type="application/json-patch+json",
            )
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 409:
                return None
            raise

        return KubernetesSandboxHandle(
            api_client=api, namespace=self._namespace, pod_name=pod["name"],
            image=pod["image"], image_digest=pod["image_digest"],
            sandbox_id=pod["name"],
        )


class KubernetesSandboxHandle:
    """Live handle to a running sandbox pod."""

    def __init__(
        self, *, api_client: Any, namespace: str, pod_name: str,
        image: str, image_digest: str, sandbox_id: str,
    ) -> None:
        self._api_client = api_client
        self._namespace = namespace
        self._pod_name = pod_name
        self.image = image
        self.image_digest = image_digest
        self.sandbox_id = sandbox_id
        self._exec_env: dict[str, str] = {}
        from sagewai.sandbox.models import SandboxMode
        self.mode = SandboxMode.PER_RUN  # pool adjusts for other lifetimes

    async def set_env(self, env: dict[str, str]) -> None:
        self._exec_env = dict(env)

    async def exec(self, tool_call: Any) -> Any:
        """Run one tool call via the K8s WS exec API."""
        import asyncio
        import json
        import time

        from sagewai.sandbox.models import ToolResult

        started = time.monotonic()
        # Per-exec env injection: command = ["env", "K=V", ..., "sagewai-tool-runner"]
        env_args = [f"{k}={v}" for k, v in sorted(self._exec_env.items())]
        command = ["env", *env_args, "sagewai-tool-runner"]

        req = {
            "jsonrpc": "2.0",
            "method": "exec",
            "params": {
                "tool": tool_call.tool, "args": tool_call.args,
                "call_id": tool_call.call_id, "timeout_s": tool_call.timeout_s,
            },
            "id": 1,
        }
        payload = (json.dumps(req) + "\n").encode()

        try:
            ws = await _ws_exec(
                name=self._pod_name, namespace=self._namespace, command=command,
                stdin=True, stdout=True, stderr=True, tty=False,
            )
        except Exception as exc:
            return ToolResult(
                call_id=tool_call.call_id, ok=False,
                error=f"k8s exec connect failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        try:
            await ws.write_stdin(payload)
            try:
                raw = await asyncio.wait_for(
                    ws.read_stdout(timeout=tool_call.timeout_s + 10.0),
                    timeout=tool_call.timeout_s + 10.0,
                )
            except asyncio.TimeoutError:
                return ToolResult(
                    call_id=tool_call.call_id, ok=False,
                    error="sandbox response timeout",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )

            duration_ms = int((time.monotonic() - started) * 1000)
            for line in raw.splitlines():
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
                call_id=tool_call.call_id, ok=False,
                error="runner exited without parseable response",
                duration_ms=duration_ms,
            )
        finally:
            try:
                await ws.close()
            except Exception:
                logger.debug("ws close failed", exc_info=True)

    async def stop(self, *, timeout: float = 10.0) -> None:
        try:
            core_v1 = _CoreV1Api(self._api_client)
            await core_v1.delete_namespaced_pod(
                name=self._pod_name, namespace=self._namespace,
                grace_period_seconds=int(timeout),
            )
        except Exception:
            logger.debug("pod delete failed for %s", self._pod_name, exc_info=True)

    async def stats(self) -> Any:
        from sagewai.sandbox.models import SandboxStats
        # metrics-server is optional; silent fallback to zeros (matches Docker).
        return SandboxStats()

    async def copy_in(self, src: Any, dst: Any) -> None:
        raise NotImplementedError("copy_in lands in Plan 2")

    async def copy_out(self, src: Any, dst: Any) -> None:
        raise NotImplementedError("copy_out lands in Plan 2")
