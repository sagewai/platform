# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Route-level tenant scope helpers (W4): _project_scope / _require_resource_write."""

import types

import pytest

from sagewai.admin.authz import PermissionDeniedError
from sagewai.admin.serve import (
    _emit_audit,
    _in_read_scope,
    _in_write_scope,
    _owner,
    _project_scope,
    _require_resource_write,
)
from sagewai.admin.state_file import SHARED_ONLY
from sagewai.admin.tenancy import RequestContext, UserRef


def _ctx(project, roles, *, mode="multi"):
    return RequestContext(
        actor=UserRef("u", "u@x.io"),
        org_id="o1",
        project_id=project,
        roles=frozenset(roles),
        scopes=frozenset({"read", "write", "admin"}),
        request_id="r",
        tenancy_mode=mode,
    )


class _Map:
    def __init__(self, d):
        self._d = d

    def get(self, key, default=None):
        return self._d.get(key, default)


def _req(ctx=None, header=None):
    state = types.SimpleNamespace()
    if ctx is not None:
        state.context = ctx
    return types.SimpleNamespace(
        state=state,
        headers=_Map({"x-project-id": header} if header else {}),
        query_params=_Map({}),
    )


def test_multi_scope_uses_validated_ctx_not_header():
    # In multi mode the route trusts the session-validated project and ignores
    # any (already-middleware-validated) header value.
    r = _req(ctx=_ctx("pA", {"project:member"}), header="pZ-forged")
    assert _project_scope(r) == "pA"


def test_single_mode_uses_header_filter():
    assert _project_scope(_req(ctx=None, header="pHeader")) == "pHeader"
    # A single-org ctx also falls back to the header path (scope is organizational).
    r = _req(ctx=_ctx("pIgnored", {"org:admin"}, mode="single"), header="pHeader")
    assert _project_scope(r) == "pHeader"


def test_require_write_denies_viewer_in_multi():
    with pytest.raises(PermissionDeniedError):
        _require_resource_write(_req(ctx=_ctx("pA", {"project:viewer"})))
    # A member is allowed (no raise).
    _require_resource_write(_req(ctx=_ctx("pA", {"project:member"})))


def test_require_write_is_noop_in_single_mode():
    _require_resource_write(_req(ctx=None))  # no context
    _require_resource_write(_req(ctx=_ctx("pA", {"project:viewer"}, mode="single")))


def test_org_scope_uses_shared_sentinel_not_none():
    # Org scope must filter to org-shared rows only — never the legacy None=all.
    assert _project_scope(_req(ctx=_ctx(None, {"org:admin"}))) == SHARED_ONLY
    # Project scope returns the concrete project id.
    assert _project_scope(_req(ctx=_ctx("pA", {"project:member"}))) == "pA"
    # Single mode keeps None (header filter).
    assert _project_scope(_req(ctx=None)) is None


def test_owner_maps_sentinel_back_to_none_for_stamping():
    assert _owner(SHARED_ONLY) is None  # org-shared row
    assert _owner("pA") == "pA"
    assert _owner(None) is None


def test_read_scope_inherits_shared_but_not_other_projects():
    proj = _req(ctx=_ctx("pA", {"project:member"}))
    assert _in_read_scope("pA", proj) and _in_read_scope(None, proj)
    assert not _in_read_scope("pB", proj)
    org = _req(ctx=_ctx(None, {"org:admin"}))
    assert _in_read_scope(None, org) and not _in_read_scope("pA", org)
    assert _in_read_scope("anything", _req(ctx=None))  # single mode: no boundary


def test_write_scope_excludes_inherited_shared():
    proj = _req(ctx=_ctx("pA", {"project:member"}))
    assert _in_write_scope("pA", proj)
    assert not _in_write_scope(None, proj)  # may use shared, may not mutate it
    assert not _in_write_scope("pB", proj)
    assert _in_write_scope("anything", _req(ctx=None))  # single mode: no boundary


def test_host_exec_forced_off_in_multi_tenant(monkeypatch):
    # W7: a tenant must never reach host exec, even with the opt-in flag set.
    from sagewai.sandbox.policy import host_exec_allowed

    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "single")
    assert host_exec_allowed() is True  # trusted single-org operator opt-in
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    assert host_exec_allowed() is False  # forced off for tenants


class _FakeAudit:
    def __init__(self):
        self.calls = []

    async def append(
        self,
        org_id,
        project_id,
        action,
        *,
        actor_user_id=None,
        target_type=None,
        target_id=None,
        metadata=None,
    ):
        self.calls.append((org_id, project_id, action, actor_user_id, target_type, target_id))


def _req_with_audit(ctx, store):
    r = _req(ctx=ctx)
    r.app = types.SimpleNamespace(state=types.SimpleNamespace(tenant_audit=store))
    return r


async def test_emit_audit_appends_in_multi_tenant():
    store = _FakeAudit()
    await _emit_audit(
        _req_with_audit(_ctx("pA", {"project:member"}), store),
        "agent.create",
        target_type="agent",
        target_id="x",
    )
    assert store.calls == [("o1", "pA", "agent.create", "u", "agent", "x")]


async def test_emit_audit_noop_in_single_mode():
    store = _FakeAudit()
    await _emit_audit(_req_with_audit(None, store), "agent.create", target_id="x")
    assert store.calls == []


async def test_emit_audit_fails_closed_when_append_errors():
    # W8 gate: in multi-tenant mode an unrecordable audit must fail the write
    # (no silent success). The route's exception handler maps this to HTTP 503.
    from sagewai.admin.serve import _AuditUnavailableError

    class _Boom:
        async def append(self, *a, **k):
            raise RuntimeError("audit store down")

    req = _req_with_audit(_ctx("pA", {"project:member"}), _Boom())
    with pytest.raises(_AuditUnavailableError):
        await _emit_audit(req, "agent.create", target_id="x")
