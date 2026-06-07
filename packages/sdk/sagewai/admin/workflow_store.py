# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Saved workflow store — persistent storage for workflow registry.

Backed by SQLAlchemy Core, compatible with both SQLite (default) and
PostgreSQL. The class name and all public method signatures are
unchanged so callers require no modification.

Usage::

    from sagewai.admin.workflow_store import SavedWorkflowStore

    # Default engine (SQLite or $SAGEWAI_DATABASE_URL):
    store = SavedWorkflowStore()
    await store.init()

    # Explicit URL (old positional form — still supported):
    store = SavedWorkflowStore("postgresql://user:pass@host/db")
    await store.init()

    # Injected engine (test / DI form):
    store = SavedWorkflowStore(engine=my_async_engine)

    wf_id = await store.save(
        name="research-pipeline",
        yaml_content="name: research-pipeline\\nagents: ...",
        description="Multi-agent research workflow",
    )

    wf = await store.get_by_name("research-pipeline")
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.core.context import get_current_project
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import Base, SavedWorkflowModel, SavedWorkflowVersionModel

logger = logging.getLogger(__name__)

_wf_tbl = SavedWorkflowModel.__table__
_ver_tbl = SavedWorkflowVersionModel.__table__


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


def _dt_to_ts(val: Any) -> float:
    """Convert a datetime (or existing float) to a Unix timestamp."""
    if val is None:
        return 0.0
    if isinstance(val, datetime):
        return val.timestamp()
    if isinstance(val, (int, float)):
        return float(val)
    return 0.0


@dataclass
class SavedWorkflow:
    """A saved workflow definition."""

    id: str
    project_id: str = "default"
    name: str = ""
    description: str = ""
    yaml_content: str = ""
    version: int = 1
    is_active: bool = True
    created_by: str | None = None
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "id": self.id,
            "project_id": self.project_id,
            "name": self.name,
            "description": self.description,
            "yaml_content": self.yaml_content,
            "version": self.version,
            "is_active": self.is_active,
            "created_by": self.created_by,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SavedWorkflowStore:
    """SQLAlchemy Core store for saved workflow definitions — SQLite or PostgreSQL.

    Constructor forms (all equivalent from caller perspective):

    * ``SavedWorkflowStore()``
        Uses the process-wide engine from :func:`sagewai.db.factory.get_engine`.
    * ``SavedWorkflowStore("postgresql://user:pass@host/db")``
        Positional URL string — back-compat with old callers.
    * ``SavedWorkflowStore(engine=my_engine)``
        Injected engine; used by tests and DI containers.
    * ``SavedWorkflowStore(database_url="...")``
        Keyword URL — also supported.

    On SQLite, :meth:`init` creates the schema via ``create_all``.
    On PostgreSQL, :meth:`init` is a no-op (Alembic owns the schema).
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()

    @property
    def is_connected(self) -> bool:
        """True once the engine is available (immediately after construction)."""
        return self._engine is not None

    async def init(self) -> None:
        """Bootstrap the schema on SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # kept for back-compat (old code called initialize())
    async def initialize(self) -> None:
        """Alias for :meth:`init`."""
        await self.init()

    async def close(self) -> None:
        """No-op — the factory or caller owns the engine lifecycle."""

    async def save(
        self,
        *,
        name: str,
        yaml_content: str,
        description: str = "",
        created_by: str | None = None,
        project_id: str | None = None,
    ) -> str:
        """Save or update a workflow. Auto-bumps version on update. Returns workflow id."""
        resolved_project = _resolve_project(project_id)

        # Check if workflow with this name already exists
        existing = await self.get_by_name(name, project_id=resolved_project)

        if existing:
            # Update existing — bump version atomically:
            # 1. Insert a new version row.
            # 2. Update the parent record's version/yaml/description.
            # Both steps happen inside a single engine.begin() transaction.
            new_version = existing.version + 1
            version_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc)

            async with self._engine.begin() as conn:
                await conn.execute(
                    insert(_ver_tbl).values(
                        id=version_id,
                        workflow_id=existing.id,
                        version=new_version,
                        yaml_content=yaml_content,
                    )
                )
                await conn.execute(
                    update(_wf_tbl)
                    .where(
                        _wf_tbl.c.id == existing.id,
                        _wf_tbl.c.project_id == resolved_project,
                    )
                    .values(
                        yaml_content=yaml_content,
                        description=description,
                        version=new_version,
                        updated_at=now,
                    )
                )
            return existing.id

        # Create new workflow + version 1, all in one transaction.
        workflow_id = uuid.uuid4().hex[:12]
        version_id = uuid.uuid4().hex[:12]
        now = datetime.now(timezone.utc)

        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_wf_tbl).values(
                    id=workflow_id,
                    project_id=resolved_project,
                    name=name,
                    description=description,
                    yaml_content=yaml_content,
                    version=1,
                    is_active=True,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            await conn.execute(
                insert(_ver_tbl).values(
                    id=version_id,
                    workflow_id=workflow_id,
                    version=1,
                    yaml_content=yaml_content,
                )
            )

        return workflow_id

    async def get(self, workflow_id: str, *, project_id: str | None = None) -> SavedWorkflow | None:
        """Get a workflow by ID, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = select(_wf_tbl).where(
            _wf_tbl.c.id == workflow_id,
            _wf_tbl.c.project_id == resolved_project,
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    async def get_by_name(
        self, name: str, *, project_id: str | None = None
    ) -> SavedWorkflow | None:
        """Get an active workflow by name, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = select(_wf_tbl).where(
            _wf_tbl.c.name == name,
            _wf_tbl.c.project_id == resolved_project,
            _wf_tbl.c.is_active == True,  # noqa: E712
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_record(row)

    async def list(
        self,
        *,
        is_active: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[SavedWorkflow]:
        """List saved workflows with optional filtering, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = select(_wf_tbl).where(_wf_tbl.c.project_id == resolved_project)

        if is_active is not None:
            stmt = stmt.where(_wf_tbl.c.is_active == is_active)

        if search:
            search_lower = search.lower()
            # Use LIKE for portable case-insensitive substring match.
            # SQLite LIKE is case-insensitive for ASCII; Postgres ILIKE would be
            # ideal but isn't portable. Lower() + LIKE covers both.
            from sqlalchemy import func as sa_func
            stmt = stmt.where(
                sa_func.lower(_wf_tbl.c.name).like(f"%{search_lower}%")
                | sa_func.lower(_wf_tbl.c.description).like(f"%{search_lower}%")
            )

        stmt = stmt.order_by(_wf_tbl.c.updated_at.desc()).limit(limit).offset(offset)

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [self._row_to_record(row) for row in rows]

    async def delete(self, workflow_id: str, *, project_id: str | None = None) -> bool:
        """Soft-delete a workflow (set is_active=False). Returns True if a row was updated."""
        resolved_project = _resolve_project(project_id)
        now = datetime.now(timezone.utc)
        stmt = (
            update(_wf_tbl)
            .where(
                _wf_tbl.c.id == workflow_id,
                _wf_tbl.c.project_id == resolved_project,
            )
            .values(is_active=False, updated_at=now)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount > 0

    async def list_versions(
        self, workflow_id: str, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List version history for a workflow, scoped to project (newest first)."""
        resolved_project = _resolve_project(project_id)
        # Join to parent to enforce project scope.
        stmt = (
            select(_ver_tbl)
            .join(_wf_tbl, _wf_tbl.c.id == _ver_tbl.c.workflow_id)
            .where(
                _ver_tbl.c.workflow_id == workflow_id,
                _wf_tbl.c.project_id == resolved_project,
            )
            .order_by(_ver_tbl.c.version.desc())
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        return [
            {
                "id": row["id"],
                "workflow_id": row["workflow_id"],
                "version": row["version"],
                "yaml_content": row["yaml_content"],
                "created_at": _dt_to_ts(row["created_at"]),
            }
            for row in rows
        ]

    async def get_version(
        self, workflow_id: str, version: int, *, project_id: str | None = None
    ) -> str | None:
        """Get yaml_content for a specific version, scoped to project."""
        resolved_project = _resolve_project(project_id)
        stmt = (
            select(_ver_tbl.c.yaml_content)
            .join(_wf_tbl, _wf_tbl.c.id == _ver_tbl.c.workflow_id)
            .where(
                _ver_tbl.c.workflow_id == workflow_id,
                _ver_tbl.c.version == version,
                _wf_tbl.c.project_id == resolved_project,
            )
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.first()
        return row[0] if row else None

    async def count(self, *, project_id: str | None = None) -> int:
        """Count active saved workflows in a project."""
        from sqlalchemy import func as sa_func

        resolved_project = _resolve_project(project_id)
        stmt = select(sa_func.count()).select_from(_wf_tbl).where(
            _wf_tbl.c.project_id == resolved_project,
            _wf_tbl.c.is_active == True,  # noqa: E712
        )
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            val = result.scalar()
        return val or 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_record(row: Any) -> SavedWorkflow:
        """Convert a SQLAlchemy row mapping to a SavedWorkflow."""
        return SavedWorkflow(
            id=row["id"],
            project_id=row["project_id"],
            name=row["name"],
            description=row.get("description") or "",
            yaml_content=row["yaml_content"],
            version=row.get("version") or 1,
            is_active=bool(row.get("is_active", True)),
            created_by=row.get("created_by"),
            created_at=_dt_to_ts(row.get("created_at")),
            updated_at=_dt_to_ts(row.get("updated_at")),
        )

    def _check_connected(self) -> None:
        if self._engine is None:
            raise RuntimeError("SavedWorkflowStore not initialized.")


class InMemorySavedWorkflowStore:
    """In-memory fallback for saved workflow definitions (dev/testing)."""

    def __init__(self) -> None:
        self._workflows: dict[str, SavedWorkflow] = {}
        self._versions: dict[str, list[dict[str, Any]]] = {}

    @property
    def is_connected(self) -> bool:
        return True

    async def save(
        self,
        *,
        name: str,
        yaml_content: str,
        description: str = "",
        created_by: str | None = None,
        project_id: str | None = None,
    ) -> str:
        resolved_project = _resolve_project(project_id)

        # Check existing
        for wf in self._workflows.values():
            if wf.name == name and wf.project_id == resolved_project:
                wf.version += 1
                wf.yaml_content = yaml_content
                wf.description = description
                wf.updated_at = time.time()
                self._versions.setdefault(wf.id, []).append(
                    {
                        "id": uuid.uuid4().hex[:12],
                        "workflow_id": wf.id,
                        "version": wf.version,
                        "yaml_content": yaml_content,
                        "created_at": time.time(),
                    }
                )
                return wf.id

        workflow_id = uuid.uuid4().hex[:12]
        now = time.time()
        wf = SavedWorkflow(
            id=workflow_id,
            project_id=resolved_project,
            name=name,
            description=description,
            yaml_content=yaml_content,
            version=1,
            is_active=True,
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        self._workflows[workflow_id] = wf
        self._versions[workflow_id] = [
            {
                "id": uuid.uuid4().hex[:12],
                "workflow_id": workflow_id,
                "version": 1,
                "yaml_content": yaml_content,
                "created_at": now,
            }
        ]
        return workflow_id

    async def get(self, workflow_id: str, *, project_id: str | None = None) -> SavedWorkflow | None:
        resolved_project = _resolve_project(project_id)
        wf = self._workflows.get(workflow_id)
        if wf and wf.project_id == resolved_project:
            return wf
        return None

    async def get_by_name(
        self, name: str, *, project_id: str | None = None
    ) -> SavedWorkflow | None:
        resolved_project = _resolve_project(project_id)
        for wf in self._workflows.values():
            if wf.name == name and wf.project_id == resolved_project and wf.is_active:
                return wf
        return None

    async def list(
        self,
        *,
        is_active: bool | None = None,
        search: str | None = None,
        limit: int = 50,
        offset: int = 0,
        project_id: str | None = None,
    ) -> list[SavedWorkflow]:
        resolved_project = _resolve_project(project_id)
        items = [wf for wf in self._workflows.values() if wf.project_id == resolved_project]
        if is_active is not None:
            items = [wf for wf in items if wf.is_active == is_active]
        if search:
            search_lower = search.lower()
            items = [
                wf
                for wf in items
                if search_lower in wf.name.lower() or search_lower in wf.description.lower()
            ]
        items.sort(key=lambda w: w.updated_at, reverse=True)
        return items[offset : offset + limit]

    async def delete(self, workflow_id: str, *, project_id: str | None = None) -> bool:
        resolved_project = _resolve_project(project_id)
        wf = self._workflows.get(workflow_id)
        if wf and wf.project_id == resolved_project:
            wf.is_active = False
            return True
        return False

    async def list_versions(
        self, workflow_id: str, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        resolved_project = _resolve_project(project_id)
        wf = self._workflows.get(workflow_id)
        if not wf or wf.project_id != resolved_project:
            return []
        return list(reversed(self._versions.get(workflow_id, [])))

    async def get_version(
        self, workflow_id: str, version: int, *, project_id: str | None = None
    ) -> str | None:
        resolved_project = _resolve_project(project_id)
        wf = self._workflows.get(workflow_id)
        if not wf or wf.project_id != resolved_project:
            return None
        for v in self._versions.get(workflow_id, []):
            if v["version"] == version:
                return v["yaml_content"]
        return None

    async def count(self, *, project_id: str | None = None) -> int:
        resolved_project = _resolve_project(project_id)
        return sum(
            1
            for wf in self._workflows.values()
            if wf.project_id == resolved_project and wf.is_active
        )
