# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the fleet CLI commands — command group, --help, and basic flows."""

from __future__ import annotations

from click.testing import CliRunner

from sagewai.cli.fleet import _LocalFleetRegistry, fleet_group


class TestFleetCLI:
    """Verify fleet CLI commands exist and produce expected output."""

    def setup_method(self) -> None:
        """Reset the singleton registry between tests."""
        _LocalFleetRegistry._instance = None

    def test_fleet_group_exists(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["--help"])
        assert result.exit_code == 0
        assert "fleet workers" in result.output.lower() or "enrollment" in result.output.lower()

    def test_register_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["register", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--org" in result.output
        assert "--models" in result.output

    def test_list_workers_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["list-workers", "--help"])
        assert result.exit_code == 0
        assert "--org" in result.output
        assert "--status" in result.output

    def test_create_key_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["create-key", "--help"])
        assert result.exit_code == 0
        assert "--name" in result.output
        assert "--max-uses" in result.output

    def test_list_keys_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["list-keys", "--help"])
        assert result.exit_code == 0
        assert "--org" in result.output

    def test_revoke_key_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["revoke-key", "--help"])
        assert result.exit_code == 0
        assert "key_id" in result.output.lower() or "KEY_ID" in result.output

    def test_register_worker(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, [
            "register",
            "--name", "test-worker",
            "--org", "acme",
            "--models", "gpt-4o,claude-sonnet-4-6",
            "--pool", "gpu",
        ])
        assert result.exit_code == 0
        assert "Registered worker" in result.output
        assert "test-worker" in result.output
        assert "gpt-4o" in result.output
        assert "gpu" in result.output

    def test_register_with_labels(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, [
            "register",
            "--name", "labeled-worker",
            "--org", "acme",
            "--models", "gpt-4o",
            "--labels", "region=us-east,tier=premium",
        ])
        assert result.exit_code == 0
        assert "labeled-worker" in result.output
        assert "region" in result.output

    def test_list_workers_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["list-workers", "--org", "acme"])
        assert result.exit_code == 0
        assert "No workers found" in result.output

    def test_list_workers_after_register(self) -> None:
        runner = CliRunner()
        # Register first
        runner.invoke(fleet_group, [
            "register", "--name", "w1", "--org", "acme", "--models", "gpt-4o",
        ])
        # List
        result = runner.invoke(fleet_group, ["list-workers", "--org", "acme"])
        assert result.exit_code == 0
        assert "w1" in result.output

    def test_list_workers_json(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "register", "--name", "w1", "--org", "acme", "--models", "gpt-4o",
        ])
        result = runner.invoke(fleet_group, ["list-workers", "--org", "acme", "--json"])
        assert result.exit_code == 0
        assert '"name": "w1"' in result.output

    def test_create_key(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, [
            "create-key",
            "--org", "acme",
            "--name", "onboarding",
            "--max-uses", "10",
            "--expires", "7d",
        ])
        assert result.exit_code == 0
        assert "swk_" in result.output
        assert "will not be shown again" in result.output
        assert "onboarding" in result.output

    def test_create_key_with_pools_and_models(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, [
            "create-key",
            "--org", "acme",
            "--name", "scoped-key",
            "--pools", "gpu,cpu",
            "--models", "gpt-4o,llama3-70b",
        ])
        assert result.exit_code == 0
        assert "gpu" in result.output

    def test_list_keys_empty(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["list-keys", "--org", "acme"])
        assert result.exit_code == 0
        assert "No enrollment keys found" in result.output

    def test_list_keys_after_create(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "create-key", "--org", "acme", "--name", "key1",
        ])
        result = runner.invoke(fleet_group, ["list-keys", "--org", "acme"])
        assert result.exit_code == 0
        assert "key1" in result.output
        assert "active" in result.output

    def test_revoke_key(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "create-key", "--org", "acme", "--name", "revoke-me",
        ])

        registry = _LocalFleetRegistry.get()
        key_id = list(registry.keys.keys())[0]

        result = runner.invoke(fleet_group, ["revoke-key", key_id])
        assert result.exit_code == 0
        assert "Revoked" in result.output

        # Verify it shows as revoked
        result = runner.invoke(fleet_group, ["list-keys", "--org", "acme"])
        assert "revoked" in result.output

    def test_revoke_key_not_found(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, ["revoke-key", "nonexistent-id"])
        assert result.exit_code != 0

    def test_revoke_key_already_revoked(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "create-key", "--org", "acme", "--name", "double-revoke",
        ])

        registry = _LocalFleetRegistry.get()
        key_id = list(registry.keys.keys())[0]

        runner.invoke(fleet_group, ["revoke-key", key_id])
        result = runner.invoke(fleet_group, ["revoke-key", key_id])
        assert result.exit_code == 0
        assert "already revoked" in result.output

    def test_register_cloud_url_not_implemented(self) -> None:
        runner = CliRunner()
        result = runner.invoke(fleet_group, [
            "register",
            "--name", "remote-w",
            "--org", "acme",
            "--models", "gpt-4o",
            "--cloud-url", "https://fleet.example.com",
        ])
        assert result.exit_code == 0
        assert "not yet implemented" in result.output

    def test_list_workers_filter_by_status(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "register", "--name", "w1", "--org", "acme", "--models", "gpt-4o",
        ])
        # Default status is pending
        result = runner.invoke(fleet_group, [
            "list-workers", "--org", "acme", "--status", "approved",
        ])
        assert result.exit_code == 0
        assert "No workers found" in result.output

        result = runner.invoke(fleet_group, [
            "list-workers", "--org", "acme", "--status", "pending",
        ])
        assert result.exit_code == 0
        assert "w1" in result.output

    def test_list_workers_filter_by_pool(self) -> None:
        runner = CliRunner()
        runner.invoke(fleet_group, [
            "register", "--name", "w1", "--org", "acme", "--models", "gpt-4o", "--pool", "gpu",
        ])
        result = runner.invoke(fleet_group, [
            "list-workers", "--org", "acme", "--pool", "cpu",
        ])
        assert result.exit_code == 0
        assert "No workers found" in result.output
