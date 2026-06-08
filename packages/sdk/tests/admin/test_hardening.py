# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""W6 hostile-network hardening: security headers + request-size cap (multi-only)."""

import httpx
from fastapi import FastAPI, Request
from httpx import ASGITransport

from sagewai.admin.hardening import SecurityHeadersMiddleware


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(SecurityHeadersMiddleware)

    @app.get("/x")
    async def _x():
        return {"ok": True}

    @app.post("/x")
    async def _xp(request: Request):
        await request.body()
        return {"ok": True}

    return app


async def _req(method="GET", path="/x", headers=None, content=None):
    transport = ASGITransport(app=_app())
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers or {}, content=content)


async def test_security_headers_present_in_multi(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    r = await _req()
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"


async def test_security_headers_absent_in_single_mode(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "single")
    r = await _req()
    assert "x-frame-options" not in r.headers  # single-org path unchanged


async def test_hsts_only_emitted_with_tls(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.delenv("SAGEWAI_ADMIN_TLS", raising=False)
    assert "strict-transport-security" not in (await _req()).headers
    monkeypatch.setenv("SAGEWAI_ADMIN_TLS", "1")
    assert "strict-transport-security" in (await _req()).headers


async def test_request_size_cap_returns_413(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_MAX_REQUEST_BYTES", "10")
    assert (await _req("POST", content=b"x" * 50)).status_code == 413
    assert (await _req("POST", content=b"x")).status_code == 200


async def test_request_size_cap_disabled_in_single_mode(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "single")
    monkeypatch.setenv("SAGEWAI_MAX_REQUEST_BYTES", "10")
    assert (await _req("POST", content=b"x" * 50)).status_code == 200


async def _aiter(*chunks):
    for chunk in chunks:
        yield chunk


async def test_streamed_body_over_cap_returns_413(monkeypatch):
    # A chunked/streamed request has no Content-Length; the cap must still apply
    # by counting actual received bytes (the Content-Length-only bypass).
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_MAX_REQUEST_BYTES", "10")
    r = await _req("POST", content=_aiter(b"x" * 20, b"y" * 30))
    assert r.status_code == 413


async def test_413_rejection_carries_security_headers(monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_MAX_REQUEST_BYTES", "10")
    r = await _req("POST", content=b"x" * 50)
    assert r.status_code == 413
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["referrer-policy"] == "no-referrer"
