# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""IdentityStore (W1): users, projects, memberships, invitations, sessions, context.

Runs on SQLite always, and on Postgres when SAGEWAI_TEST_DATABASE_URL is set
(via the dual-dialect ``dialect_engine`` fixture in tests/db/conftest.py).
"""

import pytest
import pytest_asyncio

from sagewai.admin.identity_store import (
    IdentityStore,
    InvitationError,
    TenantAccessError,
)


@pytest_asyncio.fixture
async def store(dialect_engine):
    s = IdentityStore(engine=dialect_engine)
    await s.init()
    return s


async def _org(store) -> str:
    return (await store.bootstrap_org("Acme", "acme"))["id"]


async def test_create_user_login_and_roles(store):
    oid = await _org(store)
    user = await store.create_user(
        oid, "alice@acme.io", password="pw123456", name="Alice", role="org:admin"
    )
    assert user["email"] == "alice@acme.io"
    assert await store.verify_credentials(oid, "alice@acme.io", "pw123456")
    assert await store.verify_credentials(oid, "alice@acme.io", "wrong") is None
    assert await store.resolve_roles(oid, user["id"]) == frozenset({"org:admin"})


async def test_password_reset(store):
    oid = await _org(store)
    user = await store.create_user(oid, "bob@acme.io", password="old12345")
    await store.set_password(oid, user["id"], "new12345")
    assert await store.verify_credentials(oid, "bob@acme.io", "new12345")
    assert await store.verify_credentials(oid, "bob@acme.io", "old12345") is None


async def test_project_membership_context(store):
    oid = await _org(store)
    admin = await store.create_user(oid, "admin@acme.io", password="pw0000", role="org:admin")
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    proj = await store.create_project(oid, "proj-a", "Project A")
    await store.add_membership(oid, member["id"], "project:member", project_id=proj["id"])

    ctx = await store.build_context(oid, member["id"], project_id=proj["id"])
    assert ctx.project_id == proj["id"]
    assert "project:member" in ctx.roles
    assert ctx.tenancy_mode == "multi"
    assert ctx.actor.label == "m@acme.io"

    # org admin can enter any project without an explicit project membership.
    ctx_admin = await store.build_context(oid, admin["id"], project_id=proj["id"])
    assert ctx_admin.is_org_admin


async def test_org_scope_context_when_no_project(store):
    oid = await _org(store)
    admin = await store.create_user(oid, "o@acme.io", password="pw0000", role="org:admin")
    ctx = await store.build_context(oid, admin["id"])
    assert ctx.project_id is None
    assert ctx.is_org_admin


async def test_project_only_user_no_hint_defaults_to_single_project(store):
    # RFC §4: a project-only user with no project hint must NOT fall back to org
    # scope — with exactly one membership, default to that project.
    oid = await _org(store)
    owner = await store.create_user(oid, "owner@acme.io", password="pw0000", role="org:owner")
    proj = await store.create_project(oid, "solo", "Solo")
    _rec, token = await store.create_invitation(
        oid, "po@acme.io", "project:member", owner["id"], project_id=proj["id"]
    )
    user = await store.accept_invitation(token, password="welcome1")

    ctx = await store.build_context(oid, user["id"])  # no project hint
    assert ctx.project_id == proj["id"]
    assert ctx.roles == frozenset({"project:member"})
    assert not ctx.is_org_admin


async def test_project_only_user_no_hint_multiple_requires_selection(store):
    # RFC §4: with multiple project memberships and no hint, selection is required.
    oid = await _org(store)
    owner = await store.create_user(oid, "owner2@acme.io", password="pw0000", role="org:owner")
    proj_a = await store.create_project(oid, "pa", "PA")
    proj_b = await store.create_project(oid, "pb", "PB")
    _rec, token = await store.create_invitation(
        oid, "po2@acme.io", "project:member", owner["id"], project_id=proj_a["id"]
    )
    user = await store.accept_invitation(token, password="welcome1")
    await store.add_membership(oid, user["id"], "project:member", project_id=proj_b["id"])

    with pytest.raises(TenantAccessError):
        await store.build_context(oid, user["id"])  # ambiguous — must select
    # Explicit selection resolves it.
    ctx = await store.build_context(oid, user["id"], project_id=proj_b["id"])
    assert ctx.project_id == proj_b["id"]
    assert "project:member" in ctx.roles


async def test_cross_project_access_denied(store):
    oid = await _org(store)
    proj_a = await store.create_project(oid, "a", "A")
    proj_b = await store.create_project(oid, "b", "B")
    user = await store.create_user(oid, "u@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, user["id"], "project:member", project_id=proj_a["id"])

    # A member of project A cannot build a context for project B.
    with pytest.raises(TenantAccessError):
        await store.build_context(oid, user["id"], project_id=proj_b["id"])
    # Nor for a non-existent project.
    with pytest.raises(TenantAccessError):
        await store.build_context(oid, user["id"], project_id="ghost")


async def test_invitation_project_only_user(store):
    oid = await _org(store)
    owner = await store.create_user(oid, "owner@acme.io", password="pw0000", role="org:owner")
    proj = await store.create_project(oid, "p", "P")
    _rec, token = await store.create_invitation(
        oid, "new@acme.io", "project:member", owner["id"], project_id=proj["id"]
    )
    user = await store.accept_invitation(token, password="welcome1")

    # Project-only user: a project membership, no org-level membership.
    assert await store.resolve_roles(oid, user["id"]) == frozenset({"project:member"})
    ctx = await store.build_context(oid, user["id"], project_id=proj["id"])
    assert "project:member" in ctx.roles

    # A token cannot be redeemed twice.
    with pytest.raises(InvitationError):
        await store.accept_invitation(token, password="x")


async def test_invitation_unknown_token(store):
    await _org(store)
    with pytest.raises(InvitationError):
        await store.accept_invitation("not-a-real-token", password="x")


async def test_sessions_issue_resolve_revoke(store):
    oid = await _org(store)
    user = await store.create_user(oid, "s@acme.io", password="pw0000")
    token = await store.issue_session(oid, user["id"])
    assert await store.resolve_session(token) == {"org_id": oid, "user_id": user["id"]}
    await store.revoke_session(token)
    assert await store.resolve_session(token) is None


async def test_expired_session_not_resolved(store):
    oid = await _org(store)
    user = await store.create_user(oid, "e@acme.io", password="pw0000")
    token = await store.issue_session(oid, user["id"], ttl_seconds=-1)  # already expired
    assert await store.resolve_session(token) is None


async def test_role_scope_python_validation(store):
    oid = await _org(store)
    user = await store.create_user(oid, "r@acme.io", password="pw0000")
    proj = await store.create_project(oid, "x", "X")
    with pytest.raises(ValueError):
        await store.add_membership(oid, user["id"], "project:admin")  # project role, no project
    with pytest.raises(ValueError):
        await store.add_membership(oid, user["id"], "org:admin", project_id=proj["id"])
    with pytest.raises(ValueError):
        await store.create_user(oid, "x@acme.io", role="project:admin")  # not an org role
