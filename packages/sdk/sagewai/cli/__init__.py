#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sagewai CLI — Click-based command-line interface.

Provides subcommands for managing agents, workflows, MCP servers,
admin operations, evaluations, database migrations, and project init.

Usage::

    sagewai version
    sagewai agent list
    sagewai agent run --name MyAgent --message "Hello"
    sagewai mcp list
    sagewai admin status
    sagewai eval run --dataset evals.jsonl
    sagewai db upgrade
    sagewai init my-project
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import click

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version constant — derived from the top-level sagewai package so it stays
# in sync with pyproject.toml on every release without manual edits.
# ---------------------------------------------------------------------------

from sagewai import __version__ as VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers  (kept here so tests can ``@patch("sagewai.cli._api_get")`` etc.)
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in a new event loop."""
    return asyncio.run(coro)


def _echo_json(data: Any) -> None:
    """Pretty-print a dict/list as indented JSON."""
    click.echo(json.dumps(data, indent=2, default=str))


def _echo_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    """Print a simple ASCII table."""
    if not rows:
        click.echo("(no data)")
        return

    # Calculate column widths
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = max(
            len(col),
            max((len(str(row.get(col, ""))) for row in rows), default=0),
        )

    # Header
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    click.echo(header)
    click.echo("-" * len(header))

    # Rows
    for row in rows:
        line = "  ".join(
            str(row.get(col, "")).ljust(widths[col]) for col in columns
        )
        click.echo(line)


# ---------------------------------------------------------------------------
# Admin API helpers — canonical implementation in _helpers.py
# Re-exported here for backward compat (prompt.py etc. use _cli._api_get)
# ---------------------------------------------------------------------------

from sagewai.cli._helpers import (  # noqa: E402, F401
    ADMIN_URL,
    _api_delete,
    _api_get,
    _api_post,
    _api_put,
    _auth_headers,
)


# ===========================================================================
# Root group
# ===========================================================================


@click.group(invoke_without_command=True)
@click.version_option(version=VERSION)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Sagewai — Agent Infrastructure You Own.

    Build, deploy, and operate AI agents from the command line.

    \b
    Quick start:
      sagewai init my-project     Create a new project
      sagewai doctor              Check installation health
      sagewai run                 Interactive agent REPL
      sagewai status              Check infrastructure health

    \b
    Documentation: https://docs.sagewai.dev
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ===========================================================================
# version
# ===========================================================================


@cli.command()
def version() -> None:
    """Print the Sagewai SDK version."""
    click.echo(f"sagewai {VERSION}")


# ===========================================================================
# Register submodule groups and commands
# ===========================================================================

from sagewai.cli.admin import admin  # noqa: E402
from sagewai.cli.agent import agent  # noqa: E402
from sagewai.cli.budget import budget  # noqa: E402
from sagewai.cli.db import db  # noqa: E402
from sagewai.cli.doctor import doctor  # noqa: E402
from sagewai.cli.eval_cmd import eval_group  # noqa: E402
from sagewai.cli.init_cmd import init  # noqa: E402
from sagewai.cli.mcp import mcp  # noqa: E402
from sagewai.cli.memory_cmd import memory  # noqa: E402
from sagewai.cli.model import model  # noqa: E402
from sagewai.cli.prompt import prompt  # noqa: E402
from sagewai.cli.run import run_group, _start_agent_repl  # noqa: E402, F401
from sagewai.cli.safety import safety  # noqa: E402
from sagewai.cli.session import session  # noqa: E402
from sagewai.cli.status import (  # noqa: E402, F401
    status,
    _check_tcp_connection,
    _resolve_host_port,
)
from sagewai.cli.strategy import strategy  # noqa: E402
from sagewai.cli.token import token  # noqa: E402
from sagewai.cli.workflow import workflow  # noqa: E402

cli.add_command(agent)
cli.add_command(workflow)
cli.add_command(mcp)
cli.add_command(model)
cli.add_command(prompt)
cli.add_command(token)
cli.add_command(status)
cli.add_command(doctor)
cli.add_command(init)
cli.add_command(run_group, "run")
cli.add_command(admin)
cli.add_command(eval_group, "eval")
cli.add_command(db)
cli.add_command(session)
cli.add_command(strategy)
cli.add_command(budget)
cli.add_command(safety)
cli.add_command(memory)

# ---------------------------------------------------------------------------
# Phase-4 durable-workflow commands (workflow list/inspect/retry/cancel/
# approve/reject, worker start/status, dlq list/retry/purge, db stats)
# ---------------------------------------------------------------------------

from sagewai.cli.fleet import fleet_group  # noqa: E402
from sagewai.cli.main import register_commands as _register_phase4  # noqa: E402

cli.add_command(fleet_group, "fleet")
_register_phase4(cli, workflow, db)


# ===========================================================================
# Public API
# ===========================================================================

__all__ = ["cli"]
