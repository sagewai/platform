# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant data-scope primitive (W2) — proven against a real table."""

import pytest
import sqlalchemy as sa

from sagewai.admin.scoping import (
    UnscopedQueryError,
    apply_scope,
    apply_write_scope,
    require_ctx,
    row_in_scope,
    row_writable,
    scope_values,
)
from sagewai.admin.tenancy import RequestContext, UserRef


def _ctx(org, project):
    return RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id=org,
        project_id=project,
        roles=frozenset(),
        scopes=frozenset(),
        request_id="r",
        tenancy_mode="multi",
    )


@pytest.fixture
def table_engine():
    md = sa.MetaData()
    t = sa.Table(
        "res",
        md,
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("org_id", sa.Text, nullable=False),
        sa.Column("project_id", sa.Text, nullable=True),
        sa.Column("name", sa.Text),
    )
    eng = sa.create_engine("sqlite://")
    md.create_all(eng)
    with eng.begin() as c:
        c.execute(
            t.insert(),
            [
                {"id": "1", "org_id": "o1", "project_id": None, "name": "shared"},
                {"id": "2", "org_id": "o1", "project_id": "p1", "name": "a1"},
                {"id": "3", "org_id": "o1", "project_id": "p2", "name": "b1"},
                {"id": "4", "org_id": "o2", "project_id": None, "name": "other"},
            ],
        )
    return t, eng


def _names(eng, stmt):
    with eng.connect() as c:
        return {r.name for r in c.execute(stmt)}


def test_project_scope_sees_own_plus_shared(table_engine):
    t, eng = table_engine
    stmt = apply_scope(sa.select(t), t, _ctx("o1", "p1"))
    assert _names(eng, stmt) == {"shared", "a1"}  # never b1, never other


def test_org_scope_sees_shared_only(table_engine):
    t, eng = table_engine
    stmt = apply_scope(sa.select(t), t, _ctx("o1", None))
    assert _names(eng, stmt) == {"shared"}


def test_never_leaks_other_org_or_project(table_engine):
    t, eng = table_engine
    assert "other" not in _names(eng, apply_scope(sa.select(t), t, _ctx("o1", "p1")))
    assert "b1" not in _names(eng, apply_scope(sa.select(t), t, _ctx("o1", "p1")))
    # An org with no rows sees nothing (no wildcard).
    assert _names(eng, apply_scope(sa.select(t), t, _ctx("o3", None))) == set()


def test_row_in_scope_mirrors_the_filter():
    pctx = _ctx("o1", "p1")
    assert row_in_scope({"org_id": "o1", "project_id": "p1"}, pctx)
    assert row_in_scope({"org_id": "o1", "project_id": None}, pctx)  # shared inherited
    assert not row_in_scope({"org_id": "o1", "project_id": "p2"}, pctx)  # other project
    assert not row_in_scope({"org_id": "o2", "project_id": None}, pctx)  # other org
    octx = _ctx("o1", None)
    assert row_in_scope({"org_id": "o1", "project_id": None}, octx)
    assert not row_in_scope({"org_id": "o1", "project_id": "p1"}, octx)  # org scope = shared only


def test_scope_values_from_ctx():
    assert scope_values(_ctx("o1", "p1")) == {"org_id": "o1", "project_id": "p1"}
    assert scope_values(_ctx("o1", None)) == {"org_id": "o1", "project_id": None}


def test_require_ctx_guards_unscoped():
    ctx = _ctx("o1", None)
    assert require_ctx(ctx) is ctx
    with pytest.raises(UnscopedQueryError):
        require_ctx(None)


# --- mutation scope: a project actor must NOT be able to mutate org-shared rows ---


def test_write_scope_project_excludes_inherited_shared(table_engine):
    t, eng = table_engine
    # Read scope inherits 'shared'; write scope must NOT (only own project rows).
    assert _names(eng, apply_scope(sa.select(t), t, _ctx("o1", "p1"))) == {"shared", "a1"}
    assert _names(eng, apply_write_scope(sa.select(t), t, _ctx("o1", "p1"))) == {"a1"}


def test_write_scope_org_targets_shared_only(table_engine):
    t, eng = table_engine
    assert _names(eng, apply_write_scope(sa.select(t), t, _ctx("o1", None))) == {"shared"}


def test_row_writable_blocks_shared_mutation_from_project():
    pctx = _ctx("o1", "p1")
    assert row_writable({"org_id": "o1", "project_id": "p1"}, pctx)  # own row
    assert not row_writable({"org_id": "o1", "project_id": None}, pctx)  # inherited shared
    assert not row_writable({"org_id": "o1", "project_id": "p2"}, pctx)  # other project
    octx = _ctx("o1", None)
    assert row_writable({"org_id": "o1", "project_id": None}, octx)  # org mutates shared
    assert not row_writable({"org_id": "o1", "project_id": "p1"}, octx)
