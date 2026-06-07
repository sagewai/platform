# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""sqlite-vec-backed durable vector memory for semantic retrieval.

Uses sqlite-vec 0.1.9 vec0 virtual tables with auxiliary columns for
project-scoped KNN search.  All heavy I/O is dispatched via
``asyncio.to_thread`` so the event loop is never blocked.

Project scoping
---------------
Empirical testing confirmed that sqlite-vec 0.1.9 supports filtering on
auxiliary (non-vector) columns in a KNN query via the form::

    SELECT id, content
    FROM <table>
    WHERE embedding MATCH ?
    AND project_id = ?
    ORDER BY distance
    LIMIT k

This is approach (a): a single vec0 table with ``project_id TEXT`` and
``content TEXT`` declared as auxiliary columns alongside the primary key and
the vector column.  Partition-key syntax (``+project_id``) was tested and
rejected by 0.1.9 with "An illegal WHERE constraint was provided on a vec0
auxiliary column in a KNN query."  The join + over-fetch approach was also
rejected because vec0 requires a ``LIMIT`` on MATCH queries when used in a
JOIN context.  Approach (a) is therefore the only working option.
"""

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import struct
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sagewai import home
from sagewai.core.context import resolve_project_id

if TYPE_CHECKING:
    from sagewai.intelligence.embeddings.protocol import Embedder

logger = logging.getLogger(__name__)

# Regex for table name safety — allow only word characters
_SAFE_NAME = re.compile(r"^\w+$")

# ---------------------------------------------------------------------------
# Extension availability probe (cached, never-raising)
# ---------------------------------------------------------------------------

_EXT_AVAILABLE: bool | None = None


def sqlite_vec_available() -> bool:
    """Return ``True`` if the sqlite-vec extension can be loaded on this platform.

    The result is cached after the first probe.  Never raises.
    """
    global _EXT_AVAILABLE
    if _EXT_AVAILABLE is None:
        try:
            import sqlite3 as _sqlite3

            import sqlite_vec as _sv  # noqa: PLC0415

            _c = _sqlite3.connect(":memory:")
            _c.enable_load_extension(True)
            _sv.load(_c)
            _c.close()
            _EXT_AVAILABLE = True
        except Exception:  # pragma: no cover
            _EXT_AVAILABLE = False
    return _EXT_AVAILABLE


def _require_safe(name: str) -> str:
    """Raise ValueError if *name* is not a safe SQL identifier."""
    if not _SAFE_NAME.match(name):
        raise ValueError(f"Unsafe table name: {name!r}")
    return name


class SqliteVecMemory:
    """Durable vector memory backed by sqlite-vec (``vec0`` virtual table).

    Satisfies the ``MemoryProvider`` protocol so it can be passed to any
    ``BaseAgent``'s ``memory`` parameter or to ``RAGEngine(vector=...)``.

    ``sqlite-vec`` is a **base dependency** of ``sagewai`` (always installed);
    no optional extra is required.

    Project scoping is implemented via an auxiliary ``project_id`` column
    inside the single vec0 table.  The KNN MATCH query is combined with
    ``AND project_id = ?`` which sqlite-vec 0.1.9 supports for auxiliary
    columns.

    Args:
        db_path: Path to the SQLite database file.  Defaults to
            ``$SAGEWAI_HOME/db/sagewai.db``.
        embedder: Embedding backend.  Defaults to ``HashEmbedder(dim=dim)``.
        dim: Vector dimension.  Ignored when *embedder* is provided (its
            ``.dimension`` property is used instead).  Defaults to 384.
        project_id: Explicit project scope.  When ``None``, auto-resolves
            from the active ``ProjectContext`` contextvar, falling back to
            ``"default"``.
        table: Name of the vec0 virtual table.  Defaults to
            ``"vec_learnings"``.
    """

    def __init__(
        self,
        *,
        db_path: str | None = None,
        embedder: Embedder | None = None,
        dim: int | None = None,
        project_id: str | None = None,
        table: str = "vec_learnings",
    ) -> None:
        if embedder is None:
            from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder

            embedder = HashEmbedder(dimension=dim or 384)

        self._embedder: Embedder = embedder
        self.dim: int = embedder.dimension
        self._project_id = project_id
        self._table = _require_safe(table)

        if db_path is None:
            db_path = str(home.db_dir() / "sagewai.db")
        self._db_path = db_path

        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Return (and lazily open + initialise) the sqlite3 connection."""
        if self._conn is not None:
            return self._conn

        try:
            import sqlite_vec  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "sqlite-vec is a base dependency of sagewai and should always be "
                "installed. If it is missing, reinstall sagewai: pip install sagewai"
            ) from exc

        # Ensure parent directory exists
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

        conn = sqlite3.connect(self._db_path, check_same_thread=False)

        # WAL mode for concurrent reads alongside SQLAlchemy stores on the same file
        conn.execute("PRAGMA journal_mode=WAL")
        # Avoid "database is locked" errors under contention
        conn.execute("PRAGMA busy_timeout=5000")

        # Load sqlite-vec extension
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)

        # Create the vec0 virtual table if it doesn't exist.
        # Auxiliary columns (project_id, content) live alongside the vector
        # so they can be filtered in a MATCH query.
        conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS {self._table}
            USING vec0(
                id TEXT PRIMARY KEY,
                embedding FLOAT[{self.dim}],
                project_id TEXT,
                content TEXT
            )
        """)
        conn.commit()

        self._conn = conn
        return conn

    async def close(self) -> None:
        """Close the underlying sqlite3 connection (async-safe)."""
        await asyncio.to_thread(self._sync_close)

    def _sync_close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # pragma: no cover
                pass
            finally:
                self._conn = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _resolve_pid(self) -> str:
        return resolve_project_id(self._project_id)

    def _pack(self, vec: list[float]) -> bytes:
        return struct.pack(f"<{self.dim}f", *vec)

    # ------------------------------------------------------------------
    # Synchronous DB operations (called via asyncio.to_thread)
    # ------------------------------------------------------------------

    def _sync_store(
        self,
        doc_id: str,
        vec: list[float],
        content: str,
        pid: str,
    ) -> None:
        conn = self._connect()
        conn.execute(
            f"INSERT INTO {self._table}(id, embedding, project_id, content) VALUES (?, ?, ?, ?)",
            (doc_id, self._pack(vec), pid, content),
        )
        conn.commit()

    def _sync_retrieve(
        self,
        vec: list[float],
        pid: str,
        top_k: int,
    ) -> list[str]:
        conn = self._connect()
        rows = conn.execute(
            f"""
            SELECT content
            FROM {self._table}
            WHERE embedding MATCH ?
              AND project_id = ?
            ORDER BY distance
            LIMIT ?
            """,
            (self._pack(vec), pid, top_k),
        ).fetchall()
        return [row[0] for row in rows]

    def _sync_delete(self, doc_id: str, pid: str) -> bool:
        conn = self._connect()
        cur = conn.execute(
            f"DELETE FROM {self._table} WHERE id = ? AND project_id = ?",
            (doc_id, pid),
        )
        conn.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------
    # MemoryProvider interface
    # ------------------------------------------------------------------

    async def store(
        self, content: str, metadata: dict[str, Any] | None = None
    ) -> str:
        """Embed and persist *content*.

        Returns:
            The generated document UUID.
        """
        doc_id = str(uuid.uuid4())
        vec = await self._embedder.embed_query(content)
        pid = self._resolve_pid()
        await asyncio.to_thread(self._sync_store, doc_id, vec, content, pid)
        logger.debug("sqlite_vec: stored %s (project=%s)", doc_id, pid)
        return doc_id

    async def retrieve(self, query: str, top_k: int = 5) -> list[str]:
        """Return up to *top_k* content strings most similar to *query*.

        Results are scoped to the current project.
        """
        vec = await self._embedder.embed_query(query)
        pid = self._resolve_pid()
        return await asyncio.to_thread(self._sync_retrieve, vec, pid, top_k)

    async def delete(self, doc_id: str) -> bool:
        """Delete a document by ID.

        Returns:
            ``True`` if a document was deleted, ``False`` if no matching
            document was found in the current project scope.
        """
        pid = self._resolve_pid()
        return await asyncio.to_thread(self._sync_delete, doc_id, pid)
