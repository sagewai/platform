# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke tests for PostgresTokenStore — SQLAlchemy Core implementation.

These tests exercise the constructor API and basic protocol conformance.
Full CRUD + parity coverage lives in tests/db/test_gateway_stores_parity.py.
"""

from __future__ import annotations

from sagewai.gateway.postgres_store import PostgresTokenStore
from sagewai.gateway.store import TokenStore


def test_postgres_token_store_implements_protocol():
    """PostgresTokenStore satisfies the TokenStore protocol."""
    assert issubclass(PostgresTokenStore, TokenStore)


def test_postgres_token_store_engine_kwarg_takes_priority(tmp_path):
    """engine= kwarg is used; database_url= is ignored when engine= is set."""
    from sagewai.db.engine import create_engine as _ce

    engine = _ce(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    # database_url is ignored when engine= is supplied
    store = PostgresTokenStore(
        database_url="postgresql://localhost/ignored",
        engine=engine,
    )
    assert store._engine is engine


def test_postgres_token_store_database_url_ctor(tmp_path):
    """Positional database_url= creates an engine."""
    store = PostgresTokenStore(
        database_url=f"sqlite+aiosqlite:///{tmp_path / 'url.db'}"
    )
    assert store._engine is not None
    assert store._engine.dialect.name == "sqlite"


def test_postgres_token_store_pool_ignored(tmp_path):
    """pool= positional arg is accepted without error (back-compat); engine= wins."""
    from sagewai.db.engine import create_engine as _ce

    engine = _ce(f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}")
    sentinel_pool = object()
    store = PostgresTokenStore(pool=sentinel_pool, engine=engine)
    # The pool argument must not be stored as self._pool (old asyncpg attr gone)
    assert not hasattr(store, "_pool") or store._pool is not sentinel_pool  # type: ignore[attr-defined]
    assert store._engine is engine
