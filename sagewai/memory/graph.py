# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Graph memory — entity-relationship knowledge graph.

Provides an in-memory graph store that implements the MemoryProvider protocol.
Stores entities and relationships, retrieves connected context via traversal.

For production, swap with a NebulaGraph-backed implementation.
"""

from __future__ import annotations

from typing import Any

from sagewai.core.context import resolve_project_id


class GraphMemory:
    """In-memory knowledge graph for entity-relationship storage.

    Stores entities (nodes) and relationships (edges), retrieves context
    by finding entities mentioned in a query and traversing their connections.

    All data is scoped by ``project_id``.  When not provided explicitly,
    the project is auto-resolved from the active ``ProjectContext`` contextvar,
    falling back to ``"default"``.

    Args:
        max_depth: Maximum traversal depth for context retrieval.
        project_id: Explicit project scope.  ``None`` → auto-resolve.
    """

    def __init__(self, *, max_depth: int = 2, project_id: str | None = None) -> None:
        # project_id → {entity_name → metadata}
        self._entities: dict[str, dict[str, dict[str, Any]]] = {}
        # project_id → [_Relation, ...]
        self._relations: dict[str, list[_Relation]] = {}
        self._max_depth = max_depth
        self._project_id = project_id

    def _resolve_pid(self) -> str:
        """Resolve the effective project_id for this operation."""
        return resolve_project_id(self._project_id)

    def _project_entities(self, pid: str) -> dict[str, dict[str, Any]]:
        return self._entities.setdefault(pid, {})

    def _project_relations(self, pid: str) -> list[_Relation]:
        return self._relations.setdefault(pid, [])

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve context by finding entities in the query and traversing.

        Args:
            query: Search query — entities mentioned are used as starting points.
            top_k: Maximum number of context strings to return.

        Returns:
            List of context strings describing related entities and relationships.
        """
        pid = self._resolve_pid()
        entities = self._project_entities(pid)
        relations = self._project_relations(pid)
        query_lower = query.lower()
        seed_entities = [name for name in entities if name.lower() in query_lower]

        if not seed_entities:
            return []

        # BFS traversal from seed entities
        visited: set[str] = set()
        context_lines: list[str] = []

        queue = [(e, 0) for e in seed_entities]
        while queue and len(context_lines) < top_k:
            entity, depth = queue.pop(0)
            if entity in visited or depth > self._max_depth:
                continue
            visited.add(entity)

            # Add entity info
            meta = entities.get(entity, {})
            if meta:
                meta_str = ", ".join(f"{k}: {v}" for k, v in meta.items())
                context_lines.append(f"{entity} ({meta_str})")
            else:
                context_lines.append(entity)

            # Find connected relations
            for rel in relations:
                if rel.source == entity and rel.target not in visited:
                    context_lines.append(f"{rel.source} --[{rel.relation}]--> {rel.target}")
                    queue.append((rel.target, depth + 1))
                elif rel.target == entity and rel.source not in visited:
                    context_lines.append(f"{rel.source} --[{rel.relation}]--> {rel.target}")
                    queue.append((rel.source, depth + 1))

        return context_lines[:top_k]

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Store an entity in the graph.

        Args:
            content: Entity name/label.
            metadata: Entity properties (type, description, etc.).
        """
        pid = self._resolve_pid()
        self._project_entities(pid)[content] = metadata or {}

    async def add_relation(self, source: str, relation: str, target: str) -> None:
        """Add a relationship between two entities.

        Creates entities automatically if they don't exist.

        Args:
            source: Source entity name.
            relation: Relationship type (e.g. "works_at", "knows").
            target: Target entity name.
        """
        pid = self._resolve_pid()
        entities = self._project_entities(pid)
        if source not in entities:
            entities[source] = {}
        if target not in entities:
            entities[target] = {}
        self._project_relations(pid).append(
            _Relation(source=source, relation=relation, target=target)
        )

    async def get_entity(self, name: str) -> dict[str, Any] | None:
        """Get entity metadata by name."""
        pid = self._resolve_pid()
        entities = self._project_entities(pid)
        if name in entities:
            return entities[name]
        return None

    async def get_relations(self, entity: str) -> list[tuple[str, str, str]]:
        """Get all relations involving an entity.

        Returns:
            List of (source, relation, target) tuples.
        """
        pid = self._resolve_pid()
        relations = self._project_relations(pid)
        return [
            (r.source, r.relation, r.target)
            for r in relations
            if r.source == entity or r.target == entity
        ]

    async def list_entities(
        self,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List entities with optional name search, pagination.

        Returns:
            List of dicts with ``name`` and ``metadata`` keys.
        """
        pid = self._resolve_pid()
        entities = self._project_entities(pid)
        names = sorted(entities.keys())
        if search:
            search_lower = search.lower()
            names = [n for n in names if search_lower in n.lower()]
        page = names[offset : offset + limit]
        return [{"name": n, "metadata": entities[n]} for n in page]

    async def get_neighbors(
        self, entity: str, depth: int = 1
    ) -> list[dict[str, str]]:
        """Get all neighbors of an entity up to a given depth via BFS.

        Returns:
            List of dicts with ``entity`` and ``relation`` keys.
        """
        pid = self._resolve_pid()
        relations = self._project_relations(pid)
        visited: set[str] = {entity}
        neighbors: list[dict[str, str]] = []

        queue: list[tuple[str, int]] = [(entity, 0)]
        while queue:
            current, d = queue.pop(0)
            if d >= depth:
                continue
            for rel in relations:
                if rel.source == current and rel.target not in visited:
                    visited.add(rel.target)
                    neighbors.append({"entity": rel.target, "relation": rel.relation})
                    queue.append((rel.target, d + 1))
                elif rel.target == current and rel.source not in visited:
                    visited.add(rel.source)
                    neighbors.append({"entity": rel.source, "relation": rel.relation})
                    queue.append((rel.source, d + 1))

        return neighbors

    async def delete(self, content: str) -> bool:
        """Remove an entity and its relations."""
        pid = self._resolve_pid()
        entities = self._project_entities(pid)
        if content not in entities:
            return False
        del entities[content]
        self._relations[pid] = [
            r
            for r in self._project_relations(pid)
            if r.source != content and r.target != content
        ]
        return True

    def clear(self) -> None:
        """Remove all entities and relations."""
        self._entities.clear()
        self._relations.clear()

    def __len__(self) -> int:
        pid = self._resolve_pid()
        return len(self._project_entities(pid))


class _Relation:
    """Internal storage for a graph relationship."""

    __slots__ = ("source", "relation", "target")

    def __init__(self, source: str, relation: str, target: str) -> None:
        self.source = source
        self.relation = relation
        self.target = target
