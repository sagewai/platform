# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant-scoped playground agent store (W4+ of the multi-tenancy roadmap).

Mirrors the :mod:`sagewai.admin.provider_store` pattern exactly, but without
any secret-encryption logic — agent specs carry no credentials.

Three scoping primitives compose here (same as the provider store):

* **Reads** use ``apply_scope`` (own project + inherited org-shared).
* **Mutations** use ``write_scope_filter`` (own rows only — no touching org-shared
  rows from a project context).
* **Shadow-resolution** in :meth:`list` and :meth:`get`: a project row with the
  same name as a global (org-shared) row wins — the project's definition shadows
  the inherited one.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping
from sagewai.db import factory
from sagewai.db.models import Base, TenantAgentModel

_tbl = TenantAgentModel.__table__


class PostgresTenantAgentStore:
    """Persists tenant-scoped playground agent specs to the ``agent`` table.

    Constructor forms:

    * ``PostgresTenantAgentStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``PostgresTenantAgentStore(engine=my_engine)``
        Injected engine; used by tests and DI containers.

    No ``identity_store`` is needed — agents carry no secrets.
    """

    def __init__(self, *, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or factory.get_engine()

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------ writes

    async def create(self, spec: dict, *, ctx) -> dict:
        """Insert or update an agent in ``ctx``'s write scope.

        ``spec["name"]`` is unique per scope.  If a row with that name already
        exists in the write scope, its ``spec`` and ``updated_at`` are updated
        rather than a duplicate row inserted.  Returns the spec dict with ``id``
        and ``project_id`` stamped in.
        """
        scoping.require_ctx(ctx)
        rec = dict(spec)
        name = rec["name"]
        rec["project_id"] = ctx.project_id
        rec.setdefault(
            "id", f"agent-{ctx.project_id or 'global'}-{name}-{uuid.uuid4().hex[:8]}"
        )
        now = datetime.now(timezone.utc)
        async with self._engine.begin() as conn:
            existing = (
                await conn.execute(
                    select(_tbl.c.id).where(
                        scoping.write_scope_filter(_tbl, ctx),
                        _tbl.c.name == name,
                    )
                )
            ).first()
            row_id = existing[0] if existing else rec["id"]
            rec["id"] = row_id
            if existing:
                await conn.execute(
                    sa_update(_tbl)
                    .where(_tbl.c.id == row_id)
                    .values(spec=rec, updated_at=now)
                )
            else:
                await conn.execute(
                    _tbl.insert().values(
                        id=row_id,
                        project_id=ctx.project_id,
                        name=name,
                        spec=rec,
                        created_at=now,
                        updated_at=now,
                    )
                )
        return rec

    async def delete(self, name: str, *, ctx) -> bool:
        """Delete an agent by name in ``ctx``'s write scope. Returns True if one row went.

        A project actor cannot delete an org-shared row — the write-scope filter
        excludes inherited rows, so the delete matches zero rows and returns False.
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                sa_delete(_tbl).where(
                    _tbl.c.name == name,
                    scoping.write_scope_filter(_tbl, ctx),
                )
            )
        return result.rowcount == 1

    async def rename(self, old: str, new: str, *, ctx) -> dict | None:
        """Rename an agent within ``ctx``'s write scope.

        Loads the write-scoped row named ``old``; returns None if not found.
        Updates the ``name`` column to ``new`` and ``spec["name"]`` to ``new``.
        Returns None (rather than raising) if a row named ``new`` already exists
        in the write scope (guards the unique-index constraint).
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            row = (
                await conn.execute(
                    select(_tbl).where(
                        scoping.write_scope_filter(_tbl, ctx),
                        _tbl.c.name == old,
                    )
                )
            ).mappings().first()
            if row is None:
                return None
            # Guard: check new name doesn't already exist in write scope
            collision = (
                await conn.execute(
                    select(_tbl.c.id).where(
                        scoping.write_scope_filter(_tbl, ctx),
                        _tbl.c.name == new,
                    )
                )
            ).first()
            if collision is not None:
                return None
            updated_spec = dict(row["spec"])
            updated_spec["name"] = new
            now = datetime.now(timezone.utc)
            try:
                await conn.execute(
                    sa_update(_tbl)
                    .where(_tbl.c.id == row["id"])
                    .values(name=new, spec=updated_spec, updated_at=now)
                )
            except IntegrityError:
                return None
        updated_spec["id"] = row["id"]
        updated_spec["project_id"] = row["project_id"]
        return updated_spec

    # ------------------------------------------------------------------- reads

    async def list(self, *, ctx) -> list[dict]:
        """List agents visible to ``ctx`` (own + org-shared), shadow-resolved by name.

        When a project defines an agent with the same name as an org-shared one,
        only the project's row is returned (project rows shadow global ones).
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(select(_tbl), _tbl, ctx).order_by(
                            _tbl.c.created_at
                        )
                    )
                )
                .mappings()
                .all()
            )
        by_name: dict[str, dict] = {}
        # project rows first (project_id is None -> True sorts last) so a
        # project's agent shadows an org-shared one of the same name.
        for r in sorted(rows, key=lambda r: r["project_id"] is None):
            name = r["name"]
            if name in by_name:
                continue
            data = dict(r["spec"])
            data["id"] = r["id"]
            data["project_id"] = r["project_id"]
            by_name[name] = data
        return list(by_name.values())

    async def get(self, name: str, *, ctx) -> dict | None:
        """Fetch an agent by name visible to ``ctx``, or None.

        Shadow-resolution: loads all rows in read-scope matching ``name``; the
        project-scoped row wins over global when both exist.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                await conn.execute(
                    scoping.apply_scope(select(_tbl), _tbl, ctx).where(
                        _tbl.c.name == name
                    )
                )
            ).mappings().all()
        if not rows:
            return None
        # prefer project-scoped row over global
        chosen = next((r for r in rows if r["project_id"] is not None), rows[0])
        data = dict(chosen["spec"])
        data["id"] = chosen["id"]
        data["project_id"] = chosen["project_id"]
        return data


__all__ = ["PostgresTenantAgentStore"]
