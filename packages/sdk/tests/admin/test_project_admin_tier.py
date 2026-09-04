# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The project-admin tier: project admins and org admins pass, members and viewers do not."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi import HTTPException
from starlette.datastructures import Headers
from starlette.requests import Request

from sagewai.admin.authz import (
    PermissionDeniedError,
    Resource,
    TenantHiddenError,
    can,
    require,
)
from sagewai.admin.serve import _require_project_admin
from sagewai.admin.tasks_routes import _task_project_scope
from sagewai.admin.tenancy import ALL_SCOPES, RequestContext, UserRef

_WRITE = frozenset({"read", "write"})


def _ctx(role: str, *, project_id: str | None = "pa") -> RequestContext:
    scopes = (
        ALL_SCOPES
        if role.startswith("org:")
        else (_WRITE if role != "project:viewer" else frozenset({"read"}))
    )
    return RequestContext(
        actor=UserRef(id="u1", label="u1@acme.io"),
        org_id="acme",
        project_id=project_id,
        roles=frozenset({role}),
        scopes=scopes,
        request_id="r1",
        tenancy_mode="multi",
    )


def _request(context: RequestContext | None, headers: dict[str, str]) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/v1/tasks",
        "query_string": b"",
        "headers": Headers(headers).raw,
    }
    request = Request(scope)
    if context is not None:
        request.state.context = context
    return request


@pytest.mark.parametrize(
    ("role", "allowed"),
    [
        ("org:owner", True),
        ("org:admin", True),
        ("project:admin", True),
        ("project:member", False),
        ("project:viewer", False),
    ],
)
def test_project_admin_permission_is_granted_to_admins_only(role: str, allowed: bool) -> None:
    ctx = _ctx(role)
    assert can("project:admin", ctx, on=Resource(ctx.org_id, ctx.project_id)) is allowed


def test_project_admin_on_another_project_is_hidden_not_forbidden() -> None:
    ctx = _ctx("project:admin", project_id="pa")
    with pytest.raises(TenantHiddenError):
        require("project:admin", ctx, on=Resource(ctx.org_id, "pb"))


@pytest.mark.parametrize("role", ["project:member", "project:viewer"])
def test_require_project_admin_refuses_members_and_viewers(role: str) -> None:
    request = _request(_ctx(role), {"x-project-id": "pa"})
    with pytest.raises(PermissionDeniedError):
        _require_project_admin(request)


@pytest.mark.parametrize("role", ["org:admin", "project:admin"])
def test_require_project_admin_admits_admins(role: str) -> None:
    request = _request(_ctx(role), {"x-project-id": "pa"})
    assert _require_project_admin(request) is None


def test_require_project_admin_is_a_no_op_without_a_multi_tenant_context() -> None:
    assert _require_project_admin(_request(None, {"x-project-id": "pa"})) is None


def test_require_project_admin_is_a_no_op_in_single_org_mode() -> None:
    single = replace(_ctx("project:viewer"), tenancy_mode="single")
    assert _require_project_admin(_request(single, {"x-project-id": "pa"})) is None


def test_project_admin_requires_the_write_scope() -> None:
    ctx = replace(_ctx("project:admin"), scopes=frozenset({"read"}))
    assert can("project:admin", ctx, on=Resource(ctx.org_id, ctx.project_id)) is False


def test_task_scope_returns_the_selected_project() -> None:
    assert _task_project_scope(_request(None, {"x-project-id": "pa"})) == "pa"


def test_task_scope_refuses_the_global_scope() -> None:
    with pytest.raises(HTTPException) as raised:
        _task_project_scope(_request(None, {"x-project-id": "global"}))
    assert raised.value.status_code == 400
    assert raised.value.detail == "Tasks require an explicit project; there is no global Task scope"


def test_task_scope_still_requires_a_scope_at_all() -> None:
    with pytest.raises(HTTPException) as raised:
        _task_project_scope(_request(None, {}))
    assert raised.value.status_code == 400
    assert raised.value.detail == "Work project scope is required"
