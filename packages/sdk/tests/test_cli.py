# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the Click-based CLI."""

from click.testing import CliRunner

from sagewai.cli import cli

runner = CliRunner()


# -- version ------------------------------------------------------------------


def test_version():
    result = runner.invoke(cli, ["version"])
    assert result.exit_code == 0
    assert "sagewai" in result.output.lower()


# -- agent --------------------------------------------------------------------


def test_agent_list():
    result = runner.invoke(cli, ["agent", "list"])
    assert result.exit_code == 0


def test_agent_run_help():
    result = runner.invoke(cli, ["agent", "run", "--help"])
    assert result.exit_code == 0


def test_agent_chat_help():
    result = runner.invoke(cli, ["agent", "chat", "--help"])
    assert result.exit_code == 0


# -- workflow -----------------------------------------------------------------


def test_workflow_help():
    result = runner.invoke(cli, ["workflow", "--help"])
    assert result.exit_code == 0


def test_workflow_run_help():
    result = runner.invoke(cli, ["workflow", "run", "--help"])
    assert result.exit_code == 0


def test_workflow_resume_help():
    result = runner.invoke(cli, ["workflow", "resume", "--help"])
    assert result.exit_code == 0


# -- mcp ----------------------------------------------------------------------
# Legacy `sagewai mcp` CLI group was deleted in MCP-as-first-class Bundle A.
# Equivalent commands now live under `sagewai connections mcp ...` and are
# covered by `tests/cli/test_connections_cli.py`.


# -- admin --------------------------------------------------------------------


def test_admin_status():
    result = runner.invoke(cli, ["admin", "status"])
    assert result.exit_code == 0


def test_admin_runs():
    result = runner.invoke(cli, ["admin", "runs"])
    assert result.exit_code == 0


def test_admin_costs():
    result = runner.invoke(cli, ["admin", "costs"])
    assert result.exit_code == 0


def test_admin_serve_help():
    result = runner.invoke(cli, ["admin", "serve", "--help"])
    assert result.exit_code == 0


# -- eval ---------------------------------------------------------------------


def test_eval_help():
    result = runner.invoke(cli, ["eval", "--help"])
    assert result.exit_code == 0


def test_eval_run_help():
    result = runner.invoke(cli, ["eval", "run", "--help"])
    assert result.exit_code == 0


def test_eval_report_help():
    result = runner.invoke(cli, ["eval", "report", "--help"])
    assert result.exit_code == 0


# -- db -----------------------------------------------------------------------


def test_db_upgrade_help():
    result = runner.invoke(cli, ["db", "upgrade", "--help"])
    assert result.exit_code == 0


def test_db_downgrade_help():
    result = runner.invoke(cli, ["db", "downgrade", "--help"])
    assert result.exit_code == 0


# -- init ---------------------------------------------------------------------


def test_init_help():
    result = runner.invoke(cli, ["init", "--help"])
    assert result.exit_code == 0


# -- top-level ----------------------------------------------------------------


def test_help():
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "sagewai" in result.output.lower()


def test_no_args_shows_help():
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
