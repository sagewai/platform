# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet CLI commands — manage fleet workers and enrollment keys.

Provides the ``sagewai fleet`` command group with subcommands for
worker registration, listing, and enrollment key management.

Usage::

    sagewai fleet register --name my-gpu-box --org acme --models gpt-4o,llama3-70b
    sagewai fleet list-workers --org acme
    sagewai fleet create-key --org acme --name onboarding-key --max-uses 10
    sagewai fleet list-keys --org acme
    sagewai fleet revoke-key <key-id>
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timedelta, timezone

import click

from sagewai.fleet.models import (
    EnrollmentKey,
    WorkerApprovalStatus,
    WorkerCapabilities,
    WorkerRecord,
)
from sagewai.fleet.normalizer import ModelNormalizer


# ---------------------------------------------------------------------------
# In-memory registry for local/demo use (gateway will use Postgres)
# ---------------------------------------------------------------------------


class _LocalFleetRegistry:
    """Singleton in-memory registry for CLI demo/local use."""

    _instance: _LocalFleetRegistry | None = None

    def __init__(self) -> None:
        self.workers: dict[str, WorkerRecord] = {}
        self.keys: dict[str, EnrollmentKey] = {}

    @classmethod
    def get(cls) -> _LocalFleetRegistry:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ---------------------------------------------------------------------------
# Duration parser
# ---------------------------------------------------------------------------


def _parse_duration(value: str) -> timedelta:
    """Parse a human-friendly duration string into a timedelta.

    Supported formats: ``7d``, ``24h``, ``30m``, ``90s``, or combinations
    like ``1d12h``.

    Raises:
        click.BadParameter: If the format is unrecognized.
    """
    pattern = re.compile(r"(\d+)([dhms])")
    matches = pattern.findall(value.lower())
    if not matches:
        raise click.BadParameter(
            f"Invalid duration '{value}'. Use format like '7d', '24h', '30m'."
        )

    total = timedelta()
    for amount, unit in matches:
        n = int(amount)
        if unit == "d":
            total += timedelta(days=n)
        elif unit == "h":
            total += timedelta(hours=n)
        elif unit == "m":
            total += timedelta(minutes=n)
        elif unit == "s":
            total += timedelta(seconds=n)
    return total


# ---------------------------------------------------------------------------
# Fleet command group
# ---------------------------------------------------------------------------


@click.group("fleet")
def fleet_group() -> None:
    """Manage distributed fleet workers and enrollment keys.

    \b
    Examples:
      sagewai fleet register --name gpu-box --org acme --models gpt-4o,llama3
      sagewai fleet list-workers --org acme --status approved
      sagewai fleet create-key --org acme --name onboarding --max-uses 10
      sagewai fleet list-keys --org acme
      sagewai fleet revoke-key <key-id>
    """


@fleet_group.command("register")
@click.option("--name", required=True, help="Worker name.")
@click.option("--org", required=True, help="Organization ID.")
@click.option("--models", required=True, help="Comma-separated model list.")
@click.option("--pool", default="default", help="Worker pool.")
@click.option("--labels", default=None, help="Comma-separated key=value labels.")
@click.option("--enrollment-key", default=None, help="Enrollment key for auto-approval.")
@click.option("--cloud-url", default=None, help="Cloud gateway URL (for remote registration).")
def register(
    name: str,
    org: str,
    models: str,
    pool: str,
    labels: str | None,
    enrollment_key: str | None,
    cloud_url: str | None,
) -> None:
    """Register this machine as a fleet worker."""
    model_list = [m.strip() for m in models.split(",") if m.strip()]
    canonical = ModelNormalizer.canonical_list(model_list)

    parsed_labels: dict[str, str] = {}
    if labels:
        for pair in labels.split(","):
            pair = pair.strip()
            if "=" in pair:
                k, v = pair.split("=", 1)
                parsed_labels[k.strip()] = v.strip()

    caps = WorkerCapabilities(
        models_supported=model_list,
        models_canonical=canonical,
        max_concurrent=1,
        labels=parsed_labels,
        pool=pool,
    )

    if cloud_url:
        click.echo(f"Remote registration to {cloud_url} is not yet implemented.")
        click.echo("Use local registration (omit --cloud-url) for now.")
        return

    worker_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    # Determine approval status
    approval = WorkerApprovalStatus.PENDING
    if enrollment_key:
        registry = _LocalFleetRegistry.get()
        # Find matching key by comparing hash against stored keys
        matched = False
        key_hash = hashlib.sha256(enrollment_key.encode()).hexdigest()
        for ek in registry.keys.values():
            if ek.is_usable() and ek.org_id == org and ek.key_hash == key_hash:
                matched = True
                ek.current_uses += 1
                break
        if matched:
            approval = WorkerApprovalStatus.APPROVED
        else:
            click.echo("Warning: enrollment key not recognized. Worker registered as PENDING.")

    record = WorkerRecord(
        id=worker_id,
        name=name,
        org_id=org,
        capabilities=caps,
        approval_status=approval,
        registered_at=now,
        approved_at=now if approval == WorkerApprovalStatus.APPROVED else None,
    )

    registry = _LocalFleetRegistry.get()
    registry.workers[worker_id] = record

    click.echo(f"Registered worker '{name}' (id: {worker_id[:12]}...)")
    click.echo(f"  Organization : {org}")
    click.echo(f"  Pool         : {pool}")
    click.echo(f"  Models       : {', '.join(canonical)}")
    click.echo(f"  Status       : {approval.value}")
    if parsed_labels:
        click.echo(f"  Labels       : {parsed_labels}")


@fleet_group.command("list-workers")
@click.option("--org", required=True, help="Organization ID.")
@click.option(
    "--status",
    default=None,
    type=click.Choice(["pending", "approved", "rejected", "revoked"]),
    help="Filter by status.",
)
@click.option("--pool", default=None, help="Filter by pool.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def list_workers(org: str, status: str | None, pool: str | None, as_json: bool) -> None:
    """List registered fleet workers."""
    import json

    registry = _LocalFleetRegistry.get()
    workers = [w for w in registry.workers.values() if w.org_id == org]

    if status:
        workers = [w for w in workers if w.approval_status.value == status]
    if pool:
        workers = [w for w in workers if w.capabilities.pool == pool]

    if as_json:
        click.echo(json.dumps([w.model_dump(mode="json") for w in workers], indent=2))
        return

    if not workers:
        click.echo("No workers found.")
        return

    for w in workers:
        hb = w.last_heartbeat.isoformat()[:19] if w.last_heartbeat else "never"
        click.echo(
            f"  {w.id[:12]}  {w.name:<20s}  {w.approval_status.value:<10s}  "
            f"{w.capabilities.pool:<12s}  {', '.join(w.capabilities.models_canonical):<30s}  "
            f"heartbeat={hb}"
        )


@fleet_group.command("create-key")
@click.option("--org", required=True, help="Organization ID.")
@click.option("--name", required=True, help="Key name.")
@click.option("--max-uses", default=None, type=int, help="Maximum registrations.")
@click.option("--expires", default=None, help="Expiration duration (e.g. '7d', '24h').")
@click.option("--pools", default=None, help="Comma-separated allowed pools.")
@click.option("--models", default=None, help="Comma-separated allowed models.")
def create_key(
    org: str,
    name: str,
    max_uses: int | None,
    expires: str | None,
    pools: str | None,
    models: str | None,
) -> None:
    """Create an enrollment key for fleet worker registration."""
    key_id = str(uuid.uuid4())
    raw_key = f"swk_{uuid.uuid4().hex}"  # prefix for identification
    now = datetime.now(timezone.utc)

    expires_at: datetime | None = None
    if expires:
        delta = _parse_duration(expires)
        expires_at = now + delta

    allowed_pools = [p.strip() for p in pools.split(",") if p.strip()] if pools else []
    allowed_models = [m.strip() for m in models.split(",") if m.strip()] if models else []

    ek = EnrollmentKey(
        id=key_id,
        org_id=org,
        name=name,
        key_hash=f"local:{raw_key}",  # placeholder — production uses bcrypt
        max_uses=max_uses,
        expires_at=expires_at,
        allowed_pools=allowed_pools,
        allowed_models=allowed_models,
        created_at=now,
        created_by="cli",
    )

    registry = _LocalFleetRegistry.get()
    registry.keys[key_id] = ek

    click.echo(f"Enrollment key created: {raw_key}")
    click.echo("Save this key - it will not be shown again.")
    click.echo(f"  ID      : {key_id[:12]}...")
    click.echo(f"  Name    : {name}")
    click.echo(f"  Org     : {org}")
    if max_uses:
        click.echo(f"  Max uses: {max_uses}")
    if expires_at:
        click.echo(f"  Expires : {expires_at.isoformat()[:19]}Z")
    if allowed_pools:
        click.echo(f"  Pools   : {', '.join(allowed_pools)}")
    if allowed_models:
        click.echo(f"  Models  : {', '.join(allowed_models)}")


@fleet_group.command("list-keys")
@click.option("--org", required=True, help="Organization ID.")
@click.option("--json", "as_json", is_flag=True, help="Output raw JSON.")
def list_keys(org: str, as_json: bool) -> None:
    """List enrollment keys."""
    import json

    registry = _LocalFleetRegistry.get()
    keys = [k for k in registry.keys.values() if k.org_id == org]

    if as_json:
        click.echo(json.dumps([k.model_dump(mode="json") for k in keys], indent=2))
        return

    if not keys:
        click.echo("No enrollment keys found.")
        return

    for k in keys:
        status = "revoked" if k.revoked else ("expired" if k.is_expired() else "active")
        uses = f"{k.current_uses}/{k.max_uses}" if k.max_uses else f"{k.current_uses}/unlimited"
        expires = k.expires_at.isoformat()[:19] if k.expires_at else "never"
        click.echo(
            f"  {k.id[:12]}  {k.name:<20s}  {status:<10s}  uses={uses:<15s}  expires={expires}"
        )


@fleet_group.command("revoke-key")
@click.argument("key_id")
def revoke_key(key_id: str) -> None:
    """Revoke an enrollment key."""
    registry = _LocalFleetRegistry.get()

    # Find by full or partial ID
    target: EnrollmentKey | None = None
    for ek in registry.keys.values():
        if ek.id == key_id or ek.id.startswith(key_id):
            target = ek
            break

    if target is None:
        click.echo(f"Error: enrollment key '{key_id}' not found.", err=True)
        raise SystemExit(1)

    if target.revoked:
        click.echo(f"Key '{target.name}' is already revoked.")
        return

    target.revoked = True
    click.echo(f"Revoked enrollment key '{target.name}' ({target.id[:12]}...).")
