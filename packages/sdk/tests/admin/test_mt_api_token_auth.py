# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Adversarial cross-tenant suite for multi-tenant API-token authentication.

W0 RFC §5/§6/§9 cat-5 ("bypass token scope"). A bearer API token in
multi-tenant mode carries ``read/write/admin`` scopes AND is bound to a scope
(``project_id = P`` or ``project_id = NULL`` = org-shared only). The effective
permission is the INTERSECTION of the token's scope and the subject's role; a
token NEVER exceeds its owner's role and is NEVER a data-scope wildcard. It
flows through ``ctx`` like a session.

These tests actively attack the boundary at the HTTP perimeter (real admin app
under ASGITransport) plus a store-unit parity layer (dual-dialect).
"""

from __future__ import annotations

import hashlib
import os

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.api_token_store import ApiTokenStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.provider_store import PostgresProviderStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine
from sagewai.db.models import Base

# ───────────────────────────── fixtures ─────────────────────────────


# Dual-dialect engine for the store-unit tests, mirroring tests/db/conftest.py's
# ``dialect_engine`` (which is not visible from tests/admin/). SQLite always;
# Postgres additionally when SAGEWAI_TEST_DATABASE_URL is set.
_PG_URL = os.environ.get("SAGEWAI_TEST_DATABASE_URL")
_DIALECTS = ["sqlite"] + (["postgres"] if _PG_URL else [])


@pytest_asyncio.fixture(params=_DIALECTS)
async def dialect_engine(request, tmp_path):
    from sagewai.db.engine import create_engine as _ce

    if request.param == "sqlite":
        engine = _ce(f"sqlite+aiosqlite:///{tmp_path / 'parity.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        engine = _ce(_PG_URL)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    """A two-project org with a member in PA and an org-admin, the real admin
    app wired multi-tenant on one shared engine, plus the ApiTokenStore so we
    can mint project-bound tokens directly (the CRUD-route minting is exercised
    separately).
    """
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    ident = IdentityStore(engine=engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    pb = (await ident.create_project(oid, "pb", "PB"))["id"]

    # A project member in PA (read+write within PA, no org-admin).
    member = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, member["id"], "project:member", project_id=pa)
    # A project member in PB.
    member_b = await ident.create_user(oid, "b@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, member_b["id"], "project:member", project_id=pb)
    # An org-admin (full scope; can mint org-shared tokens).
    admin = await ident.create_user(oid, "a@acme.io", password="pw0000", role="org:admin")

    token_store = ApiTokenStore(engine=engine)
    await token_store.init()

    # Seed a provider in PB so a PA-bound token's cross-project read is hidden.
    prov = PostgresProviderStore(engine=engine, identity_store=ident)
    await prov.init()
    ctx_b = await ident.build_context(oid, member_b["id"], project_id=pb)
    await prov.upsert({"provider_name": "pb-secret", "config": {}}, ctx=ctx_b)

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    res = AdminResourceStore(engine=engine)
    await res.init()

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(
        sf,
        identity_store=ident,
        admin_resource_store=res,
        api_token_store=token_store,
        provider_store=prov,
    )
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit

    yield {
        "app": app,
        "ident": ident,
        "token_store": token_store,
        "prov": prov,
        "oid": oid,
        "pa": pa,
        "pb": pb,
        "member": member,
        "member_b": member_b,
        "admin": admin,
    }
    await engine.dispose()


async def _mint(env, *, user_id, scopes, project_id, role="project:member"):
    """Mint a project- (or org-) bound token directly via the store, building a
    ctx for that user so the store stamps org/subject from ctx (not the body)."""
    ident = env["ident"]
    ctx = await ident.build_context(env["oid"], user_id, project_id=project_id)
    record, plaintext = await env["token_store"].create_for(
        ctx, name=f"t-{scopes}", scopes=set(scopes), project_id=project_id
    )
    return record, plaintext


def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


def _bearer(token):
    return {"authorization": f"Bearer {token}"}


# ──────────────────── cat-5: token scope ∩ role ─────────────────────


@pytest.mark.asyncio
async def test_read_token_rejected_on_writes_accepted_on_reads(env):
    """A read-scope token: the write perimeter 403s; reads pass."""
    _, plaintext = await _mint(
        env, user_id=env["member"]["id"], scopes={"read"}, project_id=env["pa"]
    )
    async with _client(env["app"]) as c:
        # read: list providers in PA — allowed
        r = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert r.status_code == 200, r.text
        # write: create a provider — read scope cannot mutate (403)
        w = await c.post(
            "/api/v1/providers",
            headers=_bearer(plaintext),
            json={"provider_name": "x", "config": {}},
        )
        assert w.status_code == 403, w.text


@pytest.mark.asyncio
async def test_write_token_can_write_in_its_project(env):
    """A write-scope token writes within its own project (sanity: scope grants it)."""
    _, plaintext = await _mint(
        env, user_id=env["member"]["id"], scopes={"read", "write"}, project_id=env["pa"]
    )
    async with _client(env["app"]) as c:
        w = await c.post(
            "/api/v1/providers",
            headers=_bearer(plaintext),
            json={"provider_name": "pa-prov", "config": {}},
        )
        assert w.status_code == 200, w.text


@pytest.mark.asyncio
async def test_project_a_token_cannot_reach_project_b_resource(env):
    """A PA-bound token never sees PB's provider (project isolation via ctx)."""
    _, plaintext = await _mint(
        env, user_id=env["member"]["id"], scopes={"read"}, project_id=env["pa"]
    )
    async with _client(env["app"]) as c:
        # The list is scoped to PA + org-shared; PB's project-isolated row is hidden.
        r = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert r.status_code == 200, r.text
        names = [p.get("provider_name") for p in r.json()]
        assert "pb-secret" not in names

        # A forged X-Project-ID: B is IGNORED for a token — its scope comes from the
        # token row (PA), not the header. The request stays PA-scoped (200, PB still
        # hidden), so the header cannot widen the token to another project.
        forged = await c.get(
            "/api/v1/providers",
            headers={**_bearer(plaintext), "x-project-id": env["pb"]},
        )
        assert forged.status_code == 200, forged.text
        assert "pb-secret" not in [p.get("provider_name") for p in forged.json()]


@pytest.mark.asyncio
async def test_org_shared_token_is_not_all_projects(env):
    """A project_id=NULL (org-shared) token reads org-shared only — a specific
    project's private resource stays hidden (NOT an all-projects token)."""
    # Mint an org-shared token owned by the org-admin (only an org-admin may).
    ident = env["ident"]
    ctx_admin = await ident.build_context(env["oid"], env["admin"]["id"], project_id=None)
    _, plaintext = await env["token_store"].create_for(
        ctx_admin, name="org-shared", scopes={"read"}, project_id=None
    )
    async with _client(env["app"]) as c:
        r = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert r.status_code == 200, r.text
        names = [p.get("provider_name") for p in r.json()]
        # org-shared scope must NOT surface PB's project-isolated provider.
        assert "pb-secret" not in names


@pytest.mark.asyncio
async def test_expired_token_401(env):
    """An expired token is rejected at the perimeter (401)."""
    from datetime import datetime, timedelta, timezone

    ident = env["ident"]
    ctx = await ident.build_context(env["oid"], env["member"]["id"], project_id=env["pa"])
    _, plaintext = await env["token_store"].create_for(
        ctx,
        name="expired",
        scopes={"read"},
        project_id=env["pa"],
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    async with _client(env["app"]) as c:
        r = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_revoked_token_401(env):
    """A revoked token is rejected at the perimeter (401)."""
    record, plaintext = await _mint(
        env, user_id=env["member"]["id"], scopes={"read"}, project_id=env["pa"]
    )
    ident = env["ident"]
    ctx = await ident.build_context(env["oid"], env["member"]["id"], project_id=env["pa"])
    assert await env["token_store"].revoke_for(ctx, record["id"]) is True
    async with _client(env["app"]) as c:
        r = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert r.status_code == 401, r.text


@pytest.mark.asyncio
async def test_admin_scope_capped_by_member_role(env):
    """A token with ``admin`` scope whose owner is only project:member does NOT
    get admin — effective scope = token ∩ role (intersection), so an org-admin
    route 403s rather than letting the token escalate."""
    _, plaintext = await _mint(
        env,
        user_id=env["member"]["id"],
        scopes={"read", "write", "admin"},
        project_id=env["pa"],
    )
    async with _client(env["app"]) as c:
        # /api/v1/tokens is an org-admin (every-method) prefix; a project member's
        # role caps the admin scope away → 403 (perimeter), never 2xx.
        r = await c.get("/api/v1/tokens/", headers=_bearer(plaintext))
        assert r.status_code == 403, r.text


# ──────────────── token CRUD is project-isolated ────────────────────


@pytest.mark.asyncio
async def test_token_crud_is_project_isolated(env):
    """PA's org-admin-minted tokens are isolated from PB at the store layer:
    a ctx in PA cannot list or revoke a PB-scoped token."""
    ident = env["ident"]
    # Org-admin mints one token bound to PA and one bound to PB.
    ctx_pa = await ident.build_context(env["oid"], env["admin"]["id"], project_id=env["pa"])
    ctx_pb = await ident.build_context(env["oid"], env["admin"]["id"], project_id=env["pb"])
    rec_pa, _ = await env["token_store"].create_for(
        ctx_pa, name="ci-pa", scopes={"read"}, project_id=env["pa"]
    )
    rec_pb, _ = await env["token_store"].create_for(
        ctx_pb, name="ci-pb", scopes={"read"}, project_id=env["pb"]
    )
    # A PA ctx lists only PA (+ org-shared) tokens — never PB's.
    listed_pa = await env["token_store"].list_for(ctx_pa)
    ids_pa = {t["id"] for t in listed_pa}
    assert rec_pa["id"] in ids_pa
    assert rec_pb["id"] not in ids_pa
    # A PA ctx cannot revoke a PB token (out of write scope → False, no effect).
    assert await env["token_store"].revoke_for(ctx_pa, rec_pb["id"]) is False
    # The PB token is still live (its hash still resolves and is not revoked).
    found = await env["token_store"].find_by_hash(rec_pb["token_hash"])
    assert found is not None and found["revoked_at"] is None


@pytest.mark.asyncio
async def test_crud_route_returns_plaintext_once_then_list_redacts(env):
    """POST /tokens returns the plaintext exactly once; GET /tokens redacts it
    (never the hash or the plaintext)."""
    # Mint a session for the org-admin so the org-admin CRUD routes are reachable.
    ident = env["ident"]
    session = await ident.issue_session(env["oid"], env["admin"]["id"])
    async with _client(env["app"]) as c:
        created = await c.post(
            "/api/v1/tokens/",
            headers=_bearer(session),
            json={"name": "CI", "scopes": ["read"]},
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert body.get("token", "").startswith("swt_")
        plaintext = body["token"]

        listed = await c.get("/api/v1/tokens/", headers=_bearer(session))
        assert listed.status_code == 200, listed.text
        rows = listed.json()
        blob = str(rows)
        assert plaintext not in blob  # plaintext never re-returned
        assert "token_hash" not in blob  # hash never exposed
        assert all("token" not in t or t.get("token") is None for t in rows)
        # The minted token authenticates as a real bearer credential.
        whoami = await c.get("/api/v1/providers", headers=_bearer(plaintext))
        assert whoami.status_code == 200, whoami.text


# ─────────────────── store-unit (dual-dialect) ──────────────────────


@pytest.mark.asyncio
async def test_store_create_find_revoke_expire(dialect_engine):
    """create → find_by_hash → revoke → expired, on both dialects."""
    from datetime import datetime, timedelta, timezone

    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    user = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, user["id"], "project:member", project_id=pa)
    ctx = await ident.build_context(oid, user["id"], project_id=pa)

    store = ApiTokenStore(engine=dialect_engine)
    await store.init()

    record, plaintext = await store.create_for(
        ctx, name="CI", scopes={"read", "write"}, project_id=pa
    )
    assert plaintext.startswith("swt_")
    h = hashlib.sha256(plaintext.encode()).hexdigest()
    found = await store.find_by_hash(h)
    assert found is not None
    assert found["org_id"] == oid
    assert found["project_id"] == pa
    assert found["subject_user_id"] == user["id"]
    assert set(found["scopes"]) == {"read", "write"}
    assert found["revoked_at"] is None

    # revoke → find_by_hash still resolves the row but marks it revoked.
    assert await store.revoke_for(ctx, record["id"]) is True
    found2 = await store.find_by_hash(h)
    assert found2 is not None and found2["revoked_at"] is not None

    # an expired token: the row exists but carries a past expiry.
    rec_exp, pt_exp = await store.create_for(
        ctx,
        name="exp",
        scopes={"read"},
        project_id=pa,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    f_exp = await store.find_by_hash(hashlib.sha256(pt_exp.encode()).hexdigest())
    assert f_exp is not None and f_exp["expires_at"] is not None

    # find_by_hash of an unknown hash → None.
    assert await store.find_by_hash("0" * 64) is None


@pytest.mark.asyncio
async def test_store_rejects_invalid_scopes(dialect_engine):
    """scopes must be a subset of {read, write, admin}."""
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    user = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, user["id"], "project:member", project_id=pa)
    ctx = await ident.build_context(oid, user["id"], project_id=pa)
    store = ApiTokenStore(engine=dialect_engine)
    await store.init()
    with pytest.raises(ValueError):
        await store.create_for(ctx, name="bad", scopes={"read", "superuser"}, project_id=pa)


@pytest.mark.asyncio
async def test_store_list_redacts_hash(dialect_engine):
    """list_for never returns token_hash or plaintext; carries a masked suffix."""
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    user = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, user["id"], "project:member", project_id=pa)
    ctx = await ident.build_context(oid, user["id"], project_id=pa)
    store = ApiTokenStore(engine=dialect_engine)
    await store.init()
    _, plaintext = await store.create_for(ctx, name="CI", scopes={"read"}, project_id=pa)
    rows = await store.list_for(ctx)
    assert rows
    row = rows[0]
    assert "token_hash" not in row
    assert "token" not in row
    assert plaintext not in str(row)
    assert row["scopes"] == ["read"]
    assert row["name"] == "CI"
    assert "suffix" in row  # a masked suffix for the operator to recognise it


@pytest.mark.asyncio
async def test_store_project_token_pins_project_id_to_ctx(dialect_engine):
    """A project member cannot mint a token for a different project_id than ctx's."""
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    pb = (await ident.create_project(oid, "pb", "PB"))["id"]
    user = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, user["id"], "project:member", project_id=pa)
    ctx = await ident.build_context(oid, user["id"], project_id=pa)
    store = ApiTokenStore(engine=dialect_engine)
    await store.init()
    # project_id must equal ctx.project_id for a project-scoped mint.
    with pytest.raises(PermissionError):
        await store.create_for(ctx, name="x", scopes={"read"}, project_id=pb)


@pytest.mark.asyncio
async def test_store_org_shared_token_requires_org_admin(dialect_engine):
    """Only an org-admin ctx may mint an org-shared (project_id=None) token."""
    ident = IdentityStore(engine=dialect_engine)
    await ident.init()
    oid = (await ident.bootstrap_org("Acme", "acme"))["id"]
    pa = (await ident.create_project(oid, "pa", "PA"))["id"]
    member = await ident.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await ident.add_membership(oid, member["id"], "project:member", project_id=pa)
    ctx_member = await ident.build_context(oid, member["id"], project_id=pa)
    store = ApiTokenStore(engine=dialect_engine)
    await store.init()
    # A project member minting an org-shared token → denied.
    with pytest.raises(PermissionError):
        await store.create_for(ctx_member, name="org", scopes={"read"}, project_id=None)
    # An org-admin minting an org-shared token → allowed.
    admin = await ident.create_user(oid, "a@acme.io", password="pw0000", role="org:admin")
    ctx_admin = await ident.build_context(oid, admin["id"], project_id=None)
    rec, _ = await store.create_for(ctx_admin, name="org", scopes={"read"}, project_id=None)
    assert rec["project_id"] is None
