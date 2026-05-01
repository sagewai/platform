# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Round-trip tests for sandbox_backends.kubernetes section."""
from __future__ import annotations

from pathlib import Path


def test_sandbox_backends_kubernetes_roundtrip(tmp_path: Path):
    from sagewai.admin.state_file import AdminStateFile

    state_path = tmp_path / "admin-state.json"
    sf = AdminStateFile(path=state_path)
    sf.set_kubernetes_backend_config(
        kubeconfig_path="/etc/kubeconfig",
        namespace="sagewai",
        egress_allowlist=["10.0.0.0/8", "192.168.0.0/16"],
        use_in_cluster=True,
    )

    sf2 = AdminStateFile(path=state_path)
    cfg = sf2.get_kubernetes_backend_config()
    assert cfg == {
        "kubeconfig_path": "/etc/kubeconfig",
        "namespace": "sagewai",
        "egress_allowlist": ["10.0.0.0/8", "192.168.0.0/16"],
        "use_in_cluster": True,
    }


def test_sandbox_backends_kubernetes_defaults_when_missing(tmp_path: Path):
    from sagewai.admin.state_file import AdminStateFile

    sf = AdminStateFile(path=tmp_path / "admin-state.json")
    cfg = sf.get_kubernetes_backend_config()
    assert cfg["namespace"] == "sagewai"
    assert cfg["egress_allowlist"] == []
    assert cfg["use_in_cluster"] is True
    assert cfg["kubeconfig_path"] is None
