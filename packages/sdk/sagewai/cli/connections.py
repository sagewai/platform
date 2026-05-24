# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""``sagewai connections`` — generic CLI for the Connections Platform.

8 generic subcommands:

  protocols                List registered protocol plugins
  backends                 List registered credentials backends
  list                     List connections in the current project
  get <id>                 Show one connection (masked)
  add <protocol>           Add a connection (JSON-driven via --data)
  update <id>              PATCH a connection (display-name / tags / data / backend)
  delete <id>              Hard-delete a connection
  test <id>                Run the plugin's test() method
  set-default <id>         Mark this connection as default for its group

Plus plugin :meth:`extra_cli` sub-groups mounted by plugin id:

  sagewai connections oauth2 start <id>
  sagewai connections oauth2 refresh <id>
  ...

All commands accept ``--project P`` (default ``$SAGEWAI_PROJECT`` or
``"default"``) and ``--json`` for machine-readable output.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import click
from pydantic import ValidationError

from sagewai.admin.state_file import AdminStateFile, default_admin_state_path
from sagewai.connections.bootstrap import build_connections_context
from sagewai.connections.credentials import all_backends
from sagewai.connections.errors import (
    ConnectionNotFoundError,
    DuplicateDisplayNameError,
    UnknownProtocolError,
)
from sagewai.connections.protocols import all_protocols, get_protocol
from sagewai.connections.protocols import oauth2 as oauth2_module


def _default_project() -> str:
    return os.environ.get("SAGEWAI_PROJECT") or "default"


def _get_ctx():
    """Build a fresh ConnectionsContext + inject it for plugin extra_cli."""
    sf = AdminStateFile(default_admin_state_path())
    ctx = build_connections_context(sf)
    # Plugins (oauth2) read this for their extra_cli command bodies.
    oauth2_module._test_inject_context(ctx)
    return ctx


def _serialize(record, *, plugin_public_view) -> dict[str, Any]:
    return {
        "id": record.id,
        "protocol": record.protocol,
        "project_id": record.project_id,
        "display_name": record.display_name,
        "tags": list(record.tags),
        "credentials_backend": record.credentials_backend,
        "status": record.status,
        "last_tested_at": record.last_tested_at,
        "last_test_ok": record.last_test_ok,
        "is_default": record.is_default,
        "created_at": record.created_at,
        "updated_at": record.updated_at,
        "last_error": record.last_error,
        "protocol_data": plugin_public_view(record.protocol_data),
    }


def _echo_table(rows: list[dict[str, Any]], columns: list[str]) -> None:
    if not rows:
        click.echo("(no records)")
        return
    widths: dict[str, int] = {}
    for col in columns:
        widths[col] = max(
            len(col),
            max((len(str(row.get(col, ""))) for row in rows), default=0),
        )
    header = "  ".join(col.ljust(widths[col]) for col in columns)
    click.echo(header)
    click.echo("-" * len(header))
    for row in rows:
        click.echo("  ".join(str(row.get(col, "")).ljust(widths[col]) for col in columns))


# ── Top-level group ─────────────────────────────────────────────────


@click.group("connections")
def connections() -> None:
    """Manage external-dependency connections (HTTP / OAuth2 / MCP / inference / SDK)."""


# ── protocols ──────────────────────────────────────────────────────


@connections.command("protocols")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def protocols_cmd(as_json: bool) -> None:
    """List registered protocol plugins."""
    rows = [
        {
            "id": p.id,
            "display_name": p.display_name,
            "sensitive_fields": list(p.sensitive_fields),
        }
        for p in all_protocols()
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    _echo_table(
        [{"id": r["id"], "display_name": r["display_name"]} for r in rows],
        ["id", "display_name"],
    )


# ── backends ───────────────────────────────────────────────────────


@connections.command("backends")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def backends_cmd(as_json: bool) -> None:
    """List registered credentials backends."""
    rows = [
        {"id": b.id, "display_name": b.display_name}
        for b in all_backends()
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    _echo_table(rows, ["id", "display_name"])


# ── list ────────────────────────────────────────────────────────────


@connections.command("list")
@click.option("--project", default=None, help="Project scope ($SAGEWAI_PROJECT or 'default')")
@click.option("--protocol", default=None, help="Filter by protocol id")
@click.option("--tag", default=None, help="Filter by tag")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def list_cmd(project: str | None, protocol: str | None, tag: str | None, as_json: bool) -> None:
    """List connections in the current project."""
    pid = project or _default_project()
    ctx = _get_ctx()
    records = ctx.store.list(pid, protocol=protocol, tag=tag)
    rows = [
        _serialize(r, plugin_public_view=get_protocol(r.protocol).public_view)
        for r in records
    ]
    if as_json:
        click.echo(json.dumps(rows, indent=2, default=str))
        return
    table_rows = [
        {
            "id": r["id"],
            "protocol": r["protocol"],
            "display_name": r["display_name"],
            "status": r["status"],
            "is_default": r["is_default"],
        }
        for r in rows
    ]
    _echo_table(table_rows, ["id", "protocol", "display_name", "status", "is_default"])


# ── get ─────────────────────────────────────────────────────────────


@connections.command("get")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def get_cmd(connection_id: str, as_json: bool) -> None:
    """Show one connection (masked)."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    plugin = get_protocol(record.protocol)
    serialized = _serialize(record, plugin_public_view=plugin.public_view)
    if as_json:
        click.echo(json.dumps(serialized, indent=2, default=str))
        return
    for key in [
        "id", "protocol", "project_id", "display_name", "tags",
        "status", "is_default", "last_tested_at", "last_test_ok",
        "created_at", "updated_at",
    ]:
        click.echo(f"{key}: {serialized.get(key)}")
    click.echo(f"protocol_data: {json.dumps(serialized['protocol_data'], indent=2, default=str)}")


# ── add ─────────────────────────────────────────────────────────────


@connections.command("add")
@click.argument("protocol")
@click.option("--display-name", required=True, help="Human-readable name")
@click.option("--project", default=None, help="Project scope ($SAGEWAI_PROJECT or 'default')")
@click.option("--data", required=True, help="protocol_data as JSON string or @path/to/file.json")
@click.option("--tags", default="", help="Comma-separated tags")
@click.option("--credentials-backend", "credentials_backend_json", default=None,
              help="Per-connection credentials backend as JSON")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def add_cmd(
    protocol: str,
    display_name: str,
    project: str | None,
    data: str,
    tags: str,
    credentials_backend_json: str | None,
    as_json: bool,
) -> None:
    """Add a connection (JSON-driven via --data)."""
    pid = project or _default_project()
    ctx = _get_ctx()
    # Read data
    if data.startswith("@"):
        with open(data[1:], "r", encoding="utf-8") as f:
            protocol_data = json.load(f)
    else:
        try:
            protocol_data = json.loads(data)
        except json.JSONDecodeError as exc:
            click.echo(f"  ✗ --data is not valid JSON: {exc}", err=True)
            raise click.exceptions.Exit(2)
    credentials_backend = None
    if credentials_backend_json:
        try:
            credentials_backend = json.loads(credentials_backend_json)
        except json.JSONDecodeError as exc:
            click.echo(f"  ✗ --credentials-backend is not valid JSON: {exc}", err=True)
            raise click.exceptions.Exit(2)
    tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    try:
        plugin = get_protocol(protocol)
    except UnknownProtocolError as exc:
        click.echo(f"  ✗ unknown protocol: {exc}", err=True)
        raise click.exceptions.Exit(2)
    try:
        plugin.protocol_data_schema()(**protocol_data)
    except ValidationError as exc:
        click.echo(f"  ✗ protocol_data validation failed: {exc}", err=True)
        raise click.exceptions.Exit(2)
    encrypted_pd = ctx.router.encrypt(
        protocol_data,
        sensitive_field_paths=plugin.sensitive_fields,
        connection_credentials_backend=credentials_backend,
    )
    try:
        connection = ctx.store.create(
            protocol=protocol,
            project_id=pid,
            display_name=display_name,
            tags=tag_list,
            protocol_data=encrypted_pd,
            credentials_backend=credentials_backend,
        )
    except DuplicateDisplayNameError as exc:
        click.echo(f"  ✗ duplicate: {exc}", err=True)
        raise click.exceptions.Exit(2)
    plugin_ctx = ctx.make_plugin_context(project_id=pid, request=None)
    try:
        connection = asyncio.run(plugin.on_create(connection, ctx=plugin_ctx))
    except Exception as exc:
        ctx.store.delete(connection.id)
        click.echo(f"  ✗ plugin.on_create failed: {exc}", err=True)
        raise click.exceptions.Exit(3)
    serialized = _serialize(connection, plugin_public_view=plugin.public_view)
    if as_json:
        click.echo(json.dumps(serialized, indent=2, default=str))
    else:
        click.echo(f"  ✓ added {serialized['id']}")


# ── update ──────────────────────────────────────────────────────────


@connections.command("update")
@click.argument("connection_id")
@click.option("--display-name", default=None)
@click.option("--tags", default=None, help="Comma-separated tags (use empty string to clear)")
@click.option("--data", default=None, help="New protocol_data JSON or @file")
@click.option("--credentials-backend", "credentials_backend_json", default=None,
              help="New credentials backend JSON (use 'null' to clear)")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def update_cmd(
    connection_id: str,
    display_name: str | None,
    tags: str | None,
    data: str | None,
    credentials_backend_json: str | None,
    as_json: bool,
) -> None:
    """PATCH a connection."""
    ctx = _get_ctx()
    before = ctx.store.get(connection_id)
    if before is None:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    plugin = get_protocol(before.protocol)

    update_fields: dict[str, Any] = {}
    if display_name is not None:
        update_fields["display_name"] = display_name
    if tags is not None:
        update_fields["tags"] = [t.strip() for t in tags.split(",") if t.strip()]

    new_backend = None
    backend_changed = False
    if credentials_backend_json is not None:
        backend_changed = True
        if credentials_backend_json.strip() in ("null", "None", ""):
            new_backend = None
        else:
            try:
                new_backend = json.loads(credentials_backend_json)
            except json.JSONDecodeError as exc:
                click.echo(f"  ✗ --credentials-backend not valid JSON: {exc}", err=True)
                raise click.exceptions.Exit(2)

    if data is not None:
        if data.startswith("@"):
            with open(data[1:], "r", encoding="utf-8") as f:
                new_pd = json.load(f)
        else:
            try:
                new_pd = json.loads(data)
            except json.JSONDecodeError as exc:
                click.echo(f"  ✗ --data not valid JSON: {exc}", err=True)
                raise click.exceptions.Exit(2)
        try:
            plugin.protocol_data_schema()(**new_pd)
        except ValidationError as exc:
            click.echo(f"  ✗ protocol_data validation failed: {exc}", err=True)
            raise click.exceptions.Exit(2)
        target_backend = new_backend if backend_changed else before.credentials_backend
        encrypted_pd = ctx.router.encrypt(
            new_pd,
            sensitive_field_paths=plugin.sensitive_fields,
            connection_credentials_backend=target_backend,
        )
        update_fields["protocol_data"] = encrypted_pd
    elif backend_changed:
        swapped = ctx.router.swap(
            before.protocol_data,
            sensitive_field_paths=plugin.sensitive_fields,
            old_credentials_backend=before.credentials_backend,
            new_credentials_backend=new_backend,
        )
        update_fields["protocol_data"] = swapped

    if backend_changed:
        update_fields["credentials_backend"] = new_backend

    if not update_fields:
        click.echo("(no changes)")
        return

    try:
        after = ctx.store.update(connection_id, **update_fields)
    except DuplicateDisplayNameError as exc:
        click.echo(f"  ✗ duplicate: {exc}", err=True)
        raise click.exceptions.Exit(2)
    plugin_ctx = ctx.make_plugin_context(project_id=before.project_id, request=None)
    try:
        after = asyncio.run(plugin.on_update(before, after, ctx=plugin_ctx))
    except Exception as exc:
        click.echo(f"  ✗ plugin.on_update failed: {exc}", err=True)
        raise click.exceptions.Exit(3)
    serialized = _serialize(after, plugin_public_view=plugin.public_view)
    if as_json:
        click.echo(json.dumps(serialized, indent=2, default=str))
    else:
        click.echo(f"  ✓ updated {serialized['id']}")


# ── delete ──────────────────────────────────────────────────────────


@connections.command("delete")
@click.argument("connection_id")
@click.option("--yes", is_flag=True, help="Skip confirmation")
def delete_cmd(connection_id: str, yes: bool) -> None:
    """Hard-delete a connection."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    if not yes:
        click.confirm(f"Delete connection {record.display_name!r}?", abort=True)
    plugin = get_protocol(record.protocol)
    plugin_ctx = ctx.make_plugin_context(project_id=record.project_id, request=None)
    try:
        asyncio.run(plugin.on_delete(record, ctx=plugin_ctx))
    except Exception as exc:
        click.echo(f"  ✗ plugin.on_delete failed: {exc}", err=True)
        raise click.exceptions.Exit(3)
    if ctx.store.delete(connection_id):
        click.echo(f"  ✓ deleted {connection_id}")
    else:
        click.echo(f"  ✗ already gone {connection_id}", err=True)


# ── test ────────────────────────────────────────────────────────────


@connections.command("test")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def test_cmd(connection_id: str, as_json: bool) -> None:
    """Run the plugin's test() against the live connection."""
    ctx = _get_ctx()
    record = ctx.store.get(connection_id)
    if record is None:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    plugin = get_protocol(record.protocol)
    try:
        decrypted_pd = ctx.router.decrypt(
            record.protocol_data,
            sensitive_field_paths=plugin.sensitive_fields,
            connection_credentials_backend=record.credentials_backend,
        )
    except Exception as exc:
        click.echo(f"  ✗ decrypt failed: {exc}", err=True)
        raise click.exceptions.Exit(3)
    from dataclasses import replace
    decrypted_record = replace(record, protocol_data=decrypted_pd)
    plugin_ctx = ctx.make_plugin_context(project_id=record.project_id, request=None)
    result = asyncio.run(plugin.test(decrypted_record, ctx=plugin_ctx))
    ctx.store.update_test_result(connection_id, ok=result.ok)
    body = {
        "ok": result.ok,
        "status_code": result.status_code,
        "message": result.message,
    }
    if as_json:
        click.echo(json.dumps(body, indent=2, default=str))
    else:
        symbol = "✓" if result.ok else "✗"
        click.echo(f"  {symbol} {body['message'] or ''}")


# ── set-default ─────────────────────────────────────────────────────


@connections.command("set-default")
@click.argument("connection_id")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON")
def set_default_cmd(connection_id: str, as_json: bool) -> None:
    """Mark this connection as default for its (project, protocol, key) group."""
    ctx = _get_ctx()
    try:
        record = ctx.store.set_default(connection_id)
    except ConnectionNotFoundError:
        click.echo(f"  ✗ connection {connection_id!r} not found", err=True)
        raise click.exceptions.Exit(4)
    plugin = get_protocol(record.protocol)
    serialized = _serialize(record, plugin_public_view=plugin.public_view)
    if as_json:
        click.echo(json.dumps(serialized, indent=2, default=str))
    else:
        click.echo(f"  ✓ {connection_id} is now default")


# ── Plugin extra_cli sub-groups ─────────────────────────────────────


def _wire_plugin_subgroups() -> None:
    """Add a sub-group per plugin whose extra_cli() yields commands."""
    for plugin in all_protocols():
        cmds = plugin.extra_cli()
        if not cmds:
            continue
        sub = click.Group(name=plugin.id, help=f"{plugin.display_name}-specific commands")
        for cmd in cmds:
            sub.add_command(cmd)
        connections.add_command(sub)


_wire_plugin_subgroups()


__all__ = ["connections"]
