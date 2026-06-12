# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Full-stack admin endpoint smoke — every route returns SOMETHING sensible.

The launch coordination plan (§4 acceptance criteria #6) calls for
"all 144 admin API endpoints respond". This test enumerates every
route registered on the admin FastAPI app, hits each one with a
sane default request, and asserts the response is one of:

- 2xx (succeeded)
- 401 / 403 (correctly requires auth — expected for protected routes
  with no auth header in the smoke)
- 404 (path-parameter route hit with a synthetic id — expected)
- 422 (path-parameter or POST body validation failed — expected for
  routes with required body)

What's NOT acceptable:

- 500 (server crash — bug)
- 502 / 503 / 504 (upstream failure — bug)

The smoke catches the launch-day disaster scenario where some
recent change accidentally broke a route registration, returns the
wrong serialiser, or imports a module that crashes on first request.

Run::

    uv run --package sagewai --with pytest --with pytest-asyncio \
        --with pytest-httpx --with fastapi --with httpx pytest \
        packages/sdk/tests/test_admin_full_smoke.py -v
"""

from __future__ import annotations

import re

import pytest


# Routes that legitimately return 5xx in the smoke without external
# dependencies configured. These are *deliberate* 501/503 responses
# documenting that the feature is unavailable in this configuration —
# not server crashes.
_EXPECTED_5XX_PATTERNS: list[tuple[str, str]] = [
    (r"^/admin/runs/[^/]+/(pause|resume|cancel)$",
     "501 — run controls not configured in smoke admin app"),
    (r"^/api/v1/admin/directives/approvals/[^/]+/(approve|deny)$",
     "503 — directive approvals require running mission"),
    (r"^/api/v1/prompts/replay$",
     "501 — replay requires running agent"),
    (r"^/api/v1/mcp/call$",
     "501 — MCP server not connected in smoke"),
    (r"^/api/v1/eval/run$",
     "501 — eval requires running agent + LLM keys"),
]


def _is_expected_5xx(path: str) -> tuple[bool, str]:
    for pattern, reason in _EXPECTED_5XX_PATTERNS:
        if re.search(pattern, path):
            return True, reason
    return False, ""


def _synthesise_path_params(path: str) -> str:
    """Replace ``{name}`` placeholders with synthetic test values.

    Returns a concrete path the TestClient can call. Most routes
    will return 404 or similar — that's acceptable for a smoke test;
    we just need to verify the route doesn't 5xx.
    """
    return re.sub(r"\{(\w+)\}", r"smoke-\1", path)


@pytest.fixture(scope="module")
def app(tmp_path_factory):
    """A fully-constructed admin FastAPI app for smoke testing."""
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.admin.serve import create_admin_serve_app

    state_path = tmp_path_factory.mktemp("admin-smoke") / "admin-state.json"
    state_file = AdminStateFile(path=state_path)
    return create_admin_serve_app(state_file)


@pytest.fixture(scope="module")
def client(app):
    """A TestClient that exercises the admin app via lifespan."""
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


def _iter_routes(app):
    """Yield (method, path) tuples for every concrete HTTP route on the app."""
    for route in app.routes:
        # Skip non-HTTP routes (Starlette WebSocketRoute etc.)
        if not hasattr(route, "methods") or not hasattr(route, "path"):
            continue
        for method in sorted(route.methods or set()):
            if method in {"HEAD", "OPTIONS"}:
                continue
            yield method, route.path


# ── 1. Smoke: hit every GET route, expect non-5xx ─────────────────


def test_every_get_route_does_not_5xx(client, app):
    """Every GET route should return 2xx/3xx/4xx — never 5xx, never crash."""
    failures: list[tuple[str, int, str]] = []
    routes_tested = 0

    for method, path in _iter_routes(app):
        if method != "GET":
            continue
        concrete = _synthesise_path_params(path)
        try:
            resp = client.get(concrete)
        except Exception as exc:  # noqa: BLE001
            failures.append((concrete, -1, f"raised {type(exc).__name__}: {exc}"))
            continue

        routes_tested += 1
        if 500 <= resp.status_code < 600:
            is_expected, reason = _is_expected_5xx(path)
            if not is_expected:
                failures.append((concrete, resp.status_code, resp.text[:200]))

    if failures:
        msg = (
            f"{len(failures)} GET routes 5xx'd:\n"
            + "\n".join(f"  {p} → {s} {body}" for p, s, body in failures)
        )
        raise AssertionError(msg)

    # Sanity check that we tested a meaningful number of routes
    assert routes_tested >= 50, (
        f"Only tested {routes_tested} GET routes — admin app may be misconfigured"
    )
    print(f"\n  Smoke: {routes_tested} GET routes responded without 5xx ✓")


# ── 2. Smoke: every POST route either 4xx (validation) or 2xx ───


def test_every_post_route_does_not_5xx_with_empty_body(client, app):
    """POST routes with empty body should 4xx (validation) — never 5xx."""
    failures: list[tuple[str, int, str]] = []
    routes_tested = 0

    for method, path in _iter_routes(app):
        if method != "POST":
            continue
        concrete = _synthesise_path_params(path)
        try:
            resp = client.post(concrete, json={})
        except Exception as exc:  # noqa: BLE001
            failures.append((concrete, -1, f"raised {type(exc).__name__}: {exc}"))
            continue

        routes_tested += 1
        if 500 <= resp.status_code < 600:
            is_expected, reason = _is_expected_5xx(path)
            if not is_expected:
                failures.append((concrete, resp.status_code, resp.text[:200]))

    if failures:
        msg = (
            f"{len(failures)} POST routes 5xx'd with empty body:\n"
            + "\n".join(f"  {p} → {s} {body}" for p, s, body in failures)
        )
        raise AssertionError(msg)

    print(f"\n  Smoke: {routes_tested} POST routes returned non-5xx with empty body ✓")


# ── 3. Smoke: every PUT / DELETE / PATCH does not 5xx ─────────────


def test_every_mutating_route_does_not_5xx_with_empty_body(client, app):
    """PUT/DELETE/PATCH routes should 4xx (auth/validation) — never 5xx."""
    failures: list[tuple[str, str, int, str]] = []
    routes_tested = 0

    for method, path in _iter_routes(app):
        if method not in {"PUT", "DELETE", "PATCH"}:
            continue
        concrete = _synthesise_path_params(path)
        try:
            if method == "PUT":
                resp = client.put(concrete, json={})
            elif method == "PATCH":
                resp = client.patch(concrete, json={})
            else:  # DELETE
                resp = client.delete(concrete)
        except Exception as exc:  # noqa: BLE001
            failures.append((method, concrete, -1, f"raised {type(exc).__name__}: {exc}"))
            continue

        routes_tested += 1
        if 500 <= resp.status_code < 600:
            is_expected, reason = _is_expected_5xx(path)
            if not is_expected:
                failures.append((method, concrete, resp.status_code, resp.text[:200]))

    if failures:
        msg = (
            f"{len(failures)} mutating routes 5xx'd:\n"
            + "\n".join(f"  {m} {p} → {s} {body}" for m, p, s, body in failures)
        )
        raise AssertionError(msg)

    print(f"\n  Smoke: {routes_tested} PUT/DELETE/PATCH routes responded without 5xx ✓")


# ── 4. Sanity: route count is healthy ──────────────────────────────


def test_admin_app_has_at_least_100_routes(app):
    """The admin app should register a substantial number of routes.

    Per the launch coordination plan, the admin server provides 144
    endpoints. This is a rough lower bound to catch a future
    accidental router-removal regression.
    """
    routes = list(_iter_routes(app))
    assert len(routes) >= 100, (
        f"Only {len(routes)} routes registered — admin app may have lost a router"
    )
    print(f"\n  Total routes registered: {len(routes)}")


# ── 5. Health endpoints respond fast and OK ───────────────────────


def test_health_endpoints_respond_2xx(client):
    """The two health endpoints used by load balancers must respond 200."""
    for path in ("/api/v1/health/summary", "/api/v1/health/detailed"):
        resp = client.get(path)
        assert resp.status_code == 200, (
            f"Health endpoint {path} returned {resp.status_code}: {resp.text[:200]}"
        )


# ── 6. Setup status endpoint is unauthenticated and 200 ───────────


def test_setup_status_unauthenticated_returns_200(client):
    """First-launch setup-status must not require auth — that's the security boundary."""
    resp = client.get("/api/v1/setup/status")
    assert resp.status_code == 200, f"setup/status returned {resp.status_code}"
    body = resp.json()
    assert "setup_required" in body, f"setup/status response shape: {body}"
