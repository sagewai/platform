# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""sagewai admin directives — Sealed-v reactive directive management.

Subcommands:
  list-policies          List directive policies; optional cascade preview
  set-policy             Replace the policy tree from a JSON file
  list-evaluations       List recent directive_evaluations rows
  approve <decision_id>  Approve a pending HITL decision
  deny <decision_id>     Deny a pending HITL decision
  preview                Preview which policies are active for a given workflow

All subcommands talk to the admin REST API; no direct admin-state access.
"""
from __future__ import annotations

import json
import os

import click

_DEFAULT_BASE_URL = os.environ.get("SAGEWAI_ADMIN_URL", "http://localhost:8000")


def _resolve_base_url() -> str:
    return os.environ.get("SAGEWAI_ADMIN_URL", _DEFAULT_BASE_URL)


def _resolve_token() -> str:
    return os.environ.get("SAGEWAI_ADMIN_TOKEN", "")


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    token = _resolve_token()
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(path: str, **kwargs):
    import httpx

    return httpx.get(_resolve_base_url() + path, headers=_headers(), **kwargs)


def _put(path: str, json_body: dict, **kwargs):
    import httpx

    return httpx.put(
        _resolve_base_url() + path,
        json=json_body,
        headers=_headers(),
        **kwargs,
    )


def _post(path: str, json_body: dict, **kwargs):
    import httpx

    return httpx.post(
        _resolve_base_url() + path,
        json=json_body,
        headers=_headers(),
        **kwargs,
    )


@click.group("directives")
def directives_group() -> None:
    """Sealed-v reactive directive management."""


@directives_group.command("list-policies")
@click.option("--workflow", help="If set, show resolved cascade for this workflow")
@click.option("--project", help="Project id (with --workflow)")
def list_policies(workflow: str | None, project: str | None) -> None:
    """List directive policies."""
    if workflow:
        params = {"workflow": workflow}
        if project:
            params["project_id"] = project
        r = _get("/api/v1/admin/directives/preview", params=params)
    else:
        r = _get("/api/v1/admin/directives/policies")
    if r.status_code != 200:
        raise click.ClickException(f"server returned {r.status_code}: {r.text}")
    click.echo(json.dumps(r.json(), indent=2))


@directives_group.command("set-policy")
@click.option(
    "--from-file",
    type=click.Path(exists=True),
    required=True,
    help="JSON file containing the directives policy tree",
)
def set_policy(from_file: str) -> None:
    """Replace the directives policy tree from a JSON file."""
    with open(from_file) as f:
        body = json.load(f)
    r = _put("/api/v1/admin/directives/policies", json_body=body)
    if r.status_code != 200:
        raise click.ClickException(f"server returned {r.status_code}: {r.text}")
    click.echo("OK")


@directives_group.command("list-evaluations")
@click.option("--run-id", help="Filter by run id")
@click.option("--policy-id", help="Filter by policy id")
@click.option("--event-type", help="Filter by event_type")
@click.option("--limit", default=50, type=int)
def list_evaluations(
    run_id: str | None,
    policy_id: str | None,
    event_type: str | None,
    limit: int,
) -> None:
    """List recent directive evaluations."""
    params: dict = {"limit": limit}
    if run_id:
        params["run_id"] = run_id
    if policy_id:
        params["policy_id"] = policy_id
    if event_type:
        params["event_type"] = event_type
    r = _get("/api/v1/admin/directives/evaluations", params=params)
    if r.status_code != 200:
        raise click.ClickException(f"server returned {r.status_code}: {r.text}")
    click.echo(json.dumps(r.json(), indent=2))


@directives_group.command("approve")
@click.argument("decision_id")
@click.option("--actor", default="default-admin")
@click.option("--note", default="")
def approve(decision_id: str, actor: str, note: str) -> None:
    """Approve a pending directive decision."""
    r = _post(
        f"/api/v1/admin/directives/approvals/{decision_id}/approve",
        json_body={"actor": actor, "note": note},
    )
    if r.status_code >= 400:
        raise click.ClickException(f"{r.status_code}: {r.text}")
    click.echo("approved")


@directives_group.command("deny")
@click.argument("decision_id")
@click.option("--actor", default="default-admin")
@click.option("--note", default="")
def deny(decision_id: str, actor: str, note: str) -> None:
    """Deny a pending directive decision."""
    r = _post(
        f"/api/v1/admin/directives/approvals/{decision_id}/deny",
        json_body={"actor": actor, "note": note},
    )
    if r.status_code >= 400:
        raise click.ClickException(f"{r.status_code}: {r.text}")
    click.echo("denied")


@directives_group.command("preview")
@click.option("--workflow", required=True)
@click.option("--project")
def preview(workflow: str, project: str | None) -> None:
    """Preview which policies are active for a given workflow."""
    params = {"workflow": workflow}
    if project:
        params["project_id"] = project
    r = _get("/api/v1/admin/directives/preview", params=params)
    if r.status_code != 200:
        raise click.ClickException(f"server returned {r.status_code}: {r.text}")
    click.echo(json.dumps(r.json(), indent=2))


__all__ = ["directives_group"]
