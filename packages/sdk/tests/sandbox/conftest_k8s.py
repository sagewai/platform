# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Shared fake-k8s fixture for unit tests.

Imported as a fixture by tests; not auto-discovered as a conftest because the
fixture is opt-in (some tests need the real client). To use:

    from tests.sandbox.conftest_k8s import fake_k8s
"""
from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

import pytest


class FakeApiException(Exception):
    """Mimics kubernetes_asyncio.client.exceptions.ApiException."""

    def __init__(self, status: int, reason: str = "", body: str = "") -> None:
        super().__init__(f"{status} {reason}")
        self.status = status
        self.reason = reason
        self.body = body


@dataclass
class FakePod:
    name: str
    namespace: str
    labels: dict[str, str] = field(default_factory=dict)
    annotations: dict[str, str] = field(default_factory=dict)
    phase: str = "Pending"     # "Pending" | "Running" | "Failed"
    ready: bool = False
    resource_version: str = field(default_factory=lambda: str(uuid.uuid4().int)[:8])
    creation_timestamp: float = 0.0
    spec: dict[str, Any] = field(default_factory=dict)


@dataclass
class FakeDeployment:
    name: str
    namespace: str
    selector: dict[str, str]
    replicas: int
    template_labels: dict[str, str]
    annotations: dict[str, str] = field(default_factory=dict)


class FakeCoreV1:
    """Subset of CoreV1Api used by KubernetesBackend / ExternalMinReplicasSandboxPool."""

    def __init__(self, pods: dict[str, FakePod], lock: asyncio.Lock) -> None:
        self._pods = pods
        self._lock = lock

    async def create_namespaced_pod(self, *, namespace: str, body: dict) -> dict:
        async with self._lock:
            name = body["metadata"]["name"]
            if name in self._pods:
                raise FakeApiException(409, "AlreadyExists", f"pod {name} exists")
            pod = FakePod(
                name=name,
                namespace=namespace,
                labels=dict(body["metadata"].get("labels", {})),
                annotations=dict(body["metadata"].get("annotations", {})),
                phase="Pending",
                spec=deepcopy(body["spec"]),
                creation_timestamp=asyncio.get_event_loop().time(),
            )
            self._pods[name] = pod
            return _pod_to_dict(pod)

    async def read_namespaced_pod_status(self, *, name: str, namespace: str) -> dict:
        async with self._lock:
            pod = self._pods.get(name)
            if pod is None:
                raise FakeApiException(404, "NotFound")
            return _pod_to_dict(pod)

    async def list_namespaced_pod(
        self, *, namespace: str, label_selector: str = "", field_selector: str = "",
    ) -> dict:
        async with self._lock:
            matching = [
                p for p in self._pods.values()
                if p.namespace == namespace
                and _label_selector_matches(p.labels, label_selector)
                and _field_selector_matches(p, field_selector)
            ]
            matching.sort(key=lambda p: p.creation_timestamp)
            return {"items": [_pod_to_dict(p) for p in matching]}

    async def patch_namespaced_pod(
        self, *, name: str, namespace: str, body: list[dict], _content_type: str = "",
    ) -> dict:
        """JSON-Patch (RFC 6902) only: supports `test` on resourceVersion + `replace`/`add` on labels."""
        async with self._lock:
            pod = self._pods.get(name)
            if pod is None:
                raise FakeApiException(404, "NotFound")
            for op in body:
                path = op["path"]
                kind = op["op"]
                if kind == "test":
                    if path == "/metadata/resourceVersion":
                        if pod.resource_version != op["value"]:
                            raise FakeApiException(409, "Conflict", "resourceVersion mismatch")
                    else:
                        raise FakeApiException(422, "UnsupportedTestPath", path)
                elif kind in ("replace", "add"):
                    if path.startswith("/metadata/labels/"):
                        key = path[len("/metadata/labels/"):]
                        pod.labels[key] = op["value"]
                    else:
                        raise FakeApiException(422, "UnsupportedPath", path)
                elif kind == "remove":
                    if path.startswith("/metadata/labels/"):
                        pod.labels.pop(path[len("/metadata/labels/"):], None)
                    else:
                        raise FakeApiException(422, "UnsupportedPath", path)
                else:
                    raise FakeApiException(422, "UnsupportedOp", kind)
            pod.resource_version = str(uuid.uuid4().int)[:8]
            return _pod_to_dict(pod)

    async def delete_namespaced_pod(
        self, *, name: str, namespace: str, grace_period_seconds: int = 30,
    ) -> dict:
        async with self._lock:
            self._pods.pop(name, None)
            return {"status": "Success"}


class FakeAppsV1:
    """Subset of AppsV1Api: deployment create/read/scale."""

    def __init__(
        self, deployments: dict[str, FakeDeployment], pods: dict[str, FakePod], lock: asyncio.Lock,
    ) -> None:
        self._deployments = deployments
        self._pods = pods
        self._lock = lock

    async def read_namespaced_deployment(self, *, name: str, namespace: str) -> dict:
        async with self._lock:
            d = self._deployments.get(name)
            if d is None:
                raise FakeApiException(404, "NotFound")
            return _deployment_to_dict(d)

    async def create_namespaced_deployment(self, *, namespace: str, body: dict) -> dict:
        async with self._lock:
            name = body["metadata"]["name"]
            if name in self._deployments:
                raise FakeApiException(409, "AlreadyExists")
            d = FakeDeployment(
                name=name,
                namespace=namespace,
                selector=body["spec"]["selector"]["matchLabels"],
                replicas=body["spec"]["replicas"],
                template_labels=body["spec"]["template"]["metadata"]["labels"],
                annotations=dict(body["metadata"].get("annotations", {})),
            )
            self._deployments[name] = d
            self._spawn_replicas(d)
            return _deployment_to_dict(d)

    async def patch_namespaced_deployment_scale(
        self, *, name: str, namespace: str, body: dict,
    ) -> dict:
        async with self._lock:
            d = self._deployments[name]
            d.replicas = body["spec"]["replicas"]
            self._reconcile(d)
            return _deployment_to_dict(d)

    def _spawn_replicas(self, d: FakeDeployment) -> None:
        for _ in range(d.replicas - sum(1 for p in self._pods.values()
                                         if _matches(p.labels, d.selector))):
            self._spawn_one(d)

    def _spawn_one(self, d: FakeDeployment) -> None:
        name = f"{d.name}-{uuid.uuid4().hex[:8]}"
        self._pods[name] = FakePod(
            name=name,
            namespace=d.namespace,
            labels=dict(d.template_labels),
            phase="Running",
            ready=True,
            creation_timestamp=asyncio.get_event_loop().time(),
        )

    def reconcile(self) -> None:
        """Public hook for tests to trigger replenishment after a relabel."""
        for d in self._deployments.values():
            self._reconcile(d)

    def _reconcile(self, d: FakeDeployment) -> None:
        owned = [p for p in self._pods.values() if _matches(p.labels, d.selector)]
        deficit = d.replicas - len(owned)
        for _ in range(max(deficit, 0)):
            self._spawn_one(d)


class FakeNetworkingV1:
    """Subset of NetworkingV1Api: NP create/replace/list."""

    def __init__(
        self, network_policies: dict[str, dict], lock: asyncio.Lock,
    ) -> None:
        self._nps = network_policies
        self._lock = lock

    async def list_namespaced_network_policy(
        self, *, namespace: str, label_selector: str = "",
    ) -> dict:
        async with self._lock:
            return {
                "items": [
                    np for np in self._nps.values() if np.get("_namespace") == namespace
                ],
            }

    async def create_namespaced_network_policy(
        self, *, namespace: str, body: dict,
    ) -> dict:
        async with self._lock:
            name = body["metadata"]["name"]
            if name in self._nps:
                raise FakeApiException(409, "AlreadyExists")
            stored = dict(body)
            stored["_namespace"] = namespace
            self._nps[name] = stored
            return body

    async def replace_namespaced_network_policy(
        self, *, name: str, namespace: str, body: dict,
    ) -> dict:
        async with self._lock:
            stored = dict(body)
            stored["_namespace"] = namespace
            self._nps[name] = stored
            return body


@dataclass
class FakeK8s:
    """Bundle handed to tests."""

    pods: dict[str, FakePod]
    deployments: dict[str, FakeDeployment]
    network_policies: dict[str, dict]
    core_v1: FakeCoreV1
    apps_v1: FakeAppsV1
    networking_v1: FakeNetworkingV1
    lock: asyncio.Lock


@pytest.fixture
def fake_k8s() -> FakeK8s:
    """Construct a fresh fake k8s state per test."""
    lock = asyncio.Lock()
    pods: dict[str, FakePod] = {}
    deployments: dict[str, FakeDeployment] = {}
    nps: dict[str, dict] = {}
    return FakeK8s(
        pods=pods,
        deployments=deployments,
        network_policies=nps,
        core_v1=FakeCoreV1(pods, lock),
        apps_v1=FakeAppsV1(deployments, pods, lock),
        networking_v1=FakeNetworkingV1(nps, lock),
        lock=lock,
    )


# Helpers


def _pod_to_dict(p: FakePod) -> dict[str, Any]:
    return {
        "metadata": {
            "name": p.name,
            "namespace": p.namespace,
            "labels": dict(p.labels),
            "annotations": dict(p.annotations),
            "resourceVersion": p.resource_version,
            "creationTimestamp": p.creation_timestamp,
        },
        "status": {
            "phase": p.phase,
            "containerStatuses": [{"ready": p.ready, "name": "sandbox"}],
        },
        "spec": p.spec,
    }


def _deployment_to_dict(d: FakeDeployment) -> dict[str, Any]:
    return {
        "metadata": {"name": d.name, "namespace": d.namespace, "annotations": dict(d.annotations)},
        "spec": {
            "replicas": d.replicas,
            "selector": {"matchLabels": dict(d.selector)},
            "template": {"metadata": {"labels": dict(d.template_labels)}},
        },
    }


def _label_selector_matches(labels: dict[str, str], selector: str) -> bool:
    if not selector:
        return True
    for term in selector.split(","):
        term = term.strip()
        if term.startswith("!"):
            if term[1:] in labels:
                return False
        elif "=" in term:
            k, v = term.split("=", 1)
            if labels.get(k) != v:
                return False
        else:
            if term not in labels:
                return False
    return True


def _field_selector_matches(pod: FakePod, selector: str) -> bool:
    if not selector:
        return True
    for term in selector.split(","):
        term = term.strip()
        if "=" in term:
            k, v = term.split("=", 1)
            if k == "status.phase" and pod.phase != v:
                return False
    return True


def _matches(labels: dict[str, str], selector: dict[str, str]) -> bool:
    return all(labels.get(k) == v for k, v in selector.items())
