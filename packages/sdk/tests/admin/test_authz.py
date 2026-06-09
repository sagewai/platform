# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""RBAC require()/can() primitive (W3)."""

import pytest

from sagewai.admin.authz import (
    PermissionDeniedError,
    Resource,
    TenantHiddenError,
    can,
    require,
    require_org_admin,
)
from sagewai.admin.tenancy import RequestContext, UserRef


def _ctx(roles, *, org="o1", project=None):
    return RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id=org,
        project_id=project,
        roles=frozenset(roles),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode="multi",
    )


def test_named_permission_org_admin_only():
    require("org:manage", _ctx({"org:owner"}))
    require("org:manage", _ctx({"org:admin"}))
    for roles in ({"org:member"}, {"project:admin"}, {"project:member"}):
        with pytest.raises(PermissionDeniedError):
            require("org:manage", _ctx(roles))


def test_org_shared_write_is_org_admin_only():
    shared = Resource("o1", None)
    require("resource:write", _ctx({"org:admin"}), on=shared)
    # project roles and org:member may read/use shared, but not mutate it.
    for roles in ({"org:member"}, {"project:admin"}, {"project:member"}):
        with pytest.raises(PermissionDeniedError):
            require("resource:write", _ctx(roles, project="p1"), on=shared)


def test_org_shared_read_and_execute_inheritance():
    shared = Resource("o1", None)
    # Everyone reads org-shared (inherited).
    require("resource:read", _ctx({"project:viewer"}, project="p1"), on=shared)
    require("resource:read", _ctx({"org:member"}), on=shared)
    # Execute on shared: project member yes, org member (read-only) no, viewer no.
    require("resource:execute", _ctx({"project:member"}, project="p1"), on=shared)
    with pytest.raises(PermissionDeniedError):
        require("resource:execute", _ctx({"org:member"}), on=shared)
    with pytest.raises(PermissionDeniedError):
        require("resource:execute", _ctx({"project:viewer"}, project="p1"), on=shared)


def test_project_scoped_write_and_read():
    proj = Resource("o1", "p1")
    ctx_member = _ctx({"org:member", "project:member"}, project="p1")
    ctx_viewer = _ctx({"org:member", "project:viewer"}, project="p1")
    require("resource:write", ctx_member, on=proj)
    require("resource:read", ctx_viewer, on=proj)
    with pytest.raises(PermissionDeniedError):
        require("resource:write", ctx_viewer, on=proj)  # viewer is read-only


def test_cross_project_target_is_404_not_403():
    # ctx bound to p1; target lives in p2 -> hidden (404), even for an org admin.
    other = Resource("o1", "p2")
    with pytest.raises(TenantHiddenError):
        require("resource:read", _ctx({"project:admin"}, project="p1"), on=other)
    with pytest.raises(TenantHiddenError):
        require("resource:write", _ctx({"org:admin"}, project="p1"), on=other)


def test_cross_org_target_is_404():
    with pytest.raises(TenantHiddenError):
        require("resource:read", _ctx({"org:admin"}, org="o1"), on=Resource("o2", None))


def test_can_is_boolean_and_never_raises():
    assert can("org:manage", _ctx({"org:owner"})) is True
    assert can("org:manage", _ctx({"project:viewer"}, project="p1")) is False
    assert (
        can("resource:read", _ctx({"project:admin"}, project="p1"), on=Resource("o1", "p2"))
        is False
    )


def test_resource_permission_requires_target():
    with pytest.raises(ValueError):
        require("resource:read", _ctx({"org:admin"}))  # no on=


# --- token-scope enforcement (effective permission = scope ∩ role, RFC §5) ---


def _scoped(roles, scopes, *, project="p1"):
    return RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id="o1",
        project_id=project,
        roles=frozenset(roles),
        scopes=frozenset(scopes),
        request_id="r",
        tenancy_mode="multi",
    )


def test_read_scope_token_cannot_write_or_execute():
    read_tok = _scoped({"project:member"}, {"read"})
    proj = Resource("o1", "p1")
    require("resource:read", read_tok, on=proj)  # read allowed (scope + role)
    with pytest.raises(PermissionDeniedError):
        require("resource:write", read_tok, on=proj)  # role allows, scope does not
    with pytest.raises(PermissionDeniedError):
        require("resource:execute", read_tok, on=proj)
    assert can("resource:write", read_tok, on=proj) is False


def test_write_scope_token_cannot_do_admin_named_perm():
    write_tok = RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id="o1",
        project_id=None,
        roles=frozenset({"org:admin"}),
        scopes=frozenset({"read", "write"}),
        request_id="r",
        tenancy_mode="multi",
    )
    with pytest.raises(PermissionDeniedError):
        require("org:manage", write_tok)  # needs admin scope
    require("org:manage", _ctx({"org:admin"}))  # full-scope admin can


def test_require_org_admin_enforces_admin_scope():
    write_tok = RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id="o1",
        project_id=None,
        roles=frozenset({"org:admin"}),
        scopes=frozenset({"read", "write"}),
        request_id="r",
        tenancy_mode="multi",
    )
    with pytest.raises(PermissionDeniedError):
        require_org_admin(write_tok)
    require_org_admin(_ctx({"org:admin"}))


# --- targeted permissions must carry a project target (no cross-project leak) ---


def test_targeted_permissions_require_a_target():
    for perm in ("audit:read", "project:member"):
        with pytest.raises(ValueError):
            require(perm, _ctx({"project:admin"}, project="p1"))  # no on=


def test_targeted_permissions_enforce_scope():
    pa = _ctx({"project:admin"}, project="p1")
    require("audit:read", pa, on=Resource("o1", "p1"))  # own project ok
    require("project:member", pa, on=Resource("o1", "p1"))
    # Same project admin cannot reach another project's chain/members -> 404.
    with pytest.raises(TenantHiddenError):
        require("audit:read", pa, on=Resource("o1", "p2"))
    with pytest.raises(TenantHiddenError):
        require("project:member", pa, on=Resource("o1", "p2"))
    # A viewer cannot read audit / manage members even in their own project -> 403.
    pv = _ctx({"project:viewer"}, project="p1")
    with pytest.raises(PermissionDeniedError):
        require("audit:read", pv, on=Resource("o1", "p1"))
    with pytest.raises(PermissionDeniedError):
        require("project:member", pv, on=Resource("o1", "p1"))
