# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Schema integrity guards for the tenancy tables (W0).

These are adversarial "consistency guard" tests (category #10 of the W0
adversarial cross-tenant charter): they assert the DB itself rejects rows that
would corrupt the tenancy model — a mismatched role/scope, a duplicate
org-shared identity, or a project that belongs to a different org. Run on
SQLite always; on Postgres when SAGEWAI_TEST_DATABASE_URL is set.

Plus a round-trip unit test for Alembic migration 009 (the Postgres schema).
"""

import importlib

import pytest
import pytest_asyncio
import sqlalchemy as sa
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from sagewai.admin.identity_store import IdentityStore


@pytest_asyncio.fixture
async def seeded(dialect_engine):
    store = IdentityStore(engine=dialect_engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    user = await store.create_user(oid, "a@acme.io", password="pw0000", role="org:admin")
    proj = await store.create_project(oid, "p", "P")
    return dialect_engine, oid, user["id"], proj["id"]


async def _insert_membership(engine, **vals):
    cols = ", ".join(vals)
    params = ", ".join(f":{k}" for k in vals)
    async with engine.begin() as conn:
        await conn.execute(text(f"INSERT INTO membership ({cols}) VALUES ({params})"), vals)


async def test_check_rejects_role_scope_mismatch(seeded):
    engine, oid, uid, pid = seeded
    # project role on an org-level (NULL project) membership.
    with pytest.raises(IntegrityError):
        await _insert_membership(
            engine, id="m1", user_id=uid, org_id=oid, project_id=None, role="project:admin"
        )
    # org role on a project-scoped membership.
    with pytest.raises(IntegrityError):
        await _insert_membership(
            engine, id="m2", user_id=uid, org_id=oid, project_id=pid, role="org:admin"
        )


async def test_partial_index_rejects_duplicate_org_membership(seeded):
    engine, oid, uid, pid = seeded
    # uid already has an org-level membership (org:admin) from create_user.
    with pytest.raises(IntegrityError):
        await _insert_membership(
            engine, id="dup", user_id=uid, org_id=oid, project_id=None, role="org:member"
        )


async def test_composite_fk_rejects_cross_org_project(seeded):
    engine, oid, uid, _pid = seeded
    # project_id points at a project that does not exist in this org.
    with pytest.raises(IntegrityError):
        await _insert_membership(
            engine, id="x", user_id=uid, org_id=oid, project_id="ghost", role="project:member"
        )


def test_migration_009_roundtrip_sqlite():
    mod = importlib.import_module("sagewai.db.migrations.versions.009_tenancy_identity")
    assert mod.revision == "009_tenancy_identity"
    assert mod.down_revision == "008_directives"

    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    tenancy_tables = {
        "org",
        "user_account",
        "project",
        "membership",
        "invitation",
        "user_session",
    }
    engine = sa.create_engine("sqlite://")  # sync, in-memory
    with engine.begin() as conn:
        ctx = MigrationContext.configure(conn)
        with Operations.context(ctx):
            mod.upgrade()
            assert tenancy_tables <= set(sa.inspect(conn).get_table_names())
            mod.downgrade()
            assert not (tenancy_tables & set(sa.inspect(conn).get_table_names()))
