# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Dialect-aware INSERT ... ON CONFLICT DO UPDATE."""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy import Table
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.sql import Insert


def upsert(
    table: Table,
    values: dict[str, Any],
    *,
    index_elements: Sequence[str],
    set_: dict[str, Any] | None = None,
    dialect: str | None = None,
) -> Insert:
    """Build a portable upsert (INSERT ... ON CONFLICT DO UPDATE).

    Pass ``dialect`` = 'postgresql' or 'sqlite' (typically
    ``engine.dialect.name``). When ``set_`` is omitted, every non-key
    column is updated from the proposed (excluded) row.
    """
    make = pg_insert if dialect == "postgresql" else sqlite_insert
    stmt = make(table).values(**values)
    update_cols = set_ if set_ is not None else {
        c.name: stmt.excluded[c.name]
        for c in table.columns
        if c.name not in index_elements
    }
    return stmt.on_conflict_do_update(index_elements=list(index_elements), set_=update_cols)
