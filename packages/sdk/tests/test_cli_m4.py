# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for M4 CLI commands — admin health, workflow history, session messages."""

from __future__ import annotations

from unittest.mock import patch

from click.testing import CliRunner

from sagewai.cli import cli

runner = CliRunner()


class TestAdminHealth:
    @patch("sagewai.cli._api_get")
    def test_health_command(self, mock_get):
        mock_get.return_value = {
            "status": "healthy",
            "services": [
                {"name": "postgresql", "status": "healthy", "latency_ms": 1.2, "detail": None}
            ],
            "sdk_version": "0.1.0",
            "checked_at": "2026-03-03T12:00:00",
        }
        result = runner.invoke(cli, ["admin", "health"])
        assert result.exit_code == 0
        assert "healthy" in result.output.lower()
        mock_get.assert_called_once_with("/api/v1/health/detailed")

    @patch("sagewai.cli._api_get")
    def test_health_json(self, mock_get):
        mock_get.return_value = {
            "status": "healthy",
            "services": [],
            "sdk_version": "0.1.0",
            "checked_at": "2026-03-03T12:00:00",
        }
        result = runner.invoke(cli, ["admin", "health", "--json"])
        assert result.exit_code == 0
        assert '"status"' in result.output


class TestWorkflowHistory:
    @patch("sagewai.cli._api_get")
    def test_history_command(self, mock_get):
        mock_get.return_value = [
            {
                "id": "run-001",
                "workflow_name": "test-wf",
                "run_id": "run-001",
                "status": "succeeded",
                "created_at": "2026-03-03T12:00:00",
                "updated_at": "2026-03-03T12:00:00",
            }
        ]
        result = runner.invoke(cli, ["workflow", "history"])
        assert result.exit_code == 0
        assert "test-wf" in result.output
        mock_get.assert_called_once_with("/workflows/history?limit=50")

    @patch("sagewai.cli._api_get")
    def test_history_with_limit(self, mock_get):
        mock_get.return_value = []
        result = runner.invoke(cli, ["workflow", "history", "--limit", "10"])
        assert result.exit_code == 0
        mock_get.assert_called_once_with("/workflows/history?limit=10")

    @patch("sagewai.cli._api_get")
    def test_history_json(self, mock_get):
        mock_get.return_value = []
        result = runner.invoke(cli, ["workflow", "history", "--json"])
        assert result.exit_code == 0


class TestSessionMessages:
    @patch("sagewai.cli._helpers._api_get")
    def test_messages_command(self, mock_get):
        mock_get.return_value = {
            "session_id": "sess-001",
            "agent_name": "TestBot",
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
            ],
            "total_messages": 2,
        }
        result = runner.invoke(cli, ["session", "messages", "sess-001"])
        assert result.exit_code == 0
        assert "Hello" in result.output
        assert "Hi!" in result.output
        mock_get.assert_called_once_with("/api/v1/sessions/sess-001/messages")

    @patch("sagewai.cli._helpers._api_get")
    def test_messages_json(self, mock_get):
        mock_get.return_value = {
            "session_id": "sess-001",
            "agent_name": "TestBot",
            "messages": [],
            "total_messages": 0,
        }
        result = runner.invoke(cli, ["session", "messages", "sess-001", "--json"])
        assert result.exit_code == 0
        assert '"session_id"' in result.output
