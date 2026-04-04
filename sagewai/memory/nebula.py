# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""NebulaGraph-backed knowledge graph memory."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from sagewai.core.context import resolve_project_id

if TYPE_CHECKING:
    from sagewai.intelligence.extractors.protocol import (
        EntityExtractor,
        RelationExtractor,
    )

logger = logging.getLogger(__name__)

try:
    from nebula3.Config import Config as NebulaConfig
    from nebula3.gclient.net import ConnectionPool
except ImportError:
    ConnectionPool = None  # type: ignore[assignment,misc]
    NebulaConfig = None  # type: ignore[assignment,misc]


async def _extract_relations_llm(text: str) -> list[tuple[str, str, str]]:
    """Extract (subject, predicate, object) triples from text using LLM.

    This is the legacy fallback used when no ``RelationExtractor`` is
    configured on the :class:`NebulaGraphMemory` instance.
    """
    import json

    import litellm

    prompt = (
        "Extract entity-relationship triples from this text. "
        "Return as JSON array of [subject, predicate, object] arrays. "
        'Example: [["Python", "is_a", "Language"]]\n\n'
        f"Text: {text}\n\nJSON:"
    )
    response = await litellm.acompletion(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
    )

    try:
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            raw = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        parsed = json.loads(raw)
        return [(s, p, o) for s, p, o in parsed if len([s, p, o]) == 3]
    except (json.JSONDecodeError, TypeError, ValueError):
        logger.warning("Failed to extract relations from: %s", text[:200])
        return []


def _escape_ngql(value: str) -> str:
    """Escape a string for nGQL queries."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")


class NebulaGraphMemory:
    """Knowledge graph memory backed by NebulaGraph.

    Satisfies the ``MemoryProvider`` protocol so it can be passed to any
    BaseAgent's ``memory`` parameter or to ``RAGEngine(graph=...)``.

    Requires ``nebula3-python`` (install via ``uv add sagewai[memory]``).

    Args:
        host: NebulaGraph server host.
        port: NebulaGraph server port.
        space: Graph space name.
        user: Authentication user.
        password: Authentication password.
        project_id: Explicit project scope. When ``None``, auto-resolves
            from the active ``ProjectContext`` contextvar, falling back
            to ``"default"``.
        entity_extractor: Optional :class:`EntityExtractor` for NER.
            When provided (e.g. :class:`GLiNEREntityExtractor`), entity
            extraction uses the local model instead of an LLM prompt.
        relation_extractor: Optional :class:`RelationExtractor`.  When
            provided (e.g. :class:`HeuristicRelationExtractor`), relation
            extraction uses this backend instead of the LLM fallback.
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 9669,
        space: str = "agent_memory",
        user: str = "root",
        password: str = "nebula",
        project_id: str | None = None,
        entity_extractor: EntityExtractor | None = None,
        relation_extractor: RelationExtractor | None = None,
    ) -> None:
        if ConnectionPool is None:
            raise ImportError(
                "nebula3-python is required for NebulaGraphMemory. "
                "Install with: uv add sagewai[memory]"
            )
        self._pool = ConnectionPool()
        if NebulaConfig is not None:
            config = NebulaConfig()
            config.max_connection_pool_size = 10
            self._pool.init([(host, port)], config)
        else:
            self._pool.init([(host, port)])
        self.space = space
        self._user = user
        self._password = password
        self._project_id = project_id
        self._entity_extractor = entity_extractor
        self._relation_extractor = relation_extractor
        self._initialized = False

    def _resolve_pid(self) -> str:
        """Resolve the effective project_id for this operation."""
        return resolve_project_id(self._project_id)

    def _get_session(self) -> Any:
        """Get an authenticated session."""
        session = self._pool.get_session(self._user, self._password)
        if not self._initialized:
            self._init_space(session)
            self._initialized = True
        session.execute(f"USE {self.space}")
        return session

    def _init_space(self, session: Any) -> None:
        """Create the graph space and schema if needed.

        Schema includes temporal fields (valid_from, superseded_at) for
        fact evolution tracking.
        """
        session.execute(
            f"CREATE SPACE IF NOT EXISTS {self.space}"
            f"(vid_type=FIXED_STRING(256), partition_num=1, replica_factor=1)"
        )
        session.execute(f"USE {self.space}")
        session.execute(
            "CREATE TAG IF NOT EXISTS entity("
            "name string, content string, metadata string, project_id string, "
            "valid_from int DEFAULT 0, superseded_at int DEFAULT 0)"
        )
        session.execute(
            "CREATE EDGE IF NOT EXISTS relation("
            "name string, properties string, project_id string, "
            "valid_from int DEFAULT 0, superseded_at int DEFAULT 0)"
        )

    def _sync_add_relation(
        self,
        source: str,
        relation: str,
        target: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Synchronous implementation of add_relation."""
        import json
        import time

        session = self._get_session()
        src = _escape_ngql(source)
        tgt = _escape_ngql(target)
        rel = _escape_ngql(relation)
        pid = _escape_ngql(self._resolve_pid())
        now = int(time.time())

        props = json.dumps(properties or {})

        # Upsert vertices with project_id and valid_from timestamp
        session.execute(
            f"INSERT VERTEX IF NOT EXISTS entity("
            f"name, content, metadata, project_id, valid_from, superseded_at) "
            f'VALUES "{src}":("{src}", "", "", "{pid}", {now}, 0)'
        )
        session.execute(
            f"INSERT VERTEX IF NOT EXISTS entity("
            f"name, content, metadata, project_id, valid_from, superseded_at) "
            f'VALUES "{tgt}":("{tgt}", "", "", "{pid}", {now}, 0)'
        )
        # Insert edge with valid_from timestamp
        session.execute(
            f"INSERT EDGE relation(name, properties, project_id, valid_from, superseded_at) "
            f'VALUES "{src}"->"{tgt}":("{rel}", "{_escape_ngql(props)}", "{pid}", {now}, 0)'
        )

    async def add_relation(
        self, source: str, relation: str, target: str, properties: dict[str, Any] | None = None
    ) -> None:
        """Add a relationship between two entities with temporal tracking."""
        await asyncio.to_thread(self._sync_add_relation, source, relation, target, properties)

    async def _extract_relations(self, text: str) -> list[tuple[str, str, str]]:
        """Extract relation triples, preferring the configured extractor.

        When a ``relation_extractor`` was provided at construction time,
        it is used.  Otherwise the legacy LLM prompt is called.
        """
        if self._relation_extractor is not None:
            triples = await self._relation_extractor.extract(text)
            return [(t.subject, t.predicate, t.object) for t in triples]
        return await _extract_relations_llm(text)

    async def store(self, content: str, metadata: dict[str, Any] | None = None) -> None:
        """Extract entities and relations from content and store them."""
        relations = await self._extract_relations(content)
        for source, relation, target in relations:
            await self.add_relation(source, relation, target)

    def _sync_retrieve_entity(self, entity_name: str, pid: str, top_k: int) -> list[str]:
        """Retrieve relations for a single entity name (sync)."""
        session = self._get_session()
        escaped = _escape_ngql(entity_name)

        result = session.execute(
            f'LOOKUP ON entity WHERE entity.name == "{escaped}" '
            f'AND entity.project_id == "{pid}" '
            f"AND entity.superseded_at == 0 "
            f"| GO FROM $-.VertexID OVER relation "
            f'WHERE relation.project_id == "{pid}" '
            f"AND relation.superseded_at == 0 "
            f"YIELD $^.entity.name AS src, relation.name AS rel, $$.entity.name AS dst "
            f"LIMIT {top_k}"
        )

        if not result or not result.is_succeeded() or result.row_size() == 0:
            return []

        lines: list[str] = []
        for i in range(result.row_size()):
            row = result.row_values(i)
            lines.append(
                f"{row[0].as_string()} {row[1].as_string()} {row[2].as_string()}"
            )
        return lines

    def _extract_entity_names_simple(self, query: str) -> list[str]:
        """Extract candidate entity names from a query using simple heuristics.

        Splits on whitespace, filters stopwords and short tokens.
        Falls back to the full query if nothing else matches.
        """
        stopwords = {
            "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "used", "to", "of", "in", "for", "on", "with", "at", "by", "from",
            "as", "into", "through", "during", "before", "after", "above", "below",
            "between", "out", "off", "over", "under", "again", "further", "then",
            "once", "here", "there", "when", "where", "why", "how", "all", "each",
            "every", "both", "few", "more", "most", "other", "some", "such", "no",
            "nor", "not", "only", "own", "same", "so", "than", "too", "very",
            "just", "because", "but", "and", "or", "if", "while", "about", "what",
            "which", "who", "whom", "this", "that", "these", "those", "am", "it",
            "its", "my", "your", "his", "her", "our", "their", "me", "him", "us",
            "them", "i", "you", "he", "she", "we", "they", "know", "tell",
        }
        tokens = query.split()
        candidates = [t for t in tokens if t.lower() not in stopwords and len(t) > 1]
        return candidates if candidates else [query]

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Retrieve context by finding entities matching the query.

        Uses the configured ``entity_extractor`` to extract entity names
        from the query. Falls back to simple token-based extraction if
        no extractor is configured. Queries for each extracted entity
        and merges results.

        Results are filtered by project scope and exclude superseded entities.
        """
        # Extract entity names from the query
        entity_names: list[str] = []
        if self._entity_extractor is not None:
            try:
                extraction = await self._entity_extractor.extract(query)
                entity_names = [e.text for e in extraction.entities if e.text]
            except Exception:
                logger.warning("Entity extraction failed, falling back to heuristic")

        if not entity_names:
            entity_names = self._extract_entity_names_simple(query)

        # Query for each entity and merge results
        pid = _escape_ngql(self._resolve_pid())
        seen: set[str] = set()
        context_lines: list[str] = []

        for name in entity_names:
            lines = await asyncio.to_thread(
                self._sync_retrieve_entity, name, pid, top_k
            )
            for line in lines:
                if line not in seen:
                    seen.add(line)
                    context_lines.append(line)
                    if len(context_lines) >= top_k:
                        return context_lines

        return context_lines[:top_k]

    def _sync_get_neighbors(self, entity: str, depth: int) -> list[dict[str, str]]:
        """Synchronous implementation of get_neighbors."""
        session = self._get_session()
        escaped = _escape_ngql(entity)
        pid = _escape_ngql(self._resolve_pid())

        result = session.execute(
            f'GO {depth} STEPS FROM "{escaped}" OVER relation '
            f'WHERE relation.project_id == "{pid}" '
            f"YIELD $$.entity.name AS neighbor, relation.name AS via"
        )

        if not result.is_succeeded() or result.row_size() == 0:
            return []

        neighbors: list[dict[str, str]] = []
        for i in range(result.row_size()):
            row = result.row_values(i)
            neighbors.append({
                "entity": row[0].as_string(),
                "relation": row[1].as_string(),
            })
        return neighbors

    async def get_neighbors(self, entity: str, depth: int = 1) -> list[dict[str, str]]:
        """Get all neighbors of an entity up to a given depth."""
        return await asyncio.to_thread(self._sync_get_neighbors, entity, depth)

    def _sync_list_entities(
        self,
        search: str,
        limit: int,
        offset: int,
    ) -> list[dict[str, Any]]:
        """Synchronous implementation of list_entities."""
        import json as _json

        session = self._get_session()
        pid = _escape_ngql(self._resolve_pid())

        query = (
            f'LOOKUP ON entity WHERE entity.project_id == "{pid}" '
            f"AND entity.superseded_at == 0 "
            f"YIELD id(vertex) AS vid, entity.name AS name, entity.metadata AS meta "
            f"| ORDER BY $-.name"
        )

        result = session.execute(query)
        if not result or not result.is_succeeded():
            return []

        q = search.lower() if search else None
        entities: list[dict[str, Any]] = []
        for i in range(result.row_size()):
            row = result.row_values(i)
            name = row[1].as_string() if row[1] else ""
            if q and q not in name.lower():
                continue
            meta_str = row[2].as_string() if row[2] else ""
            try:
                meta = _json.loads(meta_str) if meta_str else {}
            except (_json.JSONDecodeError, TypeError):
                meta = {}
            entities.append({"name": name, "metadata": meta})

        return entities[offset : offset + limit]

    async def list_entities(
        self,
        search: str = "",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List entities with optional name search, pagination.

        nGQL LOOKUP doesn't support CONTAINS on non-indexed string
        fields, so we fetch all active entities and filter/paginate
        in Python.  This is fine for typical graph sizes (<100k).
        """
        return await asyncio.to_thread(self._sync_list_entities, search, limit, offset)

    def _sync_get_relations(self, entity: str) -> list[tuple[str, str, str]]:
        """Synchronous implementation of get_relations."""
        session = self._get_session()
        escaped = _escape_ngql(entity)
        pid = _escape_ngql(self._resolve_pid())

        # Outgoing edges
        result_out = session.execute(
            f'GO FROM "{escaped}" OVER relation '
            f'WHERE relation.project_id == "{pid}" '
            f"AND relation.superseded_at == 0 "
            f"YIELD $^.entity.name AS src, relation.name AS rel, $$.entity.name AS dst"
        )

        # Incoming edges
        result_in = session.execute(
            f'GO FROM "{escaped}" OVER relation REVERSELY '
            f'WHERE relation.project_id == "{pid}" '
            f"AND relation.superseded_at == 0 "
            f"YIELD $$.entity.name AS src, relation.name AS rel, $^.entity.name AS dst"
        )

        relations: list[tuple[str, str, str]] = []
        for result in [result_out, result_in]:
            if result and result.is_succeeded():
                for i in range(result.row_size()):
                    row = result.row_values(i)
                    relations.append((
                        row[0].as_string(),
                        row[1].as_string(),
                        row[2].as_string(),
                    ))
        return relations

    async def get_relations(self, entity: str) -> list[tuple[str, str, str]]:
        """Get all relations involving an entity.

        Returns:
            List of (source, relation, target) tuples.
        """
        return await asyncio.to_thread(self._sync_get_relations, entity)

    def _sync_supersede(self, entity_name: str) -> bool:
        """Synchronous implementation of supersede."""
        import time

        session = self._get_session()
        escaped = _escape_ngql(entity_name)
        pid = _escape_ngql(self._resolve_pid())
        now = int(time.time())

        # Mark entity as superseded
        result = session.execute(
            f'UPDATE VERTEX ON entity "{escaped}" '
            f"SET superseded_at = {now} "
            f"WHEN project_id == \"{pid}\" AND superseded_at == 0"
        )
        return result.is_succeeded() if result else False

    async def supersede(self, entity_name: str) -> bool:
        """Mark an entity and its relations as superseded (no longer current).

        Superseded entities are excluded from default retrieve() calls but
        remain available via retrieve_at() for point-in-time queries.
        """
        return await asyncio.to_thread(self._sync_supersede, entity_name)

    def _sync_supersede_by_document(self, document_id: str) -> int:
        """Synchronous implementation of supersede_by_document."""
        import json
        import time

        session = self._get_session()
        pid = _escape_ngql(self._resolve_pid())
        doc_id = _escape_ngql(document_id)
        now = int(time.time())

        # Find entities whose metadata contains this document_id
        # Note: NebulaGraph string matching is limited; this checks metadata field
        result = session.execute(
            f'LOOKUP ON entity WHERE entity.project_id == "{pid}" '
            f'AND entity.superseded_at == 0 '
            f"YIELD id(vertex) AS vid, entity.metadata AS meta"
        )

        if not result or not result.is_succeeded():
            return 0

        count = 0
        for i in range(result.row_size()):
            row = result.row_values(i)
            meta_str = row[1].as_string() if row[1] else ""

            # Parse metadata JSON and check document_id field specifically
            try:
                meta = json.loads(meta_str) if meta_str else {}
            except (json.JSONDecodeError, TypeError):
                meta = {}

            if meta.get("document_id") == document_id:
                vid = row[0].as_string()
                session.execute(
                    f'UPDATE VERTEX ON entity "{_escape_ngql(vid)}" '
                    f"SET superseded_at = {now}"
                )
                count += 1

        logger.info("Superseded %d entities from document %s", count, document_id)
        return count

    async def supersede_by_document(self, document_id: str) -> int:
        """Supersede all entities that originated from a specific document.

        Call this before re-ingesting a document to mark old facts as superseded.
        """
        return await asyncio.to_thread(self._sync_supersede_by_document, document_id)

    def _sync_retrieve_at(self, query: str, at_timestamp: int, top_k: int) -> list[str]:
        """Synchronous implementation of retrieve_at."""
        session = self._get_session()
        escaped = _escape_ngql(query)
        pid = _escape_ngql(self._resolve_pid())

        result = session.execute(
            f'LOOKUP ON entity WHERE entity.name == "{escaped}" '
            f'AND entity.project_id == "{pid}" '
            f"AND entity.valid_from <= {at_timestamp} "
            f"AND (entity.superseded_at == 0 OR entity.superseded_at > {at_timestamp}) "
            f"| GO FROM $-.VertexID OVER relation "
            f'WHERE relation.project_id == "{pid}" '
            f"AND relation.valid_from <= {at_timestamp} "
            f"AND (relation.superseded_at == 0 OR relation.superseded_at > {at_timestamp}) "
            f"YIELD $^.entity.name AS src, relation.name AS rel, $$.entity.name AS dst "
            f"LIMIT {top_k}"
        )

        if not result or not result.is_succeeded() or result.row_size() == 0:
            return []

        return [
            f"{result.row_values(i)[0].as_string()} "
            f"{result.row_values(i)[1].as_string()} "
            f"{result.row_values(i)[2].as_string()}"
            for i in range(result.row_size())
        ][:top_k]

    async def retrieve_at(
        self, query: str, at_timestamp: int, top_k: int = 5
    ) -> list[str]:
        """Retrieve facts that were valid at a specific point in time.

        Returns entities where valid_from <= at_timestamp AND
        (superseded_at == 0 OR superseded_at > at_timestamp).
        """
        return await asyncio.to_thread(self._sync_retrieve_at, query, at_timestamp, top_k)

    def _sync_delete(self, content: str) -> bool:
        """Synchronous implementation of delete."""
        session = self._get_session()
        escaped = _escape_ngql(content)
        result = session.execute(f'DELETE VERTEX "{escaped}" WITH EDGE')
        return result.is_succeeded()

    async def delete(self, content: str) -> bool:
        """Delete an entity vertex and its edges."""
        return await asyncio.to_thread(self._sync_delete, content)

    def _sync_clear(self) -> None:
        """Synchronous implementation of clear."""
        session = self._pool.get_session(self._user, self._password)
        session.execute(f"DROP SPACE IF EXISTS {self.space}")
        self._initialized = False

    async def clear(self) -> None:
        """Drop and recreate the graph space."""
        await asyncio.to_thread(self._sync_clear)
