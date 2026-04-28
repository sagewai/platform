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
