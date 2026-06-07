# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Durable, hash-chained, per-tenant audit emitter (W8 of the multi-tenancy roadmap).

An append-only emitter over the ``audit_event`` table (see
:class:`sagewai.db.models.AuditEventModel`). Each event belongs to a
**per-``(org_id, project_id)`` hash chain** — ``project_id IS NULL`` is the
org-level chain. Within a chain, ``seq`` is the event's position (starting at 1)
and::

    hash = sha256(prev_hash || canonical_json(event))

links every event to its predecessor, so the chain is end-to-end tamper-evident.

Each chain has a **tip checkpoint** row in ``audit_chain_head`` recording its
last ``seq`` and tip ``hash`` (see :class:`sagewai.db.models.AuditChainHeadModel`).
The checkpoint gives the emitter two properties an in-table chain alone cannot:

- **Reliable appends under concurrency.** ``append`` advances the head under
  optimistic concurrency and retries on conflict, so concurrent writers to the
  same chain cannot lose events to a ``seq`` race (the per-chain unique index is
  the backstop that turns a race into a retry, not a fork).
- **Deletion detection.** Deleting the last event — or the whole chain — leaves
  no gap, so a re-walk of the survivors looks valid. ``verify_chain`` compares
  the walked tip against the checkpoint and flags a truncated chain. Combined
  with the in-table chain it detects insertion, edit, reorder, and deletion
  (mid-chain *and* tail).

**Authorization (W0 RFC §3/§5).** Audit does **not** inherit the org-shared
rule: a project chain read returns only ``project_id = P`` and is never combined
with the org-level ``project_id IS NULL`` chain. The public read/verify API
takes a :class:`~sagewai.admin.tenancy.RequestContext`, derives the chain from
it, and enforces ``audit:read`` (``org:owner``/``org:admin`` over any chain in
their org; ``project:admin`` over their own chain only). Cross-scope reads raise
:class:`~sagewai.admin.identity_store.TenantAccessError` (HTTP 404, no existence
leak); an in-scope actor lacking ``audit:read`` raises :class:`AuditPermissionError`
(HTTP 403). Org-admin **aggregation** (:meth:`TenantAuditStore.read_chains`)
reads independent chains side-by-side — it never merges them into one logical
chain, and verification stays chain-local. The raw ``(org_id, project_id)``
selectors are private helpers for that aggregation and for verification.

Mirrors the :class:`~sagewai.admin.identity_store.IdentityStore` pattern:
engine-injectable, SQLAlchemy Core, schema created on SQLite via :meth:`init`;
on Postgres the schema comes from Alembic migration ``010_tenant_audit``.

This module is the emitter only — wiring ``append``/read calls into routes / the
auth middleware is a separate workstream. ``append`` is the system emitter: its
``(org_id, project_id)`` are resolved from ``ctx`` at the call site, never from
request input.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql.elements import ColumnElement

from sagewai.admin.identity_store import TenantAccessError
from sagewai.admin.tenancy import (
    SCOPE_ADMIN,
    SCOPE_READ,
    RequestContext,
)
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import AuditChainHeadModel, AuditEventModel, Base

_audit = AuditEventModel.__table__
_head = AuditChainHeadModel.__table__

# Roles that hold ``audit:read`` (W0 RFC §5). Org owners/admins read any chain in
# their org; project admins read only their own project's chain.
_ORG_ADMIN_ROLES = frozenset({"org:owner", "org:admin"})
_PROJECT_AUDIT_ROLE = "project:admin"
# A read also needs a read-capable token scope.
_AUDIT_READ_SCOPES = frozenset({SCOPE_READ, SCOPE_ADMIN})

# Bound on optimistic-concurrency retries for a single append. Comfortably above
# realistic same-chain write fan-out; exceeding it raises rather than spins.
_MAX_APPEND_RETRIES = 64

# Sentinel so "no project_id given" (default to the actor's own scope) is
# distinguishable from an explicit ``project_id=None`` (the org-level chain).
_UNSET: Any = object()


class AuditPermissionError(Exception):
    """An in-scope actor lacks ``audit:read``. Maps to HTTP 403 at the route layer."""


class AuditAppendError(Exception):
    """:meth:`TenantAuditStore.append` exhausted its retries under contention."""


class _HeadConflictError(Exception):
    """Internal: the chain head advanced under us between read and update; retry."""


@dataclass(frozen=True)
class ChainVerification:
    """The result of re-walking and re-hashing a single ``(org, project)`` chain.

    ``ok`` is ``True`` only when every event is present, in order, hashes to its
    stored value, and the chain tip matches the checkpoint. On failure,
    ``broken_at`` is the ``seq`` where the first inconsistency was found (the
    *expected* seq for a gap) and ``reason`` names the tamper class detected.
    """

    ok: bool
    length: int
    broken_at: int | None = None
    reason: str | None = None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: Any) -> datetime:
    """Normalise a stored timestamp to a timezone-aware UTC datetime.

    SQLite round-trips DateTime as naive values; Postgres returns aware ones.
    Treating naive values as UTC makes the canonical serialisation — and thus
    the chain hash — identical whether ``created_at`` was just produced in
    Python or read back from either dialect.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _canonical_json(obj: Any) -> str:
    """Deterministic JSON: sorted keys, no whitespace — stable across processes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _canonical_event(
    *,
    org_id: str,
    project_id: str | None,
    actor_user_id: str | None,
    action: str,
    target_type: str | None,
    target_id: str | None,
    metadata: dict[str, Any] | None,
    seq: int,
    created_at: Any,
) -> dict[str, Any]:
    """The canonical, hash-covered representation of an event.

    Covers every immutable field including ``seq`` and ``created_at`` (normalised
    to a stable ISO string) so that reorder and edit are both detectable.
    """
    return {
        "org_id": org_id,
        "project_id": project_id,
        "actor_user_id": actor_user_id,
        "action": action,
        "target_type": target_type,
        "target_id": target_id,
        "metadata": metadata or {},
        "seq": int(seq),
        "created_at": _coerce_dt(created_at).isoformat(),
    }


def _chain_hash(prev_hash: str | None, event: dict[str, Any]) -> str:
    """``sha256(prev_hash || canonical_json(event))`` — the chain link."""
    payload = (prev_hash or "") + _canonical_json(event)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _chain_pred(project_col: ColumnElement[Any], project_id: str | None) -> ColumnElement[bool]:
    """Scope-strict chain selector. NEVER inherits the org-level NULL chain."""
    if project_id is None:
        return project_col.is_(None)
    return project_col == project_id


class TenantAuditStore:
    """Append-only, hash-chained audit store, one chain per ``(org, project)``."""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        database_url: str | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------ append
    async def append(
        self,
        org_id: str,
        project_id: str | None,
        action: str,
        *,
        actor_user_id: str | None = None,
        target_type: str | None = None,
        target_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append an event to its ``(org_id, project_id)`` chain.

        The system emitter — ``(org_id, project_id)`` are resolved from ``ctx``
        at the call site, never from request input. Reads the chain head for the
        next ``seq``, links onto its tip hash, inserts the event, and advances the
        head under optimistic concurrency. Concurrent appends to the same chain
        retry on conflict (the per-chain unique ``seq`` index is the backstop), so
        no event is lost to a race. Raises :class:`AuditAppendError` only if the
        retry budget is exhausted.
        """
        meta = dict(metadata or {})
        last_exc: Exception | None = None
        for _attempt in range(_MAX_APPEND_RETRIES):
            created_at = _now()
            try:
                async with self._engine.begin() as conn:
                    head = (
                        await conn.execute(
                            select(_head.c.id, _head.c.seq, _head.c.hash).where(
                                _head.c.org_id == org_id,
                                _chain_pred(_head.c.project_id, project_id),
                            )
                        )
                    ).first()
                    if head is None:
                        seq = 1
                        prev_hash: str | None = None
                    else:
                        seq = int(head.seq) + 1
                        prev_hash = head.hash
                    event = _canonical_event(
                        org_id=org_id,
                        project_id=project_id,
                        actor_user_id=actor_user_id,
                        action=action,
                        target_type=target_type,
                        target_id=target_id,
                        metadata=meta,
                        seq=seq,
                        created_at=created_at,
                    )
                    digest = _chain_hash(prev_hash, event)
                    await conn.execute(
                        insert(_audit).values(
                            org_id=org_id,
                            project_id=project_id,
                            actor_user_id=actor_user_id,
                            action=action,
                            target_type=target_type,
                            target_id=target_id,
                            metadata=meta,
                            seq=seq,
                            prev_hash=prev_hash,
                            hash=digest,
                            created_at=created_at,
                        )
                    )
                    if head is None:
                        await conn.execute(
                            insert(_head).values(
                                org_id=org_id,
                                project_id=project_id,
                                seq=seq,
                                hash=digest,
                                updated_at=created_at,
                            )
                        )
                    else:
                        # Optimistic: only advance if no one else moved the tip.
                        res = await conn.execute(
                            update(_head)
                            .where(_head.c.id == head.id, _head.c.seq == head.seq)
                            .values(seq=seq, hash=digest, updated_at=created_at)
                        )
                        if res.rowcount != 1:
                            raise _HeadConflictError
                return {
                    "org_id": org_id,
                    "project_id": project_id,
                    "actor_user_id": actor_user_id,
                    "action": action,
                    "target_type": target_type,
                    "target_id": target_id,
                    "metadata": meta,
                    "seq": seq,
                    "prev_hash": prev_hash,
                    "hash": digest,
                    "created_at": created_at,
                }
            except (IntegrityError, OperationalError, _HeadConflictError) as exc:
                last_exc = exc
                continue
        raise AuditAppendError(
            f"could not append to audit chain after {_MAX_APPEND_RETRIES} attempts"
        ) from last_exc

    # -------------------------------------------------------- public (ctx) API
    async def read_chain(
        self, ctx: RequestContext, *, project_id: Any = _UNSET
    ) -> list[dict[str, Any]]:
        """Read the chain ``ctx`` is authorized to see (default: its own scope).

        Enforces ``audit:read`` and the §3 non-inheritance rule. Cross-scope
        targets raise :class:`TenantAccessError` (404); an in-scope actor without
        ``audit:read`` raises :class:`AuditPermissionError` (403).
        """
        target = self._authorize(ctx, project_id)
        return await self._read_chain(ctx.org_id, target)

    async def verify_chain(
        self, ctx: RequestContext, *, project_id: Any = _UNSET
    ) -> ChainVerification:
        """Verify the chain ``ctx`` is authorized to see (same authz as :meth:`read_chain`)."""
        target = self._authorize(ctx, project_id)
        return await self._verify_chain(ctx.org_id, target)

    async def read_chains(
        self, ctx: RequestContext, project_ids: list[str | None]
    ) -> dict[str | None, list[dict[str, Any]]]:
        """Org-admin aggregation: read several chains **side-by-side**, not merged.

        Returns a mapping ``{project_id_or_None: events}``. Each chain stays
        independent and individually verifiable — there is no combined logical
        chain. Requires an org-admin role; raises :class:`AuditPermissionError`
        otherwise.
        """
        if not (ctx.roles & _ORG_ADMIN_ROLES):
            raise AuditPermissionError("audit aggregation requires an org admin role")
        if not (ctx.scopes & _AUDIT_READ_SCOPES):
            raise AuditPermissionError("audit:read requires a read or admin scope")
        return {pid: await self._read_chain(ctx.org_id, pid) for pid in project_ids}

    def _authorize(self, ctx: RequestContext, project_id: Any) -> str | None:
        """Resolve + authorize the chain ``ctx`` may read. Returns the target project_id.

        ``project_id`` unset → the actor's own scope (``ctx.project_id``).
        """
        requested = ctx.project_id if project_id is _UNSET else project_id
        if not (ctx.roles & _ORG_ADMIN_ROLES):
            # A project actor may only ever touch its own project's chain. Any
            # other target (another project, or the org-level None chain audit
            # does not inherit) hides existence -> 404, before any 403 check.
            if requested != ctx.project_id:
                raise TenantAccessError("audit chain not found")
            if _PROJECT_AUDIT_ROLE not in ctx.roles:
                raise AuditPermissionError("audit:read denied for this role")
        # org admins: audit:read over any chain within their own org. Reads also
        # require a read-capable token scope.
        if not (ctx.scopes & _AUDIT_READ_SCOPES):
            raise AuditPermissionError("audit:read requires a read or admin scope")
        return requested

    # --------------------------------------------------------- raw selectors
    async def _read_chain(self, org_id: str, project_id: str | None) -> list[dict[str, Any]]:
        """Raw, unauthorized chain read, ordered by ``seq``. Scope-strict (no NULL
        inheritance). Private — callers go through :meth:`read_chain`/:meth:`read_chains`."""
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(_audit)
                        .where(_audit.c.org_id == org_id, _chain_pred(_audit.c.project_id, project_id))
                        .order_by(_audit.c.seq)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def _read_head(self, org_id: str, project_id: str | None) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_head).where(
                            _head.c.org_id == org_id, _chain_pred(_head.c.project_id, project_id)
                        )
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def _verify_chain(self, org_id: str, project_id: str | None) -> ChainVerification:
        """Raw chain verification. Private — callers go through :meth:`verify_chain`.

        Walks the chain in ``seq`` order checking three per-event invariants —
        contiguous sequence (a gap ⇒ deletion), ``prev_hash`` linkage (a break ⇒
        insertion/reorder), and stored ``hash`` recomputes from content (a
        mismatch ⇒ edit) — then compares the surviving tip against the chain
        checkpoint to catch tail/full-chain deletion (which leave no gap).
        """
        rows = await self._read_chain(org_id, project_id)
        head = await self._read_head(org_id, project_id)
        prev_hash: str | None = None
        expected_seq = 1
        for row in rows:
            seq = int(row["seq"])
            if seq != expected_seq:
                return ChainVerification(
                    ok=False,
                    length=len(rows),
                    broken_at=expected_seq,
                    reason=f"sequence gap or reorder: expected seq {expected_seq}, found {seq}",
                )
            if row["prev_hash"] != prev_hash:
                return ChainVerification(
                    ok=False,
                    length=len(rows),
                    broken_at=seq,
                    reason="prev_hash linkage broken (inserted or reordered event)",
                )
            event = _canonical_event(
                org_id=row["org_id"],
                project_id=row["project_id"],
                actor_user_id=row["actor_user_id"],
                action=row["action"],
                target_type=row["target_type"],
                target_id=row["target_id"],
                metadata=row["metadata"],
                seq=seq,
                created_at=row["created_at"],
            )
            if row["hash"] != _chain_hash(prev_hash, event):
                return ChainVerification(
                    ok=False,
                    length=len(rows),
                    broken_at=seq,
                    reason="hash mismatch (event content edited)",
                )
            prev_hash = row["hash"]
            expected_seq += 1

        # The checkpoint is the authority on the expected tip — this is what
        # catches deletion of the last event or the whole chain (no gap to find).
        if head is None:
            if rows:
                return ChainVerification(
                    ok=False,
                    length=len(rows),
                    broken_at=int(rows[-1]["seq"]),
                    reason="chain head checkpoint missing while events exist",
                )
            return ChainVerification(ok=True, length=0)
        head_seq = int(head["seq"])
        if not rows:
            return ChainVerification(
                ok=False,
                length=0,
                broken_at=head_seq,
                reason=f"all events deleted (checkpoint expects seq {head_seq})",
            )
        last = rows[-1]
        if int(last["seq"]) != head_seq or last["hash"] != head["hash"]:
            return ChainVerification(
                ok=False,
                length=len(rows),
                broken_at=head_seq,
                reason=(
                    f"chain tip mismatch: checkpoint expects seq {head_seq}, "
                    f"found seq {int(last['seq'])} (tail truncated or appended out of band)"
                ),
            )
        return ChainVerification(ok=True, length=len(rows))


__all__ = [
    "TenantAuditStore",
    "ChainVerification",
    "AuditPermissionError",
    "AuditAppendError",
]
