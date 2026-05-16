# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CLI tests for `sagewai admin sandbox config k8s` and doctor extension."""
from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner


def test_config_k8s_writes_state_file(tmp_path: Path, monkeypatch):
    from sagewai.cli.sandbox import sandbox_cli
    from sagewai.admin.state_file import AdminStateFile

    state_path = tmp_path / "admin-state.json"
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_path))

    runner = CliRunner()
    result = runner.invoke(
        sandbox_cli,
        [
            "config", "k8s",
            "--kubeconfig", "/etc/kubeconfig",
            "--namespace", "acme",
            "--egress-allowlist", "10.0.0.0/8,192.168.0.0/16",
            "--no-use-in-cluster",
        ],
    )
    assert result.exit_code == 0, result.output

    sf = AdminStateFile(path=state_path)
    cfg = sf.get_kubernetes_backend_config()
    assert cfg["kubeconfig_path"] == "/etc/kubeconfig"
    assert cfg["namespace"] == "acme"
    assert cfg["egress_allowlist"] == ["10.0.0.0/8", "192.168.0.0/16"]
    assert cfg["use_in_cluster"] is False


def test_config_k8s_empty_allowlist(tmp_path: Path, monkeypatch):
    from sagewai.cli.sandbox import sandbox_cli
    from sagewai.admin.state_file import AdminStateFile

    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    runner = CliRunner()
    result = runner.invoke(
        sandbox_cli, ["config", "k8s", "--namespace", "sagewai"],
    )
    assert result.exit_code == 0, result.output
    sf = AdminStateFile(path=Path(tmp_path / "admin-state.json"))
    assert sf.get_kubernetes_backend_config()["egress_allowlist"] == []


def test_doctor_reports_k8s_line(tmp_path: Path, monkeypatch):
    from sagewai.cli.sandbox import sandbox_cli

    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    runner = CliRunner()
    result = runner.invoke(sandbox_cli, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "null:" in result.output
    assert "kubernetes:" in result.output
