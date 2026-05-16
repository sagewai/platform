# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Pure builders for kubernetes resource dicts. No I/O, no kubernetes_asyncio import.

Each builder returns a plain dict shaped like the kubernetes_asyncio model would
serialise. The backend layer hands these to the real client.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
)

LABEL_PREFIX = "sagewai"


def _cpu_to_millicores(cpu: float) -> str:
    return f"{int(cpu * 1000)}m"


def build_pod_spec(
    *,
    sandbox_id: str,
    run_id: str,
    project_id: str,
    image: str,
    image_digest: str,
    network_policy: NetworkPolicy,
    resource_limits: ResourceLimits,
    lifetime: SandboxLifetime,
    image_pull_policy: str | None = None,
    extra_labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Build a V1Pod-shaped dict for `core_v1.create_namespaced_pod`.

    Per Plan 1.5: pod env is empty (per-exec injection model). The phase
    label drives NetworkPolicy selection (Section 7.1 of the spec).
    """
    pull_policy = image_pull_policy or ("IfNotPresent" if image_digest else "Always")
    cpu_limit = _cpu_to_millicores(resource_limits.cpu)
    cpu_request = _cpu_to_millicores(resource_limits.cpu / 2)
    mem_limit = str(resource_limits.mem_bytes)
    mem_request = str(resource_limits.mem_bytes // 2)

    labels = {
        f"{LABEL_PREFIX}.sandbox_id": sandbox_id,
        f"{LABEL_PREFIX}.run_id": run_id,
        f"{LABEL_PREFIX}.project_id": project_id,
        f"{LABEL_PREFIX}.phase": "leased",
        f"{LABEL_PREFIX}.network_policy": network_policy.value,
        f"{LABEL_PREFIX}.image": image.replace("/", "_").replace(":", "_")[:63],
    }
    if extra_labels:
        labels.update(extra_labels)

    annotations = {
        f"{LABEL_PREFIX}.io/started-at": datetime.now(timezone.utc).isoformat(),
        f"{LABEL_PREFIX}.io/lifetime": lifetime.value,
        f"{LABEL_PREFIX}.io/image-digest": image_digest,
    }

    container = {
        "name": "sandbox",
        "image": image,
        "imagePullPolicy": pull_policy,
        "env": [],
        "resources": {
            "limits": {"cpu": cpu_limit, "memory": mem_limit},
            "requests": {"cpu": cpu_request, "memory": mem_request},
        },
        "securityContext": {
            "runAsNonRoot": True,
            "readOnlyRootFilesystem": True,
            "allowPrivilegeEscalation": False,
            "capabilities": {"drop": ["ALL"]},
        },
        "volumeMounts": [
            {"name": "workspace", "mountPath": "/workspace"},
            {"name": "tmp", "mountPath": "/tmp"},
        ],
    }

    return {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": sandbox_id,
            "labels": labels,
            "annotations": annotations,
        },
        "spec": {
            "containers": [container],
            "volumes": [
                {"name": "workspace", "emptyDir": {}},
                {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
            ],
            "restartPolicy": "Never",
            "terminationGracePeriodSeconds": 10,
        },
    }


def build_network_policies(*, egress_allowlist: list[str]) -> list[dict[str, Any]]:
    """Build the three pre-deployed NetworkPolicies (none / egress_allowlist / full).

    Each NP selects pods by `sagewai.network_policy=<value>` label. The label is set
    by `build_pod_spec` from the `network_policy` argument to `start()`.
    """
    common_meta_labels = {f"{LABEL_PREFIX}.io/managed-by": "sagewai"}

    none_np = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "sagewai-netpol-none", "labels": dict(common_meta_labels)},
        "spec": {
            "podSelector": {"matchLabels": {f"{LABEL_PREFIX}.network_policy": "none"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": [],
        },
    }

    egress_rules: list[dict[str, Any]] = []
    if egress_allowlist:
        egress_rules.append({
            "to": [{"ipBlock": {"cidr": cidr}} for cidr in egress_allowlist],
        })
    # DNS to CoreDNS — required for any name lookup
    egress_rules.append({
        "to": [{
            "namespaceSelector": {
                "matchLabels": {"kubernetes.io/metadata.name": "kube-system"},
            },
            "podSelector": {"matchLabels": {"k8s-app": "kube-dns"}},
        }],
        "ports": [
            {"protocol": "UDP", "port": 53},
            {"protocol": "TCP", "port": 53},
        ],
    })

    egress_np = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {
            "name": "sagewai-netpol-egress-allowlist",
            "labels": dict(common_meta_labels),
        },
        "spec": {
            "podSelector": {
                "matchLabels": {f"{LABEL_PREFIX}.network_policy": "egress_allowlist"},
            },
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [],
            "egress": egress_rules,
        },
    }

    full_np = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "sagewai-netpol-full", "labels": dict(common_meta_labels)},
        "spec": {
            "podSelector": {"matchLabels": {f"{LABEL_PREFIX}.network_policy": "full"}},
            "policyTypes": ["Ingress", "Egress"],
            "ingress": [{}],
            "egress": [{}],
        },
    }

    return [none_np, egress_np, full_np]


def pool_key_to_name(key: Any) -> str:
    """Hash a PoolKey to a K8s-safe deployment name."""
    import hashlib
    import json

    canonical = json.dumps({
        "image_digest": key.image_digest,
        "sandbox_mode": key.sandbox_mode.value,
        "execution_mode": key.execution_mode.value,
        "network_policy": key.network_policy.value,
        "image_variant": key.image_variant.value,
    }, sort_keys=True)
    h = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"sagewai-pool-{h}"


def build_deployment_spec(
    *,
    key: Any,
    replicas: int,
    image: str,
    resource_limits: ResourceLimits,
    lifetime: SandboxLifetime,
    image_pull_policy: str | None = None,
) -> dict[str, Any]:
    """Build a V1Deployment for warm-pod replication."""
    name = pool_key_to_name(key)
    pool_hash = name.removeprefix("sagewai-pool-")
    pull_policy = image_pull_policy or ("IfNotPresent" if key.image_digest else "Always")
    cpu_limit = _cpu_to_millicores(resource_limits.cpu)
    cpu_request = _cpu_to_millicores(resource_limits.cpu / 2)
    mem_limit = str(resource_limits.mem_bytes)
    mem_request = str(resource_limits.mem_bytes // 2)

    pool_labels = {
        "sagewai-pool": pool_hash,
        f"{LABEL_PREFIX}.phase": "warm",
        f"{LABEL_PREFIX}.network_policy": key.network_policy.value,
        f"{LABEL_PREFIX}.image_variant": key.image_variant.value,
    }

    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": name,
            "annotations": {
                f"{LABEL_PREFIX}.io/pool-key-image-digest": key.image_digest,
                f"{LABEL_PREFIX}.io/pool-key-execution-mode": key.execution_mode.value,
                f"{LABEL_PREFIX}.io/pool-key-sandbox-mode": key.sandbox_mode.value,
            },
            "labels": {f"{LABEL_PREFIX}.io/managed-by": "sagewai"},
        },
        "spec": {
            "replicas": replicas,
            "selector": {"matchLabels": dict(pool_labels)},
            "template": {
                "metadata": {"labels": dict(pool_labels)},
                "spec": {
                    "containers": [{
                        "name": "sandbox",
                        "image": image,
                        "imagePullPolicy": pull_policy,
                        "env": [],
                        "resources": {
                            "limits": {"cpu": cpu_limit, "memory": mem_limit},
                            "requests": {"cpu": cpu_request, "memory": mem_request},
                        },
                        "securityContext": {
                            "runAsNonRoot": True,
                            "readOnlyRootFilesystem": True,
                            "allowPrivilegeEscalation": False,
                            "capabilities": {"drop": ["ALL"]},
                        },
                        "volumeMounts": [
                            {"name": "workspace", "mountPath": "/workspace"},
                            {"name": "tmp", "mountPath": "/tmp"},
                        ],
                    }],
                    "volumes": [
                        {"name": "workspace", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {"sizeLimit": "512Mi"}},
                    ],
                    "restartPolicy": "Always",
                    "terminationGracePeriodSeconds": 10,
                },
            },
        },
    }
