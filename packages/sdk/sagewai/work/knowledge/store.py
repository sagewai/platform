# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Append-only persistence and scoped full-text search for shared knowledge."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from sqlalchemy import func, insert, literal_column, or_, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from sagewai._project_scope import project_scope_key
from sagewai.db import factory
from sagewai.db.models import Base, KnowledgeItemModel, KnowledgeSourceRefModel
from sagewai.work.knowledge.models import KnowledgeItem, KnowledgeQuery


def _as_utc(value: datetime) -> datetime:
    """Restore the UTC marker SQLite drops from timezone-aware timestamps."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _sqlite_plaintext_query(value: str) -> str:
    """Encode operator input as literal FTS5 terms joined by implicit AND."""
    terms = (term.replace('"', '""') for term in value.split())
    return " ".join(f'"{term}"' for term in terms)


_SEARCH_TERM_RE = re.compile(r"[a-z0-9_]+")
_FALLBACK_IGNORED_TERMS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "control",
        "details",
        "during",
        "failure",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "preconditions",
        "the",
        "to",
        "was",
        "were",
        "with",
        "work",
    }
)


def _search_terms(value: str) -> tuple[str, ...]:
    """Tokenize fallback queries and candidates with one deterministic rule."""
    return tuple(dict.fromkeys(_SEARCH_TERM_RE.findall(value.casefold())))


def _sqlite_any_term_query(terms: tuple[str, ...]) -> str:
    """Encode normalized literal FTS5 terms joined by OR."""
    return " OR ".join(f'"{term}"' for term in terms)


def _has_meaningful_term_overlap(query: str, statement: str) -> bool:
    """Reject coincidences limited to control boilerplate or one generic term."""
    query_terms = set(_search_terms(query)) - _FALLBACK_IGNORED_TERMS
    statement_terms = set(_search_terms(statement)) - _FALLBACK_IGNORED_TERMS
    overlap = query_terms & statement_terms
    if len(query_terms) == 1:
        return len(overlap) == 1
    return len(overlap) >= 2


async def insert_knowledge_item(
    conn: AsyncConnection,
    item: KnowledgeItem,
    *,
    dialect_name: str,
) -> None:
    """Insert one item and its SQLite search row on an existing transaction."""
    values = item.model_dump(mode="python")
    values["project_scope_key"] = project_scope_key(item.project_id)
    values["kind"] = item.kind.value
    values["source_refs"] = list(item.source_refs)
    values["artifact_refs"] = list(item.artifact_refs)

    await conn.execute(insert(KnowledgeItemModel.__table__).values(**values))
    source_refs = tuple(dict.fromkeys(item.source_refs))
    if source_refs:
        await conn.execute(
            insert(KnowledgeSourceRefModel),
            [
                {
                    "knowledge_item_id": item.id,
                    "project_scope_key": project_scope_key(item.project_id),
                    "project_id": item.project_id,
                    "source_ref": source_ref,
                }
                for source_ref in source_refs
            ],
        )
    if dialect_name == "sqlite":
        await conn.execute(
            text(
                "INSERT INTO knowledge_items_fts (project_scope_key, item_id, statement) "
                "VALUES (:project_scope_key, :item_id, :statement)"
            ),
            {
                "project_scope_key": project_scope_key(item.project_id),
                "item_id": item.id,
                "statement": item.statement,
            },
        )


class KnowledgeStore:
    """Publish and retrieve immutable project-scoped KnowledgeItems."""

    def __init__(self, *, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or factory.get_engine()
        self._items = KnowledgeItemModel.__table__
        self._source_refs = KnowledgeSourceRefModel.__table__

    async def init(self) -> None:
        """Bootstrap the SQLite schema."""
        if self._engine.dialect.name != "sqlite":
            return
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(
                text(
                    "INSERT OR IGNORE INTO knowledge_source_refs "
                    "(project_scope_key, knowledge_item_id, project_id, source_ref) "
                    "SELECT DISTINCT knowledge.project_scope_key, knowledge.id, knowledge.project_id, source.value "
                    "FROM knowledge_items AS knowledge "
                    "CROSS JOIN json_each(knowledge.source_refs) AS source "
                    "WHERE source.type = :source_type"
                ),
                {"source_type": "text"},
            )

    async def publish(self, item: KnowledgeItem) -> None:
        """Append one immutable knowledge item."""
        async with self._engine.begin() as conn:
            await insert_knowledge_item(
                conn,
                item,
                dialect_name=self._engine.dialect.name,
            )

    async def get(self, item_id: str, *, project_id: str | None) -> KnowledgeItem | None:
        """Read one item without crossing its project boundary."""
        query = select(self._items).where(
            self._items.c.id == item_id,
            self._items.c.project_scope_key == project_scope_key(project_id),
        )
        async with self._engine.connect() as conn:
            row = (await conn.execute(query)).first()
        if row is None:
            return None
        return self._from_row(row._mapping)

    async def find_by_source_ref(
        self,
        source_ref: str,
        *,
        project_id: str | None,
        work_id: str | None = None,
    ) -> list[KnowledgeItem]:
        """Resolve an exact source ref through the project-scoped navigation index."""
        filters = [
            self._source_refs.c.project_scope_key == project_scope_key(project_id),
            self._source_refs.c.source_ref == source_ref,
            self._items.c.project_scope_key == project_scope_key(project_id),
        ]
        if work_id is not None:
            filters.append(self._items.c.work_id == work_id)
        statement = (
            select(self._items)
            .join(
                self._source_refs,
                (self._source_refs.c.project_scope_key == self._items.c.project_scope_key)
                & (self._source_refs.c.knowledge_item_id == self._items.c.id),
            )
            .where(*filters)
            .order_by(self._items.c.created_at, self._items.c.id)
        )
        async with self._engine.connect() as conn:
            rows = (await conn.execute(statement)).all()
        return [self._from_row(row._mapping) for row in rows]

    async def search(self, query: KnowledgeQuery) -> list[KnowledgeItem]:
        """Run scoped literal-term search using the active database's tokenization."""
        filters = [self._items.c.project_scope_key == project_scope_key(query.project_id)]
        if query.work_id is not None:
            filters.append(self._items.c.work_id == query.work_id)

        if self._engine.dialect.name == "sqlite":
            matched_ids = text(
                "SELECT item_id FROM knowledge_items_fts "
                "WHERE project_scope_key = :project_scope_key AND knowledge_items_fts MATCH :search_text"
            )
            filters.append(self._items.c.id.in_(matched_ids))
        else:
            config = literal_column("'simple'")
            filters.append(
                func.to_tsvector(config, self._items.c.statement).op("@@")(
                    func.plainto_tsquery(config, query.text)
                )
            )

        statement = (
            select(self._items).where(*filters).order_by(self._items.c.created_at, self._items.c.id)
        )
        if self._engine.dialect.name == "sqlite":
            statement = statement.params(
                project_scope_key=project_scope_key(query.project_id),
                search_text=_sqlite_plaintext_query(query.text),
            )

        async with self._engine.connect() as conn:
            rows = (await conn.execute(statement)).all()
        return [self._from_row(row._mapping) for row in rows]

    async def search_high_importance_project_findings_any_term(
        self,
        query: KnowledgeQuery,
        *,
        limit: int,
    ) -> list[KnowledgeItem]:
        """Return a bounded, meaningfully related project FINDING fallback."""
        terms = _search_terms(query.text)
        if not terms or limit < 1:
            return []

        filters = [
            self._items.c.project_scope_key == project_scope_key(query.project_id),
            self._items.c.work_id.is_(None),
            self._items.c.kind == "finding",
            self._items.c.importance_score > 50,
        ]
        if self._engine.dialect.name == "sqlite":
            matched_ids = text(
                "SELECT item_id FROM knowledge_items_fts "
                "WHERE project_scope_key = :project_scope_key AND knowledge_items_fts MATCH :search_text"
            )
            filters.append(self._items.c.id.in_(matched_ids))
        else:
            config = literal_column("'simple'")
            vector = func.to_tsvector(config, self._items.c.statement)
            filters.append(
                or_(*(vector.op("@@")(func.plainto_tsquery(config, term)) for term in terms))
            )

        statement = (
            select(self._items)
            .where(*filters)
            .order_by(
                self._items.c.importance_score.desc(),
                self._items.c.created_at.desc(),
                self._items.c.id,
            )
        )
        if self._engine.dialect.name == "sqlite":
            statement = statement.params(
                project_scope_key=project_scope_key(query.project_id),
                search_text=_sqlite_any_term_query(terms),
            )

        async with self._engine.connect() as conn:
            rows = (await conn.execute(statement)).all()
        matches = (self._from_row(row._mapping) for row in rows)
        return [
            item for item in matches if _has_meaningful_term_overlap(query.text, item.statement)
        ][:limit]

    @staticmethod
    def _from_row(values) -> KnowledgeItem:
        data = dict(values)
        data.pop("project_scope_key")
        data["created_at"] = _as_utc(data["created_at"])
        return KnowledgeItem.model_validate(data)
