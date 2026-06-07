# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

"""Deny-by-default auth boundary for the admin backend (spec §3.A–§3.F)."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_ADMIN = "admin"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN})


def host_exec_allowed() -> bool:
    """Host-backed bash/NullBackend/MCP exec is opt-in, regardless of bind address.

    Disabled by default; the published backend image never sets this.
    """
    return os.environ.get("SAGEWAI_ALLOW_HOST_EXEC", "") in {"1", "true"}

_SAFE_METHODS = {"GET", "HEAD"}

# Paths public regardless of setup state.
_PUBLIC_ALWAYS = (
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
    "/license",
)
_PUBLIC_PREFIXES = ("/api/v1/health",)
_DOCS_PATHS = ("/docs", "/redoc", "/openapi.json")

# Prefixes where EVERY method (incl. GET) requires admin — they expose secret
# material or security-control surfaces.
_ADMIN_ALL_METHODS = (
    "/api/v1/admin/sealed",           # GET .../full returns secrets; reveal
    "/api/v1/fleet/enrollment-keys",  # enrollment-key management
    "/api/v1/tokens",                 # API-token management
    "/api/v1/account",                # account profile/password — credential surface
    "/api/v1/admin/connections",      # credential-bearing connection CRUD
    "/api/v1/admin/inference-providers",  # legacy connection redirect
)
# Prefixes where only mutating methods require admin.
_ADMIN_MUTATION_PREFIXES = (
    "/api/v1/providers",
    "/api/v1/organization",
    "/api/v1/sandbox",
    "/api/v1/notifications",
    "/api/v1/connectors",
)
_ADMIN_FLEET_ACTIONS = ("/approve", "/reject", "/revoke")


@dataclass(frozen=True)
class Principal:
    type: Literal["session", "api_token"]
    subject_id: str
    token_id: str
    scopes: frozenset[str]
    expires_at: datetime | None
    actor_label: str

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes


def is_public(method: str, path: str, *, setup_complete: bool, expose_docs: bool = False) -> bool:
    if method == "OPTIONS":
        return True
    if path in _PUBLIC_ALWAYS or any(path.startswith(p) for p in _PUBLIC_PREFIXES):
        return True
    if path.startswith("/api/v1/setup"):
        return not setup_complete          # public only while setup incomplete
    if expose_docs and path in _DOCS_PATHS:
        return True
    return False


def required_scope(method: str, path: str) -> str:
    if any(path.startswith(p) for p in _ADMIN_ALL_METHODS):
        return SCOPE_ADMIN
    if method not in _SAFE_METHODS and any(path.startswith(p) for p in _ADMIN_MUTATION_PREFIXES):
        return SCOPE_ADMIN
    if path.startswith("/api/v1/fleet/workers") and any(path.endswith(a) for a in _ADMIN_FLEET_ACTIONS):
        return SCOPE_ADMIN
    return SCOPE_READ if method in _SAFE_METHODS else SCOPE_WRITE


def _csrf_secret(sf) -> bytes:
    """Server-only CSRF signing key (persisted; never returned over the wire)."""
    return sf.get_or_create_csrf_secret().encode("ascii")


def csrf_token_for(sf, session_token_id: str) -> str:
    return hmac.new(_csrf_secret(sf), session_token_id.encode(), hashlib.sha256).hexdigest()


def _session_token_id(raw: str) -> str:
    # 16-hex-char (64-bit) per-session id; HMAC input for CSRF binding.
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class _LoginThrottle:
    """In-memory per-(ip,email) sliding window. Single-process by contract.

    Instantiated by the login route (serve.py auth_login).
    """

    def __init__(self, limit: int = 5, window: int = 900) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque] = defaultdict(deque)

    def blocked(self, key: str) -> bool:
        now = time.time()
        dq = self._hits.get(key)
        if not dq:
            return False
        while dq and dq[0] < now - self.window:
            dq.popleft()
        if not dq:
            del self._hits[key]
            return False
        return len(dq) >= self.limit

    def record_failure(self, key: str) -> None:
        self._hits[key].append(time.time())

    def reset(self, key: str) -> None:
        self._hits.pop(key, None)


def _extract(request) -> tuple[str | None, str]:
    """Return (raw_token, mechanism), mechanism in 'bearer'|'cookie'|''."""
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:], "bearer"
    cookie = request.cookies.get("sagewai_auth")
    return (cookie, "cookie") if cookie else (None, "")


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, sf) -> None:
        super().__init__(app)
        self.sf = sf
        self.expose_docs = os.environ.get("SAGEWAI_ADMIN_EXPOSE_DOCS", "") in {"1", "true"}

    async def dispatch(self, request, call_next):
        method, path = request.method, request.url.path
        if is_public(method, path, setup_complete=self.sf.is_setup_complete(),
                     expose_docs=self.expose_docs):
            return await call_next(request)

        raw, mech = _extract(request)
        if not raw:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        principal = self._resolve(raw, mech)
        if principal is None:
            return JSONResponse({"detail": "Invalid or expired credential"}, status_code=401)

        need = required_scope(method, path)
        if not principal.has_scope(need):
            return JSONResponse({"detail": f"Requires '{need}' scope"}, status_code=403)

        # CSRF: cookie-auth mutations only (Bearer is non-ambient → exempt).
        if mech == "cookie" and method not in _SAFE_METHODS:
            sent = request.headers.get("x-csrf-token", "")
            cookie = request.cookies.get("sagewai_csrf", "")
            expected = csrf_token_for(self.sf, principal.token_id)
            if not (sent and hmac.compare_digest(sent, cookie)
                    and hmac.compare_digest(sent, expected)):
                return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)

        request.state.principal = principal
        return await call_next(request)

    def _resolve(self, raw: str, mech: str) -> Principal | None:
        user = self.sf.get_user_by_token(raw)          # session (cookie or bearer)
        if user is not None:
            return Principal(
                type="session", subject_id=user["id"],
                token_id=_session_token_id(raw), scopes=ALL_SCOPES,
                expires_at=None, actor_label=user.get("email") or "admin",
            )
        if mech == "bearer":
            tok = self.sf.find_api_token(raw)
            if tok is not None:
                return Principal(
                    type="api_token", subject_id="api", token_id=tok["id"],
                    scopes=frozenset(tok.get("scopes", [])), expires_at=None,
                    actor_label=f"api-token:{tok.get('name', '')}",
                )
        return None
