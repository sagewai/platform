# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant data-scope primitive — project_id-only contract (single-org world)."""

import pytest
import sqlalchemy as sa

from sagewai.admin.scoping import (
    UnscopedQueryError,
    apply_scope,
    apply_write_scope,
    require_ctx,
    row_in_scope,
    row_writable,
    scope_filter,
    scope_values,
    write_scope_filter,
)
from sagewai.admin.tenancy import RequestContext, UserRef

_md = sa.MetaData()
_t = sa.Table(
    "t",
    _md,
    sa.Column("id", sa.Text, primary_key=True),
    sa.Column("project_id", sa.Text, nullable=True),
)


def _ctx(project_id):
    return RequestContext(
        actor=UserRef("u", "u"),
        org_id="default",
        project_id=project_id,
        roles=frozenset(),
        scopes=frozenset(),
        request_id="r",
        tenancy_mode="multi",
    )


# --- clause-level assertions (no DB needed) ---


def test_read_scope_project_includes_global():
    s = str(scope_filter(_t, _ctx("P")).compile(compile_kwargs={"literal_binds": True}))
    assert "project_id = 'P'" in s and "project_id IS NULL" in s
    assert "org_id" not in s


def test_read_scope_org_is_global_only():
    s = str(scope_filter(_t, _ctx(None)).compile(compile_kwargs={"literal_binds": True}))
    assert "project_id IS NULL" in s and "project_id =" not in s


def test_write_scope_project_excludes_global():
    s = str(write_scope_filter(_t, _ctx("P")).compile(compile_kwargs={"literal_binds": True}))
    assert "project_id = 'P'" in s and "IS NULL" not in s


def test_row_in_scope_inherits_global():
    assert row_in_scope({"project_id": None}, _ctx("P")) is True
    assert row_in_scope({"project_id": "P"}, _ctx("P")) is True
    assert row_in_scope({"project_id": "Q"}, _ctx("P")) is False


def test_row_writable_excludes_global_for_project():
    assert row_writable({"project_id": None}, _ctx("P")) is False
    assert row_writable({"project_id": "P"}, _ctx("P")) is True


def test_scope_values_has_no_org_id():
    assert scope_values(_ctx("P")) == {"project_id": "P"}


# --- integration tests against a real SQLite table (no org_id column) ---


@pytest.fixture
def table_engine():
    md = sa.MetaData()
    t = sa.Table(
        "res",
        md,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("project_id", sa.Text, nullable=True),
        sa.Column("name", sa.Text),
    )
    eng = sa.create_engine("sqlite://")
    md.create_all(eng)
    with eng.begin() as c:
        c.execute(
            t.insert(),
            [
                {"id": "1", "project_id": None, "name": "shared"},
                {"id": "2", "project_id": "p1", "name": "a1"},
                {"id": "3", "project_id": "p2", "name": "b1"},
            ],
        )
    return t, eng


def _names(eng, stmt):
    with eng.connect() as c:
        return {r.name for r in c.execute(stmt)}


def test_project_scope_sees_own_plus_shared(table_engine):
    t, eng = table_engine
    stmt = apply_scope(sa.select(t), t, _ctx("p1"))
    assert _names(eng, stmt) == {"shared", "a1"}  # never b1


def test_org_scope_sees_shared_only(table_engine):
    t, eng = table_engine
    stmt = apply_scope(sa.select(t), t, _ctx(None))
    assert _names(eng, stmt) == {"shared"}


def test_never_leaks_other_project(table_engine):
    t, eng = table_engine
    assert "b1" not in _names(eng, apply_scope(sa.select(t), t, _ctx("p1")))


def test_write_scope_project_excludes_inherited_shared(table_engine):
    t, eng = table_engine
    assert _names(eng, apply_scope(sa.select(t), t, _ctx("p1"))) == {"shared", "a1"}
    assert _names(eng, apply_write_scope(sa.select(t), t, _ctx("p1"))) == {"a1"}


def test_write_scope_org_targets_shared_only(table_engine):
    t, eng = table_engine
    assert _names(eng, apply_write_scope(sa.select(t), t, _ctx(None))) == {"shared"}


def test_require_ctx_guards_unscoped():
    ctx = _ctx(None)
    assert require_ctx(ctx) is ctx
    with pytest.raises(UnscopedQueryError):
        require_ctx(None)
