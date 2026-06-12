# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Audit-actor attribution must use the authenticated actor.

Directive approve/deny and Sealed revocation lift formerly recorded a hardcoded
``"default-admin"`` placeholder, and the directive routes trusted a spoofable
``body["actor"]``. These tests pin the honest behaviour: the recorded actor is
the authenticated principal, never the placeholder and never the body value.
"""
from __future__ import annotations

import json

import httpx
import pytest
from fastapi import FastAPI, Request

from sagewai.admin._actor import actor_id_for
from sagewai.admin.tenancy import single_org_context

# ── actor_id_for unit behaviour ─────────────────────────────────────────────

class _State:
    pass


class _Req:
    def __init__(self, state):
        self.state = state


def test_actor_id_for_prefers_context_actor_label():
    state = _State()
    state.context = single_org_context(actor_id="u-1", actor_label="alice@example.com")
    assert actor_id_for(_Req(state)) == "alice@example.com"


def test_actor_id_for_falls_back_to_principal_actor_label():
    state = _State()

    class _P:
        actor_label = "api-token:CI"
        subject_id = "tok-9"

    state.principal = _P()
    assert actor_id_for(_Req(state)) == "api-token:CI"


def test_actor_id_for_never_raises_without_context_or_principal():
    # Unwired test app: no auth middleware attached anything to request.state.
    assert actor_id_for(_Req(_State())) == "admin"


# ── directive approve/deny attribution (no Postgres needed) ──────────────────

class _RecordingApprovals:
    """Minimal stand-in for PendingApprovalsRegistry that records the actor."""

    def __init__(self):
        self.calls: list[tuple[str, str, str | None]] = []

    async def list_pending(self):
        return []  # no scope guard rows → _guard_approval_scope falls through

    async def approve(self, *, decision_id, actor, note=None):
        self.calls.append(("approve", actor, note))
        return {"decision_id": decision_id, "decided_by": actor, "status": "approved"}

    async def deny(self, *, decision_id, actor, note=None):
        self.calls.append(("deny", actor, note))
        return {"decision_id": decision_id, "decided_by": actor, "status": "denied"}


@pytest.fixture
def state_file(tmp_path, monkeypatch):
    path = tmp_path / "admin-state.json"
    path.write_text(json.dumps({"workflows": {}}))
    from sagewai.admin import state_file as state_file_module

    monkeypatch.setattr(state_file_module, "default_admin_state_path", lambda: path)
    return path


def _build_app(state_file, approvals, *, actor_label: str):
    """Directive app whose middleware sets an authenticated single-org context."""
    from sagewai.admin import directive_routes
    from sagewai.admin.state_file import AdminStateFile

    app = FastAPI()
    directive_routes.register(app, AdminStateFile(), approvals=approvals)

    @app.middleware("http")
    async def _inject_ctx(request: Request, call_next):
        request.state.context = single_org_context(
            actor_id="auth-user", actor_label=actor_label,
        )
        return await call_next(request)

    return app


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test")


@pytest.mark.asyncio
async def test_approve_records_authenticated_actor_not_placeholder(state_file):
    approvals = _RecordingApprovals()
    app = _build_app(state_file, approvals, actor_label="ops@example.com")
    async with await _client(app) as cl:
        res = await cl.post(
            "/api/v1/admin/directives/approvals/dec-1/approve",
            json={"actor": "attacker@evil.com", "note": "looks fine"},
        )
    assert res.status_code == 200, res.text
    assert approvals.calls == [("approve", "ops@example.com", "looks fine")]
    # Body-spoofed actor must NOT win; placeholder must NOT appear.
    recorded_actor = approvals.calls[0][1]
    assert recorded_actor == "ops@example.com"
    assert recorded_actor != "attacker@evil.com"
    assert recorded_actor != "default-admin"


@pytest.mark.asyncio
async def test_deny_records_authenticated_actor_not_body_value(state_file):
    approvals = _RecordingApprovals()
    app = _build_app(state_file, approvals, actor_label="ops@example.com")
    async with await _client(app) as cl:
        res = await cl.post(
            "/api/v1/admin/directives/approvals/dec-2/deny",
            json={"actor": "attacker@evil.com", "note": "nope"},
        )
    assert res.status_code == 200, res.text
    assert approvals.calls == [("deny", "ops@example.com", "nope")]
    assert approvals.calls[0][1] != "attacker@evil.com"
    assert approvals.calls[0][1] != "default-admin"


@pytest.mark.asyncio
async def test_approve_unwired_context_falls_back_to_admin_not_placeholder(state_file):
    """Without an auth context, the fallback is "admin" — never "default-admin"."""
    from sagewai.admin import directive_routes
    from sagewai.admin.state_file import AdminStateFile

    approvals = _RecordingApprovals()
    app = FastAPI()
    directive_routes.register(app, AdminStateFile(), approvals=approvals)
    async with await _client(app) as cl:
        res = await cl.post(
            "/api/v1/admin/directives/approvals/dec-3/approve",
            json={"actor": "attacker@evil.com"},
        )
    assert res.status_code == 200, res.text
    assert approvals.calls[0][1] == "admin"
    assert approvals.calls[0][1] != "default-admin"
