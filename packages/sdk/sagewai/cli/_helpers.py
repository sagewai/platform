# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Shared CLI helpers — async runner, output formatters, and API clients."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import click

# ---------------------------------------------------------------------------
# Version constant — derived from sagewai.__version__ so it tracks
# pyproject.toml automatically on every release.
# ---------------------------------------------------------------------------

from sagewai import __version__ as VERSION  # noqa: E402

# ---------------------------------------------------------------------------
# Async helper
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    """Run an async coroutine in a new event loop."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------


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
# Admin API helpers (httpx-backed)
# ---------------------------------------------------------------------------

ADMIN_URL = os.environ.get("SAGEWAI_ADMIN_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    """Return Authorization header if SAGEWAI_API_TOKEN is set."""
    token = os.environ.get("SAGEWAI_API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api_get(path: str) -> Any:
    """GET from the admin API."""
    import httpx

    resp = httpx.get(f"{ADMIN_URL}{path}", headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _api_post(path: str, body: dict[str, Any] | None = None) -> Any:
    """POST to the admin API."""
    import httpx

    resp = httpx.post(
        f"{ADMIN_URL}{path}", json=body or {}, headers=_auth_headers(), timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _api_delete(path: str) -> Any:
    """DELETE on the admin API."""
    import httpx

    resp = httpx.delete(f"{ADMIN_URL}{path}", headers=_auth_headers(), timeout=30)
    resp.raise_for_status()
    return resp.json()


def _api_put(path: str, body: dict[str, Any] | None = None) -> Any:
    """PUT to the admin API."""
    import httpx

    resp = httpx.put(
        f"{ADMIN_URL}{path}", json=body or {}, headers=_auth_headers(), timeout=30,
    )
    resp.raise_for_status()
    return resp.json()
