# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant-scoped LLM provider config store (W4+ of the multi-tenancy roadmap).

The pattern-setting config store for multi-tenant mode — its shape is the
template the agent and connection stores follow. Mirrors the
``PostgresAgentStore`` pattern (engine-injectable, SQLAlchemy Core, schema
created on SQLite via :meth:`init`; Alembic owns the Postgres schema).

Three primitives compose here:

* **Scoping** (:mod:`sagewai.admin.scoping`) — every read uses ``apply_scope``
  (own project + inherited org-shared); every mutation uses
  ``write_scope_filter`` / ``row_writable`` (own rows only), so a project actor
  can *use* an org-shared provider but never mutate or delete it.
* **Per-project secret encryption** (:mod:`sagewai.admin.tenant_keys`) — secret
  fields in ``config`` are encrypted under the project data key on write and
  decrypted on :meth:`get_decrypted`; the data key for a row is derived from the
  row's own ``project_id`` (org-shared rows use the org master key).
* **Secret field helpers** (:mod:`sagewai.admin.provider_secrets`) — the same
  walker / redactor the file-backed store uses, so secrets never leave the store
  in cleartext on :meth:`list`.

Scope-shadowing: a project that defines a provider with the same name as an
org-shared one sees only its own (the project row wins). :meth:`list` resolves
this by name, project rows taking precedence.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping, tenant_keys
from sagewai.admin.provider_secrets import is_encrypted, redact_secrets, walk_secret_fields
from sagewai.db import factory
from sagewai.db.models import Base, ProviderModel
from sagewai.sealed.crypto import Crypto, SecretCorrupted

_tbl = ProviderModel.__table__


class ProviderSecretDecryptionError(RuntimeError):
    """A provider secret could not be decrypted and execution must fail closed."""


class PostgresProviderStore:
    """Persists tenant-scoped provider configs to the ``provider`` table.

    Constructor forms:

    * ``PostgresProviderStore(identity_store=ident)``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``PostgresProviderStore(engine=my_engine, identity_store=ident)``
        Injected engine; used by tests and DI containers.

    ``identity_store`` is the :class:`~sagewai.admin.identity_store.IdentityStore`
    that owns the per-project data keys (see :mod:`sagewai.admin.tenant_keys`);
    it is required for secret encryption / decryption.
    """

    def __init__(
        self,
        *,
        engine: AsyncEngine | None = None,
        identity_store: Any = None,
    ) -> None:
        self._engine = engine or factory.get_engine()
        self._identity = identity_store

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ----------------------------------------------------------------- helpers
    async def _encrypt_config(self, config: dict, ctx) -> None:
        """Encrypt every plaintext secret field in ``config`` in place.

        Each secret is wrapped under the data key for ``ctx.project_id``
        (org master key when org-shared). Already-encrypted values are skipped,
        so re-saving a redacted-then-restored config never double-wraps.
        """
        targets: list = []
        walk_secret_fields(config, lambda parent, k: targets.append((parent, k)))
        for parent, k in targets:
            v = parent.get(k)
            if isinstance(v, str) and v and not v.startswith(Crypto.PREFIX):
                parent[k] = await tenant_keys.encrypt_for_project(
                    self._identity, ctx.org_id, ctx.project_id, v
                )

    def _public(self, data: dict, is_default: bool) -> dict:
        """The caller-safe view of a record: default flag carried, secrets redacted."""
        out = dict(data)
        out["default"] = is_default
        return redact_secrets(out)

    # ------------------------------------------------------------------ writes
    async def upsert(self, provider: dict, *, ctx) -> dict:
        """Insert or update a provider in ``ctx``'s write scope (own rows only).

        ``provider_name`` is unique per scope, so an existing row in the same
        scope is updated rather than duplicated. Secret fields are encrypted
        before persisting. When ``default`` is truthy, every other provider in
        the same write scope is cleared so at most one default remains.
        """
        scoping.require_ctx(ctx)
        rec = dict(provider)
        rec["project_id"] = ctx.project_id
        config = dict(rec.get("config") or {})
        await self._encrypt_config(config, ctx)
        rec["config"] = config
        pname = rec.get("provider_name", "")
        rec.setdefault("id", f"prov-{ctx.project_id or 'global'}-{pname}-{uuid.uuid4().hex[:8]}")
        is_default = bool(rec.get("default"))
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(_tbl.c.id).where(
                        scoping.write_scope_filter(_tbl, ctx),
                        _tbl.c.provider_name == pname,
                    )
                )
            ).first()
            row_id = existing[0] if existing else rec["id"]
            rec["id"] = row_id
            if existing:
                await conn.execute(
                    sa_update(_tbl)
                    .where(_tbl.c.id == row_id)
                    .values(data=rec, is_default=is_default, updated_at=now)
                )
            else:
                await conn.execute(
                    _tbl.insert().values(
                        id=row_id,
                        project_id=ctx.project_id,
                        provider_name=pname,
                        is_default=is_default,
                        data=rec,
                        created_at=now,
                        updated_at=now,
                    )
                )
            if is_default:
                await conn.execute(
                    sa_update(_tbl)
                    .where(scoping.write_scope_filter(_tbl, ctx), _tbl.c.id != row_id)
                    .values(is_default=False)
                )
        return self._public(rec, is_default)

    async def set_default(self, provider_id: str, *, ctx) -> dict | None:
        """Make ``provider_id`` the sole default in its write scope.

        Returns ``None`` if the row is not writable by ``ctx`` (a project actor
        may not set an org-shared provider as default). Clears every other
        default in the scope first, so exactly one default remains.
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == provider_id)))
                .mappings()
                .first()
            )
            if row is None or not scoping.row_writable(row, ctx):
                return None
            await conn.execute(
                sa_update(_tbl)
                .where(scoping.write_scope_filter(_tbl, ctx))
                .values(is_default=False)
            )
            await conn.execute(
                sa_update(_tbl).where(_tbl.c.id == provider_id).values(is_default=True)
            )
        return {"id": provider_id, "default": True}

    async def delete(self, provider_id: str, *, ctx) -> bool:
        """Delete a provider in ``ctx``'s write scope. Returns True if one row went.

        A project actor cannot delete an org-shared row — the write-scope filter
        excludes inherited rows, so the delete matches zero rows and returns False.
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa_delete(_tbl).where(
                    _tbl.c.id == provider_id, scoping.write_scope_filter(_tbl, ctx)
                )
            )
        return result.rowcount == 1

    # ------------------------------------------------------------------- reads
    async def list(self, *, ctx) -> list[dict]:
        """List providers visible to ``ctx`` (own + org-shared), secrets redacted.

        Scope-shadowing: when a project defines a provider whose name matches an
        org-shared one, only the project's row is returned. Project rows are
        sorted first so they win the by-name de-duplication.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(select(_tbl), _tbl, ctx).order_by(_tbl.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        by_name: dict[str, dict] = {}
        # project rows first (project_id is None -> True sorts last) so a
        # project's provider shadows an org-shared one of the same name.
        for r in sorted(rows, key=lambda r: r["project_id"] is None):
            name = r["provider_name"]
            if name in by_name:
                continue
            data = dict(r["data"])
            data["id"] = r["id"]
            data.setdefault("project_id", r["project_id"])
            data["_scope"] = "org" if r["project_id"] is None else "project"
            by_name[name] = self._public(data, bool(r["is_default"]))
        return list(by_name.values())

    async def get_decrypted(self, provider_id: str, *, ctx) -> dict | None:
        """Fetch a single provider with its secrets decrypted, or ``None``.

        Returns ``None`` when the row is not visible to ``ctx`` (different
        project, no inheritance) — the post-fetch scope check mirrors
        :func:`scoping.scope_filter`. Secrets are decrypted under the data key of
        the row's own ``project_id``. A value that cannot be decrypted (corrupt
        or missing key) raises :class:`ProviderSecretDecryptionError` so execution
        cannot silently fall back to an inherited provider.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.id == provider_id)))
                .mappings()
                .first()
            )
        if row is None or not scoping.row_in_scope(row, ctx):
            return None
        data = dict(row["data"])
        data["id"] = row["id"]
        data["default"] = bool(row["is_default"])
        config = dict(data.get("config") or {})
        await self._decrypt_config(config, row["project_id"], ctx)
        data["config"] = config
        return data

    async def _decrypt_config(self, config: dict, row_project_id, ctx) -> None:
        """Decrypt fernet-prefixed secret fields in ``config`` in place.

        Decrypts under the data key of the row's own ``project_id`` (org master
        key for org-shared rows). An undecryptable value (corrupt / missing key)
        fails closed; callers must not choose another provider as fallback.
        """
        if not is_encrypted({"config": config}):
            return
        targets: list = []
        walk_secret_fields(config, lambda parent, k: targets.append((parent, k)))
        for parent, k in targets:
            v = parent.get(k)
            if isinstance(v, str) and v.startswith(Crypto.PREFIX):
                try:
                    parent[k] = await tenant_keys.decrypt_for_project(
                        self._identity, ctx.org_id, row_project_id, v
                    )
                except SecretCorrupted as exc:
                    raise ProviderSecretDecryptionError(
                        "provider secret could not be decrypted"
                    ) from exc

    async def list_decrypted(self, *, ctx) -> list[dict]:
        """All providers visible to ``ctx`` (own + org-shared) with secrets DECRYPTED.

        Internal only — for model aggregation / connection probing. **Never return
        over HTTP** (use :meth:`list`, which redacts). Mirrors the file store's
        ``list_providers_decrypted`` but scoped by ``ctx``. Unlike :meth:`list` it
        does not collapse shadowed names — aggregation wants every reachable model.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(select(_tbl), _tbl, ctx).order_by(_tbl.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        out: list[dict] = []
        for r in rows:
            data = dict(r["data"])
            data["id"] = r["id"]
            data.setdefault("project_id", r["project_id"])
            data["default"] = bool(r["is_default"])
            config = dict(data.get("config") or {})
            await self._decrypt_config(config, r["project_id"], ctx)
            data["config"] = config
            out.append(data)
        return out

    async def has_encrypted_secrets(self) -> bool:
        """True if ANY provider row carries an encrypted secret (across all projects).

        Used at startup to fail closed: if encrypted tenant secrets exist, the org
        master key must resolve before the backend serves. Intentionally unscoped —
        it is a custody check, not a tenant read.
        """
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(_tbl.c.data))).mappings().all()
        for r in rows:
            data = r["data"] or {}
            if is_encrypted({"config": data.get("config") or {}}):
                return True
        return False


__all__ = ["PostgresProviderStore", "ProviderSecretDecryptionError"]
