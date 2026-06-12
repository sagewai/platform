# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""`GET /api/v1/readyz` is a DEEP readiness probe for orchestrators/ops.

Unlike the shallow, in-process `/health/detailed` (state-file read + config
inventory), `/readyz` actually exercises the production dependencies (DB,
tenant stores, master key, audit store) and returns 200 only when every
*configured* dependency is reachable, 503 otherwise. It is PUBLIC (orchestrator
probes don't authenticate) and therefore leak-free: it returns only component
STATUS strings, never error messages, connection strings, or secrets. It reuses
the app's already-pooled engines/stores — never a fresh per-call pool.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile

# Only these status strings may ever appear in a /readyz check.
_ALLOWED_STATUSES = {"ok", "error", "not_configured"}


def _app(tmp_path):
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    return create_admin_serve_app(sf), sf


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path)


def _assert_leak_free(text: str) -> None:
    """A public endpoint must never leak secrets, connection strings, or detail."""
    low = text.lower()
    for needle in (
        "password",
        "secret",
        "traceback",
        "sqlite",
        "postgres",
        "postgresql",
        "://",  # any URL / connection string scheme
        "/users/",  # filesystem paths
        "exception",
    ):
        assert needle not in low, f"/readyz leaked {needle!r}: {text!r}"


@pytest.mark.asyncio
async def test_readyz_public_no_auth_required(tmp_path):
    """Single-org: reachable WITHOUT auth (public), 200, ready: true."""
    app, _sf = _app(tmp_path)
    r = await _get(app, "/api/v1/readyz")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ready"] is True
    assert isinstance(body["checks"], list) and body["checks"]


@pytest.mark.asyncio
async def test_readyz_single_org_check_shape(tmp_path):
    """Single-org: state_file ok; DB/stores/audit/key not_configured (no DB)."""
    app, _sf = _app(tmp_path)
    r = await _get(app, "/api/v1/readyz")
    assert r.status_code == 200
    checks = {c["name"]: c["status"] for c in r.json()["checks"]}
    # only the three sanctioned status strings ever appear
    assert set(checks.values()) <= _ALLOWED_STATUSES, checks
    assert checks["state_file"] == "ok"
    # single-org has no DB / tenant stores wired
    assert checks["database"] == "not_configured"
    assert checks["identity_store"] == "not_configured"
    assert checks["resource_store"] == "not_configured"
    assert checks["tenant_audit"] == "not_configured"
    # master_key in single-org (no encrypted tenant secrets) is neutral
    assert checks["master_key"] in ("ok", "not_configured")
    # rate-limit backend is multi-only (in-memory single-process in single-org)
    assert checks["rate_limit"] == "not_configured"


@pytest.mark.asyncio
async def test_readyz_is_leak_free(tmp_path):
    """The public body carries no error text / secret / connection-string leak."""
    app, _sf = _app(tmp_path)
    r = await _get(app, "/api/v1/readyz")
    _assert_leak_free(r.text)


@pytest.mark.asyncio
async def test_readyz_state_file_error_is_503_and_leak_free(tmp_path, monkeypatch):
    """Degraded path: if the state-file probe raises, overall is NOT ready (503),
    state_file: error — and STILL no leaked detail.

    The middleware also reads ``sf`` (``is_setup_complete``) on every request, so
    a plain ``sf._read`` that always raises would 500 before the handler. We use a
    call-count side-effect: the first read (middleware setup gate) succeeds, the
    second (the probe) raises — so the request reaches the handler and the probe
    is the thing that fails.
    """
    app, sf = _app(tmp_path)

    real_read = sf._read
    calls = {"n": 0}

    def _flaky_read(*a, **k):
        calls["n"] += 1
        # let the middleware's setup-complete read through, fail the probe read
        if calls["n"] >= 2:
            raise OSError("disk gone: /Users/secret/state.json")
        return real_read(*a, **k)

    monkeypatch.setattr(sf, "_read", _flaky_read)

    r = await _get(app, "/api/v1/readyz")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["ready"] is False
    checks = {c["name"]: c["status"] for c in body["checks"]}
    assert checks["state_file"] == "error"
    # the raised exception text (a fake path) must NOT appear in the response
    _assert_leak_free(r.text)
    assert "disk gone" not in r.text


@pytest.mark.asyncio
async def test_readyz_database_error_is_503(tmp_path, monkeypatch):
    """Degraded path: a configured DB whose probe raises → ready: false, 503,
    database: error, and no leaked detail.

    We drive the helper directly (no live engine needed): point
    ``_resolve_database_url`` at a configured URL and stub the engine resolver so
    the probe's ``engine.connect()`` raises. This proves the configured→error→503
    path without spinning up a broken real pool.
    """
    import sagewai.admin.serve as serve

    app, _sf = _app(tmp_path)

    monkeypatch.setattr(serve, "_resolve_database_url", lambda: "sqlite+aiosqlite:///x")

    class _BrokenEngine:
        def connect(self):
            raise RuntimeError("connection refused to postgres://secret@host/db")

    # the helper resolves an engine for the SELECT 1; make that engine broken
    monkeypatch.setattr(serve, "_readyz_db_engine", lambda request: _BrokenEngine())

    r = await _get(app, "/api/v1/readyz")
    assert r.status_code == 503, r.text
    body = r.json()
    assert body["ready"] is False
    checks = {c["name"]: c["status"] for c in body["checks"]}
    assert checks["database"] == "error"
    _assert_leak_free(r.text)
    assert "connection refused" not in r.text


@pytest.mark.asyncio
async def test_readyz_database_ok_with_real_sqlite_engine(tmp_path, monkeypatch):
    """A configured DB whose SELECT 1 succeeds → database: ok (reuses an engine,
    no fresh pool); overall stays ready: true."""
    import sagewai.admin.serve as serve
    from sagewai.db.engine import create_engine

    app, _sf = _app(tmp_path)
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'rdy.db'}")

    monkeypatch.setattr(serve, "_resolve_database_url", lambda: "sqlite+aiosqlite:///rdy.db")
    monkeypatch.setattr(serve, "_readyz_db_engine", lambda request: engine)

    try:
        r = await _get(app, "/api/v1/readyz")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ready"] is True
        checks = {c["name"]: c["status"] for c in body["checks"]}
        assert checks["database"] == "ok"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_readyz_multi_tenant_probes_ok(tmp_path, monkeypatch):
    """Multi-tenant: identity_store / resource_store / tenant_audit / database all
    reachable on a shared in-process engine → ok, ready: true. Proves the probes
    reuse the wired stores' engines (no fresh pool) and report ok when live."""
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    from sagewai.admin.admin_resource_store import AdminResourceStore
    from sagewai.admin.identity_store import IdentityStore
    from sagewai.admin.resource_stores import ResourceStores
    from sagewai.admin.tenant_audit import TenantAuditStore
    from sagewai.db.engine import create_engine

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'mt.db'}")
    identity = IdentityStore(engine=engine)
    await identity.init()
    res = AdminResourceStore(engine=engine)
    await res.init()
    audit = TenantAuditStore(engine=engine)
    await audit.init()

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    app = create_admin_serve_app(sf, identity_store=identity, admin_resource_store=res)
    app.state.resource_stores = ResourceStores(admin_resource=res)
    app.state.tenant_audit = audit

    try:
        r = await _get(app, "/api/v1/readyz")
        assert r.status_code == 200, r.text
        checks = {c["name"]: c["status"] for c in r.json()["checks"]}
        assert set(checks.values()) <= _ALLOWED_STATUSES
        assert checks["identity_store"] == "ok"
        assert checks["resource_store"] == "ok"
        assert checks["tenant_audit"] == "ok"
        assert checks["database"] == "ok"
        # rate-limit backend wired + reachable (shares the tenant engine)
        assert checks["rate_limit"] == "ok"
        # master_key: no encrypted secrets exist, so neutral (not an error)
        assert checks["master_key"] in ("ok", "not_configured")
        _assert_leak_free(r.text)
    finally:
        await engine.dispose()


def test_readyz_is_public_in_middleware():
    """The middleware must classify /api/v1/readyz as public (orchestrator probes
    do not authenticate)."""
    from sagewai.admin.auth_middleware import is_public

    assert is_public("GET", "/api/v1/readyz", setup_complete=True) is True
