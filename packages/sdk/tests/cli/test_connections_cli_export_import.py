# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the `sagewai connections export/import` CLI commands."""
from __future__ import annotations

import json
import os
from textwrap import dedent

import pytest
import yaml as pyyaml
from click.testing import CliRunner
from cryptography.fernet import Fernet

from sagewai.cli.connections import connections as connections_cli


@pytest.fixture
def master_key():
    saved = os.environ.get("SAGEWAI_MASTER_KEY")
    os.environ["SAGEWAI_MASTER_KEY"] = Fernet.generate_key().decode()
    yield
    if saved is None:
        os.environ.pop("SAGEWAI_MASTER_KEY", None)
    else:
        os.environ["SAGEWAI_MASTER_KEY"] = saved


@pytest.fixture
def isolated_env(tmp_path, monkeypatch, master_key):
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    monkeypatch.setenv("SAGEWAI_CONNECTIONS_FILE", str(tmp_path / "connections.json"))
    return tmp_path


def test_export_writes_yaml_to_stdout(isolated_env):
    runner = CliRunner()
    result = runner.invoke(connections_cli, ["export", "--project", "test"])
    assert result.exit_code == 0, result.output
    body = pyyaml.safe_load(result.output)
    assert body["version"] == 1


def test_export_to_file(isolated_env, tmp_path):
    outfile = tmp_path / "out.yaml"
    runner = CliRunner()
    result = runner.invoke(
        connections_cli, ["export", "--project", "test", "-o", str(outfile)]
    )
    assert result.exit_code == 0, result.output
    assert outfile.exists()
    body = pyyaml.safe_load(outfile.read_text())
    assert body["version"] == 1


def test_export_invalid_secrets_mode_errors(isolated_env):
    runner = CliRunner()
    result = runner.invoke(
        connections_cli, ["export", "--project", "test", "--secrets", "bogus"]
    )
    assert result.exit_code != 0


def test_import_from_stdin_creates(isolated_env):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()

    runner = CliRunner()
    result = runner.invoke(
        connections_cli, ["import", "--project", "test"], input=yaml_text
    )
    assert result.exit_code == 0, result.output
    assert "created: 1" in result.output or "alpha" in result.output


def test_import_dry_run_does_not_persist(isolated_env, tmp_path):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()

    runner = CliRunner()
    result = runner.invoke(
        connections_cli,
        ["import", "--project", "test", "--dry-run"],
        input=yaml_text,
    )
    assert result.exit_code == 0, result.output

    # Now run list to verify nothing persisted
    list_result = runner.invoke(
        connections_cli, ["list", "--project", "test", "--json"]
    )
    parsed = json.loads(list_result.output) if list_result.output.strip() else []
    assert not parsed or all(c["display_name"] != "alpha" for c in parsed)


def test_import_json_output(isolated_env):
    yaml_text = dedent("""
        version: 1
        secrets_mode: redacted
        connections:
          - protocol: http
            display_name: alpha
            tags: []
            credentials_backend:
              kind: local
            is_default: true
            protocol_data:
              base_url: https://a.com
              auth:
                kind: none
    """).strip()

    runner = CliRunner()
    result = runner.invoke(
        connections_cli,
        ["import", "--project", "test", "--json"],
        input=yaml_text,
    )
    assert result.exit_code == 0, result.output
    parsed = json.loads(result.output)
    assert "created" in parsed
    assert len(parsed["created"]) == 1
