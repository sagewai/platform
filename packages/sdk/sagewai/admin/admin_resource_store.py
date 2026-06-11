# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Generic project-scoped admin control-plane resource store (MT durability).

One store, one table (``admin_resources``), keyed by ``kind`` — the durable
backing for every admin control-plane resource that is file-backed today:
budgets, guardrails, saved workflows, eval datasets, notification
channels/triggers, connector triggers, artifact destinations. Each resource
class is a ``kind``; the store never interprets the per-resource ``data`` blob,
so a new resource type needs no schema change — just a new ``kind`` string.

Mirrors :mod:`sagewai.admin.tenant_agent_store` (no secrets at the store layer):

* **Reads** use ``apply_scope`` / ``row_in_scope`` — own project + inherited
  org-shared (``project_id IS NULL``).
* **Mutations** use ``write_scope_filter`` / ``row_writable`` — own rows only; a
  project context may *read* an org-shared resource but never mutate or delete it
  (raises :class:`ResourceWriteScopeError`).
* **Writes stamp** ``project_id`` from ``ctx`` (never from the request body).

Secret encryption is the **route's** job (a later wiring step): the store keeps
``data`` opaque and persists exactly the JSON it is handed, scoped.

Name uniqueness is enforced by the NULL-safe partial-unique index
``(kind, project_id, name) WHERE name IS NOT NULL`` — one name per
(kind, project) and one global name per kind. A violating insert/update surfaces
as :class:`ResourceConflictError`.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, select
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping
from sagewai.admin.tenancy import RequestContext
from sagewai.db import factory
from sagewai.db.models import AdminResourceModel, Base

_tbl = AdminResourceModel.__table__


class ResourceWriteScopeError(PermissionError):
    """A resource exists but is not writable by the acting context (org-shared
    from a project ctx, or owned by another project). The route maps this to 403."""


class ResourceConflictError(RuntimeError):
    """A resource ``name`` already exists in the (kind, project) scope. The route
    maps this to 409."""


class AdminResourceStore:
    """Persists generic project-scoped admin resources to ``admin_resources``.

    Constructor forms:

    * ``AdminResourceStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``AdminResourceStore(engine=my_engine)``
        Injected engine; used by tests and DI containers.

    No ``identity_store`` is needed — the store keeps ``data`` opaque and does no
    secret handling (that is the route's responsibility).
    """

    def __init__(self, *, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or factory.get_engine()

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------ writes

    async def upsert_for(
        self,
        ctx: RequestContext,
        kind: str,
        resource_id: str,
        data: dict,
        *,
        name: str | None = None,
    ) -> dict:
        """Insert or update ``(kind, resource_id)`` in ``ctx``'s write scope.

        Stamps ``project_id`` from ``ctx`` (own scope only). If a row with this
        ``(kind, resource_id)`` already exists:

        * out of write scope (org-shared from a project ctx, or another
          project's row) → raises :class:`ResourceWriteScopeError`;
        * in scope → updated in place.

        A ``name`` collision within the (kind, project) scope — i.e. a *different*
        resource_id already using ``name`` — surfaces from the partial-unique
        index as :class:`ResourceConflictError`. Returns the stored ``data`` dict.
        """
        scoping.require_ctx(ctx)
        payload = dict(data)
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(_tbl.c.project_id).where(
                        _tbl.c.kind == kind, _tbl.c.resource_id == resource_id
                    )
                )
            ).first()
            if existing is not None:
                if not scoping.row_writable({"project_id": existing[0]}, ctx):
                    raise ResourceWriteScopeError(
                        f"{kind} resource {resource_id!r} is not writable in this scope"
                    )
                try:
                    await conn.execute(
                        sa_update(_tbl)
                        .where(_tbl.c.kind == kind, _tbl.c.resource_id == resource_id)
                        .values(name=name, data=payload, updated_at=now)
                    )
                except IntegrityError as exc:
                    raise ResourceConflictError(
                        f"{kind} resource name {name!r} already exists in this scope"
                    ) from exc
            else:
                try:
                    await conn.execute(
                        _tbl.insert().values(
                            kind=kind,
                            resource_id=resource_id,
                            project_id=ctx.project_id,
                            name=name,
                            data=payload,
                            created_at=now,
                            updated_at=now,
                        )
                    )
                except IntegrityError as exc:
                    raise ResourceConflictError(
                        f"{kind} resource name {name!r} already exists in this scope"
                    ) from exc
        return payload

    async def delete_for(self, ctx: RequestContext, kind: str, resource_id: str) -> bool:
        """Delete ``(kind, resource_id)`` in ``ctx``'s write scope. True if one row went.

        A project actor cannot delete an org-shared row — the write-scope filter
        excludes inherited rows, so the delete matches zero rows and returns False
        (rather than raising). Cross-project deletes likewise return False.
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa_delete(_tbl).where(
                    _tbl.c.kind == kind,
                    _tbl.c.resource_id == resource_id,
                    scoping.write_scope_filter(_tbl, ctx),
                )
            )
        return result.rowcount == 1

    # ------------------------------------------------------------------- reads

    async def list_for(self, ctx: RequestContext, kind: str) -> list[dict]:
        """List ``kind`` resources visible to ``ctx`` (own + org-shared).

        Returns the stored ``data`` dicts, ordered by creation. Unlike the
        name-keyed config stores, no shadow-resolution is applied — every
        in-scope row of this kind is returned (callers key by resource_id/name).
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(
                            select(_tbl).where(_tbl.c.kind == kind), _tbl, ctx
                        ).order_by(_tbl.c.created_at)
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r["data"]) for r in rows]

    async def get_for(self, ctx: RequestContext, kind: str, resource_id: str) -> dict | None:
        """Fetch ``(kind, resource_id)`` if visible to ``ctx``, else ``None``.

        ``None`` when the row is out of read scope (another project's row) — the
        post-fetch :func:`scoping.row_in_scope` check mirrors the read filter, so
        a cross-project resource is hidden rather than revealed.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_tbl).where(_tbl.c.kind == kind, _tbl.c.resource_id == resource_id)
                    )
                )
                .mappings()
                .first()
            )
        if row is None or not scoping.row_in_scope(row, ctx):
            return None
        return dict(row["data"])

    async def get_by_name_for(self, ctx: RequestContext, kind: str, name: str) -> dict | None:
        """Fetch the ``kind`` resource named ``name`` visible to ``ctx``, else None.

        Read scope (own project + org-shared). When both a project row and an
        org-shared row of the same ``(kind, name)`` are visible, the project row
        wins (project shadows global), matching the config stores' resolution.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(
                            select(_tbl).where(and_(_tbl.c.kind == kind, _tbl.c.name == name)),
                            _tbl,
                            ctx,
                        )
                    )
                )
                .mappings()
                .all()
            )
        if not rows:
            return None
        chosen = next((r for r in rows if r["project_id"] is not None), rows[0])
        return dict(chosen["data"])


__all__ = [
    "AdminResourceStore",
    "ResourceConflictError",
    "ResourceWriteScopeError",
]
