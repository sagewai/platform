# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sagewai admin sealed — master-key management + status."""
from __future__ import annotations

import asyncio
import base64
import json
import sys

import click
from cryptography.fernet import Fernet
from mnemonic import Mnemonic

from sagewai.sealed.master_key import (
    DEFAULT_KEY_PATH,
    MasterKeyMissing,
    resolve_master_key,
    store_master_key,
)


@click.group("sealed")
def sealed_group() -> None:
    """Sealed master-key + audit + cascade management."""


@sealed_group.command("init")
def sealed_init() -> None:
    """First-run master-key generation."""
    try:
        resolve_master_key()
        click.echo("Master key already configured. Use `sealed status` to inspect.")
        sys.exit(0)
    except MasterKeyMissing:
        pass

    click.echo("\n  No master key found. Sealed needs one to encrypt every secret.\n")
    click.echo("  Where should it live?")
    click.echo("    1) Environment variable SAGEWAI_MASTER_KEY  (containers, CI)")
    click.echo("    2) OS keychain (recommended for workstations)")
    click.echo(f"    3) {DEFAULT_KEY_PATH} file (mode 0600)")
    choice = click.prompt("\n  Choose [1/2/3]", type=click.Choice(["1", "2", "3"]))

    new_key = Fernet.generate_key()
    destination = {"1": "env-var", "2": "keychain", "3": "file"}[choice]

    if destination == "env-var":
        click.echo("\n  Set this env var in your shell profile / container config:\n")
        click.echo(f"    export SAGEWAI_MASTER_KEY='{new_key.decode('ascii')}'\n")
    else:
        store_master_key(new_key, destination)
        click.echo(f"\n  Master key stored in {destination}.")

    # Backup phrase
    mnemo = Mnemonic("english")
    phrase = mnemo.to_mnemonic(new_key[:32])  # First 32 bytes encoded as 24 words
    click.echo("\n  BACKUP NOW -- losing this key means losing every encrypted secret.\n")
    click.echo("  Backup phrase (write down or paste into your password manager):\n")
    click.echo(f"      {phrase}\n")
    click.confirm("  Have you saved the backup phrase?", abort=True)
    click.echo("\n  Sealed initialized.")
    click.echo("\n  Next: `sagewai admin profiles create my-first-profile`")


@sealed_group.command("status")
def sealed_status() -> None:
    """Print master-key + sealed-config status."""
    try:
        _, source = resolve_master_key()
        configured = True
    except MasterKeyMissing:
        configured = False
        source = "none"

    from sagewai.admin.state_file import AdminStateFile

    state = AdminStateFile()
    sealed_cfg = state.get_sealed_config()

    info = {
        "master_key_configured": configured,
        "master_key_source": source,
        "master_key_last_rotated_at": sealed_cfg.get("master_key_last_rotated_at"),
        "audit_retention_days": sealed_cfg.get("audit_retention_days", 365),
        "reveal_rate_limit_per_admin_per_hour": sealed_cfg.get(
            "reveal_rate_limit_per_admin_per_hour", 30
        ),
    }
    click.echo(json.dumps(info, indent=2, default=str))


@sealed_group.command("rotate-master-key")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def sealed_rotate(yes: bool) -> None:
    """Rotate the master key — re-encrypts every secret."""
    from sagewai.sealed.refs import ProfileRef, resolve_backend

    backend = resolve_backend(ProfileRef(scheme="builtin", path=""))

    profiles = asyncio.run(backend.list_profiles())
    secret_count = sum(len(p.secret_keys) for p in profiles)

    click.echo("\n  Rotating master key for built-in store.")
    click.echo(f"  Profiles: {len(profiles)}")
    click.echo(f"  Total secrets: {secret_count}")
    if not yes:
        click.confirm("\n  Proceed?", abort=True)

    new_key = Fernet.generate_key()
    re_count = asyncio.run(backend.rotate_master_key(new_key))
    click.echo(f"\n  ✓ Re-encrypted {re_count} secrets")

    # Determine where the OLD key was stored
    _, old_source = resolve_master_key()

    # Store new key in same place
    store_master_key(new_key, old_source)
    click.echo(f"  ✓ New key written to {old_source}")

    # Backup phrase for the new key
    mnemo = Mnemonic("english")
    phrase = mnemo.to_mnemonic(new_key[:32])
    click.echo(f"\n  Backup phrase for the new key:\n\n      {phrase}\n")


@sealed_group.command("restore")
def sealed_restore() -> None:
    """Re-derive the master key from a backup phrase (24 words)."""
    phrase = click.prompt("Enter backup phrase (24 words)", hide_input=False)
    mnemo = Mnemonic("english")
    if not mnemo.check(phrase):
        click.echo("  ✗ Invalid backup phrase.", err=True)
        sys.exit(1)
    seed = mnemo.to_seed(phrase)[:32]
    new_key = base64.urlsafe_b64encode(seed)

    click.echo("\n  Where should the recovered key live?")
    click.echo("    1) Env var instructions")
    click.echo("    2) OS keychain")
    click.echo("    3) ~/.sagewai/master.key file")
    choice = click.prompt("Choose [1/2/3]", type=click.Choice(["1", "2", "3"]))
    destination = {"1": "env-var", "2": "keychain", "3": "file"}[choice]
    store_master_key(new_key, destination)

    # Verify by attempting to decrypt one secret in any existing profile
    from sagewai.sealed.refs import ProfileRef, resolve_backend

    backend = resolve_backend(ProfileRef(scheme="builtin", path=""))
    profiles = asyncio.run(backend.list_profiles())
    for p in profiles:
        if p.secret_keys:
            try:
                full = asyncio.run(backend.get_profile(p.id))
                _ = next(iter(full.secrets.values()))  # one decrypt is enough to verify
                click.echo(f"  ✓ Verified by decrypting a secret in profile {p.id!r}.")
                return
            except Exception as exc:
                click.echo(f"  ✗ Verification decrypt failed: {exc}", err=True)
                sys.exit(1)
    click.echo("  ✓ Key restored. (No existing profiles to verify against.)")


async def _get_store():
    """Construct a PostgresStore using SAGEWAI_DATABASE_URL."""
    import os

    from sagewai.core.stores.postgres import PostgresStore

    url = os.environ.get("SAGEWAI_DATABASE_URL")
    if not url:
        raise click.UsageError(
            "SAGEWAI_DATABASE_URL not set; revocation requires Postgres."
        )
    store = PostgresStore(database_url=url)
    await store.initialize()
    return store


@sealed_group.command("revoke")
@click.argument("profile_id")
@click.argument("secret_key", required=False, default=None)
@click.option("--reason", required=True, help="Reason for revocation (audit)")
@click.option("--hard", is_flag=True, help="Also abort in-flight runs")
@click.option("--yes", is_flag=True, help="Skip confirmation prompt")
def sealed_revoke(profile_id, secret_key, reason, hard, yes):
    """Revoke a secret or whole profile."""

    async def _run():
        from sagewai.sealed.audit import AuditWriter
        from sagewai.sealed.revocation import (
            RevocationConflictError,
            RevocationRegistry,
        )

        store = await _get_store()
        try:
            reg = RevocationRegistry(store, audit_writer=AuditWriter(store))

            # If hard, preview affected runs first
            if hard and not yes:
                rows = await store._pool.fetch(
                    """
                    SELECT run_id FROM workflow_runs
                    WHERE status = 'running'
                      AND security_profile_ref = $1
                      AND ($2::text IS NULL OR $2 = ANY(effective_secret_keys))
                    """,
                    profile_id,
                    secret_key,
                )
                count = len(rows)
                click.echo(
                    f"\n  Hard revoke will mark {count} in-flight run(s) as failed:\n"
                )
                for row in rows[:10]:
                    click.echo(f"    - {row['run_id']}")
                if count > 10:
                    click.echo(f"    ... and {count - 10} more")
                click.confirm("\n  Continue?", abort=True)

            # Bulk profile revoke (no secret_key) requires explicit current_keys
            # which the CLI does not currently auto-discover. Explicit per-key
            # revoke is the v1 path.
            if secret_key is None:
                click.echo(
                    "  Bulk profile revoke (omitting secret_key) is not "
                    "supported via CLI in v1. Specify --reason and a "
                    "secret_key explicitly.",
                    err=True,
                )
                raise click.Abort()

            try:
                rows = await reg.revoke(
                    profile_id=profile_id,
                    secret_key=secret_key,
                    reason=reason,
                    actor_id="cli",
                    hard=hard,
                )
            except RevocationConflictError as exc:
                click.echo(f"  ✗ {exc}", err=True)
                raise click.Abort()

            for r in rows:
                click.echo(f"  ✓ Revoked {r.profile_id}/{r.secret_key} (id={r.id})")
        finally:
            await store.close()

    asyncio.run(_run())


@sealed_group.command("lift-revocation")
@click.argument("revocation_id", type=int)
@click.option("--yes", is_flag=True)
def sealed_lift(revocation_id, yes):
    """Lift a previous revocation."""

    async def _run():
        from sagewai.sealed.audit import AuditWriter
        from sagewai.sealed.revocation import (
            RevocationConflictError,
            RevocationRegistry,
        )

        store = await _get_store()
        try:
            reg = RevocationRegistry(store, audit_writer=AuditWriter(store))
            if not yes:
                click.confirm(f"Lift revocation {revocation_id}?", abort=True)
            try:
                r = await reg.lift(revocation_id, actor_id="cli")
            except (LookupError, RevocationConflictError) as exc:
                click.echo(f"  ✗ {exc}", err=True)
                raise click.Abort()
            click.echo(f"  ✓ Lifted {r.profile_id}/{r.secret_key}")
        finally:
            await store.close()

    asyncio.run(_run())


@sealed_group.command("list-revocations")
@click.option("--profile", default=None)
@click.option("--include-lifted", is_flag=True)
@click.option("--limit", type=int, default=200)
def sealed_list_revocations(profile, include_lifted, limit):
    """List active (or all) revocations as JSON."""
    import json as _json

    async def _run():
        from sagewai.sealed.revocation import RevocationRegistry

        store = await _get_store()
        try:
            reg = RevocationRegistry(store)
            rows = await (
                reg.list_all(profile_id=profile, include_lifted=True, limit=limit)
                if include_lifted
                else reg.list_active(profile_id=profile, limit=limit)
            )
            click.echo(
                _json.dumps(
                    [r.model_dump(mode="json") for r in rows],
                    indent=2,
                    default=str,
                )
            )
        finally:
            await store.close()

    asyncio.run(_run())
