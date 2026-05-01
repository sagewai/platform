# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Pure-builder unit tests for k8s_resources. No I/O, no kubernetes_asyncio import."""
from __future__ import annotations

import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxLifetime,
)


def test_build_pod_spec_minimal():
    from sagewai.sandbox.k8s_resources import build_pod_spec

    spec = build_pod_spec(
        sandbox_id="sgw-abc123",
        run_id="r-1",
        project_id="p-1",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="sha256:" + "a" * 64,
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        lifetime=SandboxLifetime.PER_RUN,
    )

    assert spec["metadata"]["name"] == "sgw-abc123"
    labels = spec["metadata"]["labels"]
    assert labels["sagewai.sandbox_id"] == "sgw-abc123"
    assert labels["sagewai.run_id"] == "r-1"
    assert labels["sagewai.project_id"] == "p-1"
    assert labels["sagewai.phase"] == "leased"
    assert labels["sagewai.network_policy"] == "none"

    annotations = spec["metadata"]["annotations"]
    assert "sagewai.io/started-at" in annotations
    assert annotations["sagewai.io/lifetime"] == "per_run"
    assert annotations["sagewai.io/image-digest"] == "sha256:" + "a" * 64

    container = spec["spec"]["containers"][0]
    assert container["image"] == "ghcr.io/sagewai/sandbox-base:dev"
    assert container["imagePullPolicy"] == "IfNotPresent"  # digest present
    assert container["env"] == []
    assert container["resources"]["limits"]["memory"] == f"{2 * 1024**3}"
    assert container["resources"]["limits"]["cpu"] == "2000m"
    assert container["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]

    volume_names = {v["name"] for v in spec["spec"]["volumes"]}
    assert volume_names == {"workspace", "tmp"}
    assert spec["spec"]["restartPolicy"] == "Never"
    assert spec["spec"]["terminationGracePeriodSeconds"] == 10


def test_build_pod_spec_no_digest_uses_always_pull():
    from sagewai.sandbox.k8s_resources import build_pod_spec

    spec = build_pod_spec(
        sandbox_id="sgw-x",
        run_id="r",
        project_id="p",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="",
        network_policy=NetworkPolicy.FULL,
        resource_limits=ResourceLimits(),
        lifetime=SandboxLifetime.PER_RUN,
    )
    assert spec["spec"]["containers"][0]["imagePullPolicy"] == "Always"
    assert spec["metadata"]["labels"]["sagewai.network_policy"] == "full"


def test_build_pod_spec_explicit_pull_policy_override():
    from sagewai.sandbox.k8s_resources import build_pod_spec

    spec = build_pod_spec(
        sandbox_id="sgw-x",
        run_id="r",
        project_id="p",
        image="ghcr.io/sagewai/sandbox-base:dev",
        image_digest="sha256:" + "b" * 64,
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(),
        lifetime=SandboxLifetime.PER_RUN,
        image_pull_policy="Always",
    )
    assert spec["spec"]["containers"][0]["imagePullPolicy"] == "Always"


def test_build_pod_spec_resource_limits_in_millicores():
    from sagewai.sandbox.k8s_resources import build_pod_spec

    spec = build_pod_spec(
        sandbox_id="sgw-x", run_id="r", project_id="p",
        image="img", image_digest="",
        network_policy=NetworkPolicy.NONE,
        resource_limits=ResourceLimits(cpu=0.5, mem_bytes=512 * 1024**2),
        lifetime=SandboxLifetime.PER_RUN,
    )
    container = spec["spec"]["containers"][0]
    assert container["resources"]["limits"]["cpu"] == "500m"
    assert container["resources"]["limits"]["memory"] == f"{512 * 1024**2}"
    # Requests = 50% of limits
    assert container["resources"]["requests"]["cpu"] == "250m"
    assert container["resources"]["requests"]["memory"] == f"{256 * 1024**2}"


def test_pool_key_to_name_is_deterministic_and_short():
    from sagewai.sandbox.k8s_resources import pool_key_to_name
    from sagewai.sandbox.models import (
        NetworkPolicy, SandboxImageVariant, SandboxMode,
    )
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.pool_protocol import PoolKey

    key = PoolKey(
        image_digest="sha256:" + "a" * 64,
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.IDENTITY,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    name = pool_key_to_name(key)
    assert name.startswith("sagewai-pool-")
    assert len(name) <= 63
    assert name == pool_key_to_name(key)

    other = PoolKey(
        image_digest="sha256:" + "b" * 64,
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.IDENTITY,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )
    assert pool_key_to_name(other) != name


def test_build_deployment_spec_shape():
    from sagewai.sandbox.k8s_resources import build_deployment_spec
    from sagewai.sandbox.models import (
        NetworkPolicy, ResourceLimits, SandboxImageVariant, SandboxLifetime, SandboxMode,
    )
    from sagewai.core.state import ExecutionMode
    from sagewai.sandbox.pool_protocol import PoolKey

    key = PoolKey(
        image_digest="sha256:" + "a" * 64,
        sandbox_mode=SandboxMode.PER_RUN,
        execution_mode=ExecutionMode.IDENTITY,
        network_policy=NetworkPolicy.NONE,
        image_variant=SandboxImageVariant.BASE,
    )

    spec = build_deployment_spec(
        key=key, replicas=4,
        image="ghcr.io/sagewai/sandbox-base:dev",
        resource_limits=ResourceLimits(),
        lifetime=SandboxLifetime.PER_RUN,
        image_pull_policy=None,
    )

    assert spec["apiVersion"] == "apps/v1"
    assert spec["kind"] == "Deployment"
    assert spec["spec"]["replicas"] == 4
    sel = spec["spec"]["selector"]["matchLabels"]
    assert sel["sagewai.phase"] == "warm"
    assert sel["sagewai-pool"]
    template = spec["spec"]["template"]
    template_labels = template["metadata"]["labels"]
    assert template_labels["sagewai.phase"] == "warm"
    assert template_labels["sagewai-pool"] == sel["sagewai-pool"]
    assert template_labels["sagewai.network_policy"] == "none"
    assert template["spec"]["containers"][0]["env"] == []


def test_build_network_policies_returns_three():
    from sagewai.sandbox.k8s_resources import build_network_policies

    nps = build_network_policies(egress_allowlist=["10.0.0.0/8"])
    by_name = {np["metadata"]["name"]: np for np in nps}
    assert set(by_name) == {
        "sagewai-netpol-none",
        "sagewai-netpol-egress-allowlist",
        "sagewai-netpol-full",
    }


def test_none_policy_blocks_all():
    from sagewai.sandbox.k8s_resources import build_network_policies

    nps = build_network_policies(egress_allowlist=[])
    none_np = next(np for np in nps if np["metadata"]["name"] == "sagewai-netpol-none")
    spec = none_np["spec"]
    assert spec["podSelector"]["matchLabels"] == {"sagewai.network_policy": "none"}
    assert spec["policyTypes"] == ["Ingress", "Egress"]
    assert spec["ingress"] == []
    assert spec["egress"] == []


def test_egress_allowlist_renders_cidrs_and_dns():
    from sagewai.sandbox.k8s_resources import build_network_policies

    nps = build_network_policies(egress_allowlist=["10.0.0.0/8", "192.168.0.0/16"])
    np = next(n for n in nps if n["metadata"]["name"] == "sagewai-netpol-egress-allowlist")
    egress_rules = np["spec"]["egress"]
    cidr_rule = egress_rules[0]
    cidrs = {to["ipBlock"]["cidr"] for to in cidr_rule["to"]}
    assert cidrs == {"10.0.0.0/8", "192.168.0.0/16"}
    dns_rule = egress_rules[1]
    assert any(
        to.get("namespaceSelector", {}).get("matchLabels", {}).get(
            "kubernetes.io/metadata.name") == "kube-system"
        for to in dns_rule["to"]
    )
    assert {p["port"] for p in dns_rule["ports"]} == {53}


def test_full_policy_allows_all():
    from sagewai.sandbox.k8s_resources import build_network_policies

    nps = build_network_policies(egress_allowlist=[])
    full = next(np for np in nps if np["metadata"]["name"] == "sagewai-netpol-full")
    assert full["spec"]["egress"] == [{}]
    assert full["spec"]["ingress"] == [{}]
