# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Kubernetes ApiClient factory + kubeconfig resolution chain."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from sagewai.sandbox.docker_backend import SandboxError

_IN_CLUSTER_TOKEN_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/token")
_DEFAULT_KUBECONFIG = Path("~/.kube/config").expanduser()


async def make_api_client(
    *,
    kubeconfig_path: str | None,
    use_in_cluster: bool,
    default_path: Path = _DEFAULT_KUBECONFIG,
) -> Any:
    """Resolve a kubernetes_asyncio ApiClient via the standard chain.

    Priority: explicit path > in-cluster (if token exists) > default kubeconfig.
    Raises SandboxError if none resolve.
    """
    from kubernetes_asyncio import client, config  # lazy: extra-gated

    if kubeconfig_path:
        await config.load_kube_config(config_file=kubeconfig_path)
        return client.ApiClient()

    if use_in_cluster and _IN_CLUSTER_TOKEN_PATH.exists():
        config.load_incluster_config()
        return client.ApiClient()

    if default_path.exists():
        await config.load_kube_config(config_file=str(default_path))
        return client.ApiClient()

    raise SandboxError(
        "no kubeconfig found: set kubernetes_kubeconfig_path or run in-cluster"
    )
