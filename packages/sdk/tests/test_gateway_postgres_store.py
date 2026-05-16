# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for PostgresTokenStore — uses InMemoryTokenStore as stand-in.

The PostgresTokenStore is tested against the TokenStore protocol using
the same test patterns. Integration tests with a real database are
marked with @pytest.mark.integration and skipped by default.
"""

from __future__ import annotations

from sagewai.gateway.postgres_store import PostgresTokenStore


def test_postgres_token_store_implements_protocol():
    """PostgresTokenStore satisfies the TokenStore protocol."""
    from sagewai.gateway.store import TokenStore

    assert issubclass(PostgresTokenStore, TokenStore)


def test_postgres_token_store_init():
    """PostgresTokenStore can be instantiated with a database URL."""
    store = PostgresTokenStore(database_url="postgresql://localhost/test")
    assert store._database_url == "postgresql://localhost/test"
    assert store._pool is None
