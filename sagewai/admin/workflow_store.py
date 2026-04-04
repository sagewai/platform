# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Saved workflow store — persistent storage for workflow registry.

Provides a PostgreSQL-backed store for saved workflow definitions with CRUD
operations, versioning, and project-scoped isolation.

Usage::

    from sagewai.admin.workflow_store import SavedWorkflowStore

    store = SavedWorkflowStore("postgresql://user:pass@host/db")
    await store.init()

    wf_id = await store.save(
        name="research-pipeline",
        yaml_content="name: research-pipeline\\nagents: ...",
        description="Multi-agent research workflow",
    )

    wf = await store.get_by_name("research-pipeline")
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from sagewai.core.context import get_current_project

logger = logging.getLogger(__name__)


def _resolve_project(project_id: str | None = None) -> str:
    """Resolve project_id from explicit param, contextvar, or default."""
    if project_id:
        return project_id
    ctx = get_current_project()
    return ctx.project_id if ctx else "default"


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
    """PostgreSQL-backed store for saved workflow definitions.

    Schema is managed by Alembic migrations. Run ``alembic upgrade head``
    before starting the application.
    """

    def __init__(self, database_url: str) -> None:
        self._url = database_url
        self._pool: Any = None

    @property
    def is_connected(self) -> bool:
        return self._pool is not None

    async def init(self) -> None:
        """Initialize the connection pool."""
        try:
            import asyncpg
        except ImportError as exc:
            raise ImportError(
                "asyncpg is required for PostgreSQL. " "Install with: uv add 'sagewai[postgres]'"
            ) from exc

        self._pool = await asyncpg.create_pool(self._url, min_size=1, max_size=5)

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

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
        self._check_connected()
        resolved_project = _resolve_project(project_id)

        # Check if workflow with this name already exists
        existing = await self.get_by_name(name, project_id=resolved_project)

        if existing:
            # Update existing — bump version
            new_version = existing.version + 1

            # Save version history + update main record atomically
            version_id = uuid.uuid4().hex[:12]
            async with self._pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        INSERT INTO saved_workflow_versions
                        (id, workflow_id, version, yaml_content)
                        VALUES ($1, $2, $3, $4)
                        """,
                        version_id,
                        existing.id,
                        new_version,
                        yaml_content,
                    )

                    # Update the main record
                    await conn.execute(
                        """
                        UPDATE saved_workflows
                        SET yaml_content = $1, description = $2, version = $3,
                            updated_at = NOW()
                        WHERE id = $4 AND project_id = $5
                        """,
                        yaml_content,
                        description,
                        new_version,
                        existing.id,
                        resolved_project,
                    )
            return existing.id

        # Create new workflow
        workflow_id = uuid.uuid4().hex[:12]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO saved_workflows
                    (id, project_id, name, description, yaml_content, version,
                     is_active, created_by)
                    VALUES ($1, $2, $3, $4, $5, 1, TRUE, $6)
                    """,
                    workflow_id,
                    resolved_project,
                    name,
                    description,
                    yaml_content,
                    created_by,
                )

                # Also save as version 1
                version_id = uuid.uuid4().hex[:12]
                await conn.execute(
                    """
                    INSERT INTO saved_workflow_versions
                    (id, workflow_id, version, yaml_content)
                    VALUES ($1, $2, 1, $3)
                    """,
                    version_id,
                    workflow_id,
                    yaml_content,
                )

        return workflow_id

    async def get(self, workflow_id: str, *, project_id: str | None = None) -> SavedWorkflow | None:
        """Get a workflow by ID, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM saved_workflows WHERE id = $1 AND project_id = $2",
                workflow_id,
                resolved_project,
            )
        if not row:
            return None
        return self._row_to_record(row)

    async def get_by_name(
        self, name: str, *, project_id: str | None = None
    ) -> SavedWorkflow | None:
        """Get a workflow by name, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM saved_workflows WHERE name = $1 AND project_id = $2",
                name,
                resolved_project,
            )
        if not row:
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
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        conditions.append(f"project_id = ${idx}")
        params.append(resolved_project)
        idx += 1

        if is_active is not None:
            conditions.append(f"is_active = ${idx}")
            params.append(is_active)
            idx += 1

        if search:
            conditions.append(f"(name ILIKE ${idx} OR description ILIKE ${idx})")
            params.append(f"%{search}%")
            idx += 1

        where = " AND ".join(conditions)
        sql = (
            f"SELECT * FROM saved_workflows WHERE {where} "
            f"ORDER BY updated_at DESC LIMIT ${idx} OFFSET ${idx + 1}"
        )
        params.extend([limit, offset])

        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
        return [self._row_to_record(row) for row in rows]

    async def delete(self, workflow_id: str, *, project_id: str | None = None) -> bool:
        """Soft-delete a workflow (set is_active=false)."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE saved_workflows
                SET is_active = FALSE, updated_at = NOW()
                WHERE id = $1 AND project_id = $2
                """,
                workflow_id,
                resolved_project,
            )
        return "UPDATE 1" in result

    async def list_versions(
        self, workflow_id: str, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """List version history for a workflow, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT v.id, v.workflow_id, v.version, v.yaml_content, v.created_at
                FROM saved_workflow_versions v
                JOIN saved_workflows w ON w.id = v.workflow_id
                WHERE v.workflow_id = $1 AND w.project_id = $2
                ORDER BY v.version DESC
                """,
                workflow_id,
                resolved_project,
            )
        return [
            {
                "id": row["id"],
                "workflow_id": row["workflow_id"],
                "version": row["version"],
                "yaml_content": row["yaml_content"],
                "created_at": row["created_at"].timestamp() if row["created_at"] else 0,
            }
            for row in rows
        ]

    async def get_version(
        self, workflow_id: str, version: int, *, project_id: str | None = None
    ) -> str | None:
        """Get yaml_content for a specific version, scoped to project."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT v.yaml_content FROM saved_workflow_versions v
                JOIN saved_workflows w ON w.id = v.workflow_id
                WHERE v.workflow_id = $1 AND v.version = $2 AND w.project_id = $3
                """,
                workflow_id,
                version,
                resolved_project,
            )
        return row["yaml_content"] if row else None

    async def count(self, *, project_id: str | None = None) -> int:
        """Count active saved workflows."""
        self._check_connected()
        resolved_project = _resolve_project(project_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COUNT(*) as cnt FROM saved_workflows WHERE project_id = $1 AND is_active = TRUE",
                resolved_project,
            )
        return row["cnt"] if row else 0

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _row_to_record(self, row: Any) -> SavedWorkflow:
        """Convert an asyncpg Record to a SavedWorkflow."""
        return SavedWorkflow(
            id=row["id"],
            project_id=row.get("project_id", "default"),
            name=row["name"],
            description=row.get("description", ""),
            yaml_content=row["yaml_content"],
            version=row.get("version", 1),
            is_active=row.get("is_active", True),
            created_by=row.get("created_by"),
            created_at=row["created_at"].timestamp() if row.get("created_at") else 0,
            updated_at=row["updated_at"].timestamp() if row.get("updated_at") else 0,
        )

    def _check_connected(self) -> None:
        if self._pool is None:
            raise RuntimeError("SavedWorkflowStore not initialized. Call init() first.")


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
