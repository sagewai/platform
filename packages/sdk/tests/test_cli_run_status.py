# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for `sagewai run` and `sagewai status` CLI commands (#458)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from sagewai.cli import cli

runner = CliRunner()


# ---------------------------------------------------------------------------
# sagewai run — help & argument handling
# ---------------------------------------------------------------------------


class TestRunCommand:
    def test_run_help(self):
        """sagewai run --help shows usage."""
        result = runner.invoke(cli, ["run", "--help"])
        assert result.exit_code == 0
        assert "--agent" in result.output
        assert "--model" in result.output
        assert "--config" in result.output
        assert "--tools" in result.output

    def test_run_no_args_shows_help(self):
        """sagewai run with no args shows help (not an error)."""
        result = runner.invoke(cli, ["run"])
        assert result.exit_code == 0
        assert "--agent" in result.output or "Usage" in result.output

    def test_run_subcommands_still_work(self):
        """Existing subcommands under `run` are accessible."""
        result = runner.invoke(cli, ["run", "api-list", "--help"])
        assert result.exit_code == 0
        assert "List recent runs" in result.output

    @patch("sagewai.cli._run_async")
    def test_run_agent_repl_exit(self, mock_run_async):
        """sagewai run --agent test --model gpt-4o starts REPL and exits on 'exit'."""
        # Simulate the REPL: _run_async calls the coroutine which prompts;
        # we intercept at the _start_agent_repl level by mocking _run_async
        mock_run_async.return_value = None

        result = runner.invoke(cli, ["run", "--agent", "test", "--model", "gpt-4o"])
        # Since _run_async is mocked, it just returns None (no actual REPL)
        assert result.exit_code == 0
        mock_run_async.assert_called_once()

    @patch("sagewai.cli._start_agent_repl")
    def test_run_agent_calls_repl(self, mock_repl):
        """sagewai run --agent helper calls _start_agent_repl with correct args."""
        mock_repl.return_value = None

        result = runner.invoke(
            cli,
            [
                "run",
                "--agent", "helper",
                "--model", "gpt-4o-mini",
                "--system-prompt", "Be concise.",
                "--no-stream",
            ],
        )
        assert result.exit_code == 0
        mock_repl.assert_called_once_with(
            agent_name="helper",
            model="gpt-4o-mini",
            config_path=None,
            tools=None,
            system_prompt="Be concise.",
            stream=False,
        )

    @patch("sagewai.cli._start_agent_repl")
    def test_run_config_path(self, mock_repl):
        """sagewai run --config <path> passes config to repl."""
        import tempfile
        import os

        # Create a temporary YAML file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write("name: test-agent\nmodel: gpt-4o\n")
            tmp_path = f.name

        try:
            mock_repl.return_value = None
            result = runner.invoke(cli, ["run", "--config", tmp_path])
            assert result.exit_code == 0
            mock_repl.assert_called_once()
            call_kwargs = mock_repl.call_args[1]
            assert call_kwargs["config_path"] == tmp_path
        finally:
            os.unlink(tmp_path)

    @patch("sagewai.cli._start_agent_repl")
    def test_run_with_tools(self, mock_repl):
        """sagewai run --agent a --tools 'cmd1,cmd2' passes tools."""
        mock_repl.return_value = None
        result = runner.invoke(
            cli,
            ["run", "--agent", "a", "--tools", "npx server1,npx server2"],
        )
        assert result.exit_code == 0
        call_kwargs = mock_repl.call_args[1]
        assert call_kwargs["tools"] == "npx server1,npx server2"


# ---------------------------------------------------------------------------
# sagewai status — infrastructure checks
# ---------------------------------------------------------------------------


class TestStatusCommand:
    def test_status_help(self):
        """sagewai status --help shows usage."""
        result = runner.invoke(cli, ["status", "--help"])
        assert result.exit_code == 0
        assert "--service" in result.output
        assert "--json" in result.output

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_all_connected(self, mock_tcp):
        """All services connected shows green summary."""
        mock_tcp.return_value = True
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "PostgreSQL" in result.output
        assert "Redis" in result.output
        assert "Milvus" in result.output
        assert "NebulaGraph" in result.output
        assert "All 4 services connected" in result.output

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_all_unreachable(self, mock_tcp):
        """All services unreachable shows red summary."""
        mock_tcp.return_value = False
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "All 4 services unreachable" in result.output

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_mixed(self, mock_tcp):
        """Some connected, some not shows yellow summary."""
        # Postgres connected, rest not
        mock_tcp.side_effect = [True, False, False, False]
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0
        assert "1/4 services connected" in result.output

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_json_output(self, mock_tcp):
        """--json outputs valid JSON array."""
        mock_tcp.return_value = True
        result = runner.invoke(cli, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 4
        assert all(r["status"] == "connected" for r in data)
        assert data[0]["service"] == "PostgreSQL"

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_filter_service(self, mock_tcp):
        """--service postgres only checks Postgres."""
        mock_tcp.return_value = True
        result = runner.invoke(cli, ["status", "--service", "postgres"])
        assert result.exit_code == 0
        assert "PostgreSQL" in result.output
        # Should not show other services
        assert "Redis" not in result.output
        assert "Milvus" not in result.output
        mock_tcp.assert_called_once()

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_filter_redis(self, mock_tcp):
        """--service redis only checks Redis."""
        mock_tcp.return_value = False
        result = runner.invoke(cli, ["status", "--service", "redis"])
        assert result.exit_code == 0
        assert "Redis" in result.output
        assert "PostgreSQL" not in result.output

    def test_status_unknown_service(self):
        """--service unknown exits with error."""
        result = runner.invoke(cli, ["status", "--service", "mongodb"])
        assert result.exit_code == 1

    @patch("sagewai.cli._check_tcp_connection")
    def test_status_json_unreachable(self, mock_tcp):
        """JSON output reflects unreachable status."""
        mock_tcp.return_value = False
        result = runner.invoke(cli, ["status", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert all(r["status"] == "unreachable" for r in data)


# ---------------------------------------------------------------------------
# _resolve_host_port
# ---------------------------------------------------------------------------


class TestResolveHostPort:
    def test_defaults(self):
        from sagewai.cli import _resolve_host_port

        svc = {
            "env_host": "TEST_NONEXISTENT_HOST",
            "env_port": "TEST_NONEXISTENT_PORT",
            "default_host": "localhost",
            "default_port": 5432,
        }
        host, port = _resolve_host_port(svc)
        assert host == "localhost"
        assert port == 5432

    @patch.dict("os.environ", {"TEST_HOST": "db.example.com", "TEST_PORT": "6543"})
    def test_from_env(self):
        from sagewai.cli import _resolve_host_port

        svc = {
            "env_host": "TEST_HOST",
            "env_port": "TEST_PORT",
            "default_host": "localhost",
            "default_port": 5432,
        }
        host, port = _resolve_host_port(svc)
        assert host == "db.example.com"
        assert port == 6543

    @patch.dict(
        "os.environ",
        {"TEST_DB_URL": "postgresql://user:pass@myhost:9999/db"},
    )
    def test_from_database_url(self):
        from sagewai.cli import _resolve_host_port

        svc = {
            "env_host": "TEST_NONEXISTENT_HOST",
            "env_port": "TEST_NONEXISTENT_PORT",
            "default_host": "localhost",
            "default_port": 5432,
            "env_url": "TEST_DB_URL",
        }
        host, port = _resolve_host_port(svc)
        assert host == "myhost"
        assert port == 9999


# ---------------------------------------------------------------------------
# _check_tcp_connection
# ---------------------------------------------------------------------------


class TestCheckTcpConnection:
    def test_unreachable_port(self):
        """Port that nothing listens on returns False."""
        from sagewai.cli import _check_tcp_connection

        # Use a high ephemeral port unlikely to be in use
        assert _check_tcp_connection("127.0.0.1", 59999, timeout=0.5) is False

    def test_invalid_host(self):
        """Invalid host returns False."""
        from sagewai.cli import _check_tcp_connection

        assert _check_tcp_connection("invalid.host.example", 1234, timeout=0.5) is False
