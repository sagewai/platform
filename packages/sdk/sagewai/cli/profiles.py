# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""sagewai admin profiles — profile CRUD CLI."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import click

from sagewai.sealed.backend import ProfileNotFoundError
from sagewai.sealed.models import ProfileWritePayload
from sagewai.sealed.refs import ProfileRef, resolve_backend


@click.group("profiles")
def profiles_group() -> None:
    """Manage security profiles."""


def _backend(scheme: str = "builtin"):
    return resolve_backend(ProfileRef(scheme=scheme, path=""))


@profiles_group.command("list")
@click.option("--tag", default=None)
def profiles_list(tag: str | None) -> None:
    """List all profiles (metadata only)."""
    profiles = asyncio.run(_backend().list_profiles())
    if tag:
        profiles = [p for p in profiles if tag in p.tags]
    output = [
        {
            "id": p.id,
            "name": p.name,
            "owner": p.owner,
            "tags": p.tags,
            "secret_keys_count": len(p.secret_keys),
            "env_keys_count": len(p.env),
            "last_rotated_at": p.last_rotated_at.isoformat() if p.last_rotated_at else None,
        }
        for p in profiles
    ]
    click.echo(json.dumps(output, indent=2))


@profiles_group.command("get")
@click.argument("profile_id")
def profiles_get(profile_id: str) -> None:
    """Show profile metadata (no secret values)."""
    try:
        md = asyncio.run(_backend().get_profile_metadata(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)
    click.echo(json.dumps(md.model_dump(mode="json"), indent=2, default=str))


@profiles_group.command("get-full")
@click.argument("profile_id")
def profiles_get_full(profile_id: str) -> None:
    """Show full profile WITH decrypted secret values (audit-logged)."""
    if not click.confirm(
        f"This decrypts every secret in {profile_id!r} and writes audit events. Continue?"
    ):
        sys.exit(0)
    try:
        full = asyncio.run(_backend().get_profile(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)
    click.echo(json.dumps(full.model_dump(mode="json"), indent=2, default=str))


@profiles_group.command("create")
@click.argument("profile_id")
@click.option("--from-file", "from_file", type=click.Path(exists=True, dir_okay=False))
def profiles_create(profile_id: str, from_file: str | None) -> None:
    """Create a profile.

    If --from-file is given, reads JSON of ProfileWritePayload shape.
    Otherwise prompts interactively for name + initial values.
    """
    if from_file:
        data = json.loads(Path(from_file).read_text())
        data["id"] = profile_id
        payload = ProfileWritePayload.model_validate(data)
    else:
        name = click.prompt("Name", default=profile_id)
        payload = ProfileWritePayload(id=profile_id, name=name)

    saved = asyncio.run(_backend().save_profile(payload))
    click.echo(f"✓ Created profile {saved.id!r}")


@profiles_group.command("edit")
@click.argument("profile_id")
def profiles_edit(profile_id: str) -> None:
    """Open the profile in $EDITOR (full-form, decrypts secrets)."""
    try:
        full = asyncio.run(_backend().get_profile(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)

    editor = os.environ.get("EDITOR", "vi")
    import tempfile
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    ) as f:
        json.dump(full.model_dump(mode="json"), f, indent=2, default=str)
        tmp_path = f.name

    subprocess.call([editor, tmp_path])

    edited = json.loads(Path(tmp_path).read_text())
    Path(tmp_path).unlink()

    payload = ProfileWritePayload.model_validate({
        **edited,
        "id": profile_id,
    })
    asyncio.run(_backend().save_profile(payload))
    click.echo(f"✓ Updated profile {profile_id!r}")


@profiles_group.command("delete")
@click.argument("profile_id")
@click.option("--yes", is_flag=True)
def profiles_delete(profile_id: str, yes: bool) -> None:
    """Delete a profile."""
    if not yes:
        click.confirm(f"Delete profile {profile_id!r}?", abort=True)
    try:
        asyncio.run(_backend().delete_profile(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)
    click.echo(f"✓ Deleted profile {profile_id!r}")


@profiles_group.command("reveal")
@click.argument("profile_id")
@click.argument("secret_key")
def profiles_reveal(profile_id: str, secret_key: str) -> None:
    """Decrypt and print one secret value (audit-logged)."""
    try:
        full = asyncio.run(_backend().get_profile(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)
    if secret_key not in full.secrets:
        click.echo(f"  ✗ Secret {secret_key!r} not in profile", err=True)
        sys.exit(1)
    click.echo(full.secrets[secret_key])


@profiles_group.command("acl")
@click.argument("profile_id")
@click.argument("action", type=click.Choice(["show", "set", "remove"]))
@click.argument("tool_name", required=False)
@click.argument("keys", required=False)
def profiles_acl(
    profile_id: str, action: str, tool_name: str | None, keys: str | None
) -> None:
    """Show/edit per-CLI ACL on a profile.

    sagewai admin sealed profiles acl <id> show
    sagewai admin sealed profiles acl <id> set <tool> <key1,key2,...>
    sagewai admin sealed profiles acl <id> remove <tool>
    """
    try:
        profile = asyncio.run(_backend().get_profile(profile_id))
    except ProfileNotFoundError:
        click.echo(f"  ✗ Profile {profile_id!r} not found", err=True)
        sys.exit(1)

    new_acl = dict(profile.acl or {})

    if action == "show":
        click.echo(json.dumps(new_acl, indent=2))
        return

    if action in ("set", "remove") and tool_name is None:
        click.echo("  ✗ tool_name required for set/remove", err=True)
        sys.exit(2)

    if action == "set":
        new_keys = [k.strip() for k in (keys or "").split(",") if k.strip()] if keys else []
        new_acl[tool_name] = new_keys
    elif action == "remove":
        new_acl.pop(tool_name, None)

    payload = ProfileWritePayload(
        id=profile.id,
        name=profile.name,
        description=profile.description,
        owner=profile.owner,
        tags=list(profile.tags),
        allowed_workflows=list(profile.allowed_workflows),
        env=dict(profile.env),
        secrets=dict(profile.secrets),
        acl=new_acl,
    )
    asyncio.run(_backend().save_profile(payload))
    click.echo(json.dumps(new_acl, indent=2))
