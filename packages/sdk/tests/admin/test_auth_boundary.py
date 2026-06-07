# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations

import hashlib

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from sagewai.admin.auth_middleware import (
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    Principal,
    csrf_token_for,
    is_public,
    required_scope,
)
from sagewai.admin.state_file import AdminStateFile


def _make_setup(path):
    sf = AdminStateFile(path=path)
    sf.complete_setup(org_name="Acme", admin_email="a@b.com", admin_password="pw123456")
    return sf


def _make_stub_profile(pid: str):
    from sagewai.sealed.models import Profile
    return Profile(id=pid, name=pid, secrets={"k": "v"})


def _app_with(sf):
    from sagewai.admin.auth_middleware import AuthMiddleware
    app = FastAPI()
    app.add_middleware(AuthMiddleware, sf=sf)

    @app.get("/api/v1/organization")
    async def org():
        return {"ok": True}

    @app.post("/api/v1/projects")
    async def mk(request: Request):
        return {"actor": request.state.principal.actor_label}

    @app.post("/api/v1/tokens/")
    async def tok():
        return {"ok": True}
    return app


@pytest.mark.parametrize("path,setup_done,expect", [
    ("/api/v1/auth/login", True, True),
    ("/license", True, True),
    ("/api/v1/health/summary", True, True),
    ("/api/v1/setup/status", False, True),     # public only while incomplete
    ("/api/v1/setup/status", True, False),     # locked after setup
    ("/openapi.json", True, False),            # docs behind auth by default
    ("/api/v1/organization", True, False),
])
def test_is_public_rules(path, setup_done, expect):
    assert is_public("GET", path, setup_complete=setup_done, expose_docs=False) is expect


def test_options_always_public():
    assert is_public("OPTIONS", "/api/v1/organization", setup_complete=True) is True


def test_docs_public_when_exposed():
    assert is_public("GET", "/openapi.json", setup_complete=True, expose_docs=True) is True


@pytest.mark.parametrize("method,path,scope", [
    ("GET", "/api/v1/providers", SCOPE_READ),
    ("POST", "/api/v1/projects", SCOPE_WRITE),
    ("POST", "/api/v1/tokens/", SCOPE_ADMIN),
    ("POST", "/api/v1/fleet/workers/w1/approve", SCOPE_ADMIN),
    ("POST", "/api/v1/admin/sealed/profiles/p1/reveal/k", SCOPE_ADMIN),
    ("POST", "/api/v1/providers", SCOPE_ADMIN),     # secret material write
    ("PATCH", "/api/v1/organization", SCOPE_ADMIN), # settings write
    ("GET", "/api/v1/fleet/workers", SCOPE_READ),
    ("GET", "/api/v1/admin/sealed/profiles/p1/full", SCOPE_ADMIN),
    ("GET", "/api/v1/fleet/enrollment-keys", SCOPE_ADMIN),
    ("GET", "/api/v1/tokens/", SCOPE_ADMIN),
    ("POST", "/api/v1/account/password", SCOPE_ADMIN),
    ("PATCH", "/api/v1/account/profile", SCOPE_ADMIN),
    ("GET", "/api/v1/account", SCOPE_ADMIN),
    ("POST", "/api/v1/admin/connections", SCOPE_ADMIN),
    ("GET", "/api/v1/admin/connections", SCOPE_ADMIN),
])
def test_required_scope(method, path, scope):
    assert required_scope(method, path) == scope


def test_principal_scopes_helpers():
    p = Principal(type="api_token", subject_id="u", token_id="t",
                  scopes=frozenset({SCOPE_READ}), expires_at=None, actor_label="api:CI")
    assert p.has_scope(SCOPE_READ) and not p.has_scope(SCOPE_ADMIN)


def test_unauthenticated_is_401(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    c = TestClient(_app_with(sf))
    assert c.get("/api/v1/organization").status_code == 401


def test_session_cookie_authenticates(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c = TestClient(_app_with(sf))
    c.cookies.set("sagewai_auth", raw)
    assert c.get("/api/v1/organization").status_code == 200


def test_bearer_session_token_authenticates(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c = TestClient(_app_with(sf))
    assert c.get("/api/v1/organization", headers={"Authorization": f"Bearer {raw}"}).status_code == 200


def test_api_token_write_scope_cannot_hit_admin_route(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    created = sf.create_api_token(name="CI", scopes=["read", "write"])
    c = TestClient(_app_with(sf))
    h = {"Authorization": f"Bearer {created['token']}"}
    assert c.post("/api/v1/tokens/", headers=h).status_code == 403   # needs admin


def test_api_token_read_scope_cannot_mutate(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    created = sf.create_api_token(name="CI", scopes=["read"])
    c = TestClient(_app_with(sf))
    h = {"Authorization": f"Bearer {created['token']}", "X-CSRF-Token": "x"}
    # bearer is exempt from CSRF, but read scope can't POST
    assert c.post("/api/v1/projects", headers=h).status_code == 403


def test_csrf_required_for_cookie_mutations(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c = TestClient(_app_with(sf))
    c.cookies.set("sagewai_auth", raw)
    assert c.post("/api/v1/projects").status_code == 403   # no csrf token


def test_csrf_passes_with_matching_token(tmp_path):
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    token_id = hashlib.sha256(raw.encode()).hexdigest()[:16]
    csrf = csrf_token_for(sf, token_id)
    c = TestClient(_app_with(sf))
    c.cookies.set("sagewai_auth", raw)
    c.cookies.set("sagewai_csrf", csrf)
    assert c.post("/api/v1/projects", headers={"X-CSRF-Token": csrf}).status_code == 200


def _logged_in_client(state_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(state_path)
    app = create_admin_serve_app(sf)
    c = TestClient(app)
    r = c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pw123456"})
    assert r.status_code == 200
    c.headers.update({"X-CSRF-Token": c.cookies.get("sagewai_csrf")})
    return c, sf


def test_full_app_blocks_anon_and_allows_session(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    anon = TestClient(create_admin_serve_app(sf))
    assert anon.get("/api/v1/organization").status_code == 401
    c, _ = _logged_in_client(tmp_path / "s2.json")
    assert c.get("/api/v1/organization").status_code == 200


def test_full_app_logout_revokes(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    app = create_admin_serve_app(sf)
    c = TestClient(app)
    raw = c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pw123456"}).json()["access_token"]
    # token works as Bearer before logout
    assert c.get("/api/v1/organization", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    assert c.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {raw}"}).status_code == 200
    # server-side revoked: the same raw token is now rejected
    assert c.get("/api/v1/organization", headers={"Authorization": f"Bearer {raw}"}).status_code == 401


def test_full_app_create_token_then_list_redacts(tmp_path):
    c, sf = _logged_in_client(tmp_path / "s.json")
    r = c.post("/api/v1/tokens/", json={"name": "CI", "scopes": ["read"]})
    assert r.status_code == 201
    raw = r.json()["token"]
    assert raw.startswith("sw_")
    listed = c.get("/api/v1/tokens/").json()
    assert all("token" not in t and "token_hash" not in t for t in listed)


def test_login_throttles_after_repeated_failures(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    c = TestClient(create_admin_serve_app(sf))
    for _ in range(5):
        assert c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "wrong"}).status_code == 429


def test_sealed_reveal_audits_real_actor(tmp_path, monkeypatch):
    from sagewai.admin.serve import create_admin_serve_app
    import sagewai.admin.sealed_routes as sr

    sf = _make_setup(tmp_path / "s.json")
    app = create_admin_serve_app(sf)

    class _StubBackend:
        async def get_profile(self, pid):
            return _make_stub_profile(pid)

    monkeypatch.setattr(sr, "_backend", lambda: _StubBackend())

    c = TestClient(app)
    raw = c.post("/api/v1/auth/login", json={"email": "a@b.com", "password": "pw123456"}).json()["access_token"]
    # Bearer session token (admin scope, CSRF-exempt)
    r = c.post("/api/v1/admin/sealed/profiles/p1/reveal/k",
               headers={"Authorization": f"Bearer {raw}"})
    assert r.status_code == 200
    assert r.json()["value"] == "v"
    events = sf._read().get("audit_events", [])
    assert any(e["event_type"] == "sealed.reveal" and e["actor_label"] == "a@b.com"
               for e in events), "reveal must audit the real actor, not default-admin"


def test_sealed_reveal_requires_auth(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    c = TestClient(create_admin_serve_app(sf))
    assert c.post("/api/v1/admin/sealed/profiles/p1/reveal/k").status_code == 401


def test_host_exec_disabled_by_default(monkeypatch):
    from sagewai.admin.auth_middleware import host_exec_allowed
    monkeypatch.delenv("SAGEWAI_ALLOW_HOST_EXEC", raising=False)
    assert host_exec_allowed() is False
    monkeypatch.setenv("SAGEWAI_ALLOW_HOST_EXEC", "1")
    assert host_exec_allowed() is True


def test_every_non_public_route_requires_auth(tmp_path):
    """Caution 1: iterate the LIVE route list; every non-public route must 401
    when anonymous. Catches odd routes the prefix table might miss, and any
    future unguarded route."""
    import re
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.auth_middleware import is_public

    sf = _make_setup(tmp_path / "s.json")
    app = create_admin_serve_app(sf)
    c = TestClient(app)

    def concrete(p):
        return re.sub(r"\{[^}]+\}", "x", p)

    checked = 0
    leaks = []
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            path = route.path
            if is_public(method, path, setup_complete=True, expose_docs=False):
                continue
            resp = c.request(method, concrete(path))
            if resp.status_code == 200:
                leaks.append(f"{method} {path} -> 200 (LEAK)")
            elif resp.status_code != 401:
                leaks.append(f"{method} {path} -> {resp.status_code} (expected 401)")
            checked += 1
    assert not leaks, "Unauthenticated access issues:\n" + "\n".join(leaks)
    assert checked > 100, f"only checked {checked} routes"


def test_dev_trust_inert_without_env(tmp_path, monkeypatch):
    from sagewai.admin.serve import create_admin_serve_app
    monkeypatch.delenv("SAGEWAI_DEV_TRUST_LOCAL", raising=False)
    sf = _make_setup(tmp_path / "s.json")
    c = TestClient(create_admin_serve_app(sf))
    assert c.post("/api/v1/auth/refresh").status_code == 401


def test_cli_default_host_is_loopback():
    import importlib.util
    import pathlib

    # sagewai.cli.admin registers itself as a click Group in sys.modules,
    # so we load it as a plain module to access the admin_serve Command object.
    path = pathlib.Path(__file__).parent.parent.parent / "sagewai" / "cli" / "admin.py"
    spec = importlib.util.spec_from_file_location("_admin_plain", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    opt = next(p for p in mod.admin_serve.params if p.name == "host")
    assert opt.default == "127.0.0.1"


def test_write_token_cannot_change_admin_password(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    tok = sf.create_api_token(name="CI", scopes=["read", "write"])["token"]
    c = TestClient(create_admin_serve_app(sf))
    r = c.post("/api/v1/account/password",
               headers={"Authorization": f"Bearer {tok}"},
               json={"current_password": "pw123456", "new_password": "newpass1234"})
    assert r.status_code == 403


def test_sensitive_routes_require_admin_scope(tmp_path):
    """Live-route scope contract: every sensitive prefix must resolve to SCOPE_ADMIN.

    This prevents silent fall-through when new routes are added under
    credential-bearing or security-control paths.
    """
    from sagewai.admin.serve import create_admin_serve_app
    from sagewai.admin.auth_middleware import required_scope, SCOPE_ADMIN, _SAFE_METHODS

    sf = _make_setup(tmp_path / "s.json")
    app = create_admin_serve_app(sf)

    SENSITIVE = (
        "/api/v1/admin/sealed",
        "/api/v1/admin/connections",
        "/api/v1/admin/inference-providers",  # legacy connections redirect
        "/api/v1/tokens",
        "/api/v1/fleet/enrollment-keys",
        "/api/v1/account",
    )
    misses = []
    for route in app.routes:
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        path = route.path
        for method in (route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            sensitive = any(path.startswith(p) for p in SENSITIVE) or (
                path.startswith("/api/v1/providers") and method not in _SAFE_METHODS
            )
            if sensitive and required_scope(method, path) != SCOPE_ADMIN:
                misses.append(f"{method} {path}")
    assert not misses, "sensitive routes not admin-scoped:\n" + "\n".join(misses)


def test_admin_session_can_change_password(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    raw = sf.validate_login("a@b.com", "pw123456")["access_token"]
    c = TestClient(create_admin_serve_app(sf))
    r = c.post("/api/v1/account/password",
               headers={"Authorization": f"Bearer {raw}"},
               json={"current_password": "pw123456", "new_password": "newpass1234"})
    assert r.status_code == 200
    # old password no longer works, new one does
    assert sf.validate_login("a@b.com", "pw123456") is None
    assert sf.validate_login("a@b.com", "newpass1234") is not None


def test_auth_me_works_with_api_token(tmp_path):
    # /auth/me previously re-resolved a session token and 401'd API tokens;
    # it now trusts the middleware principal + get_admin_user.
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    tok = sf.create_api_token(name="CI", scopes=["read"])["token"]
    c = TestClient(create_admin_serve_app(sf))
    r = c.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"


def test_account_works_with_admin_api_token(tmp_path):
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    tok = sf.create_api_token(name="CI", scopes=["read", "write", "admin"])["token"]
    c = TestClient(create_admin_serve_app(sf))
    r = c.get("/api/v1/account", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["email"] == "a@b.com"


def test_account_read_token_denied_by_admin_scope(tmp_path):
    # the unification must NOT weaken the scope boundary: /api/v1/account is
    # admin-scoped, so a read-only API token is still rejected (403).
    from sagewai.admin.serve import create_admin_serve_app
    sf = _make_setup(tmp_path / "s.json")
    tok = sf.create_api_token(name="CI", scopes=["read"])["token"]
    c = TestClient(create_admin_serve_app(sf))
    assert c.get("/api/v1/account", headers={"Authorization": f"Bearer {tok}"}).status_code == 403
