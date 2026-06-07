# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenancy mode flag + RequestContext (W0)."""

import pytest

from sagewai.admin import tenancy


def test_tenancy_mode_defaults_single(monkeypatch):
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    assert tenancy.tenancy_mode() == "single"
    assert tenancy.is_multi_tenant() is False


@pytest.mark.parametrize(
    "value,expected",
    [
        ("multi", "multi"),
        ("multi-tenant", "multi"),
        ("MULTI", "multi"),
        ("mt", "multi"),
        ("single", "single"),
        ("nonsense", "single"),
        ("", "single"),
    ],
)
def test_tenancy_mode_values(monkeypatch, value, expected):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", value)
    assert tenancy.tenancy_mode() == expected
    assert tenancy.is_multi_tenant() == (expected == "multi")


def test_single_org_context_preserves_foundation_behaviour():
    ctx = tenancy.single_org_context()
    assert ctx.tenancy_mode == "single"
    assert ctx.project_id is None
    assert ctx.is_org_admin
    assert ctx.has_role("org:admin")
    assert ctx.has_scope("admin")
    assert ctx.actor.id == "admin"


def test_request_context_role_and_scope_helpers():
    ctx = tenancy.RequestContext(
        actor=tenancy.UserRef(id="u1", label="a@x.io"),
        org_id="o1",
        project_id="p1",
        roles=frozenset({"project:member"}),
        scopes=frozenset({"read"}),
        request_id="req-1",
        tenancy_mode="multi",
    )
    assert ctx.has_role("project:member")
    assert not ctx.is_org_admin
    assert ctx.has_scope("read")
    assert not ctx.has_scope("admin")


def test_role_namespaces_are_disjoint():
    assert tenancy.ORG_ROLES.isdisjoint(tenancy.PROJECT_ROLES)
    assert "org:admin" in tenancy.ORG_ROLES
    assert "project:viewer" in tenancy.PROJECT_ROLES
    assert tenancy.is_org_role("org:owner")
    assert tenancy.is_project_role("project:admin")
    assert not tenancy.is_org_role("project:admin")
