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

from sagewai.admin.tenancy import is_multi_tenant, single_org_context

SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_ADMIN = "admin"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN})


from sagewai.sandbox.policy import host_exec_allowed  # re-export; keeps PR1 imports working

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

# Multi-tenant: prefixes that require org-admin for EVERY method (read + write).
# Two groups: (a) genuine org/system/global surfaces, and (b) stores that have no
# project_id column yet, so they cannot be safely exposed to a project context at
# all (reads would leak across projects) — gated org-admin as a documented interim
# until they grow a project_id (then move them to the project-scoped path). This is
# central + covers GETs, so it can't be missed per-handler. Project-scoped prefixes
# (providers, admin/connections, playground, admin/runs, prompts, autopilot
# missions, admin/projects sandbox-defaults, admin/workflows replay, admin/directives
# approvals|evaluations|runs) are deliberately NOT here — they enforce isolation at
# the data layer and are member-writable.
_MULTI_ORG_PREFIXES = (
    # org / system / global
    "/api/v1/organization",
    "/api/v1/projects",  # project CRUD (distinct from /api/v1/admin/projects sandbox-defaults)
    "/api/v1/tokens",
    "/api/v1/account",
    "/api/v1/audit",
    "/api/v1/connectors",
    "/api/v1/admin/sealed",  # sealed config + revocations
    "/api/v1/admin/agents",  # sandbox agent-requirements (org-level agent config)
    "/api/v1/admin/directives/policies",
    "/api/v1/admin/directives/preview",
    "/api/v1/fleet/enrollment-keys",
    "/api/v1/fleet/workers",  # approve/reject/revoke + list/detail (fleet mgmt)
    "/api/v1/fleet/audit",
    "/api/v1/harness",  # global proxy policy/keys/spend/config
    # no-project_id-column stores — org-admin interim (reads + writes) until scoped
    "/api/v1/workflow-registry",
    "/api/v1/budget",
    "/api/v1/guardrails",
    "/api/v1/eval",
    "/api/v1/notifications",
    "/api/v1/triggers",
    "/api/v1/memory",
)


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


def _scopes_for_roles(roles: frozenset[str]) -> frozenset[str]:
    """Token scopes derived from namespaced roles (multi-tenant sessions).

    org owners/admins get full ``admin`` scope; project admins/members get
    read+write (they manage their own project's resources); viewers and bare
    org members are read-only. This is what makes the read/write perimeter a
    real gate in multi-tenant mode (sessions no longer get blanket ALL_SCOPES).
    """
    if roles & {"org:owner", "org:admin"}:
        return ALL_SCOPES
    if roles & {"project:admin", "project:member"}:
        return frozenset({SCOPE_READ, SCOPE_WRITE})
    return frozenset({SCOPE_READ})


def required_scope(method: str, path: str, *, multi: bool = False) -> str:
    if multi:
        # Multi-tenant: org/system/global prefixes (and not-yet-project-scoped
        # stores) require org-admin for EVERY method — a central gate that covers
        # GETs too, so a sensitive read/list can't leak via a forgotten handler.
        # Everything else is a coarse read/write gate (viewer=read, member=write),
        # with project isolation enforced at the data layer. The single-org
        # admin-marking of project-scoped prefixes (providers/connections/...) must
        # NOT apply here, or it would lock project members out of their own work.
        if any(path.startswith(p) for p in _MULTI_ORG_PREFIXES):
            return SCOPE_ADMIN
        return SCOPE_READ if method in _SAFE_METHODS else SCOPE_WRITE
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


def _project_hint(request) -> str | None:
    """The client-supplied project hint — a *hint within authorized scope*, never
    an authorization input (multi-tenant resolves it against membership)."""
    return request.headers.get("x-project-id") or request.query_params.get("project_id") or None


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, sf, identity_store=None) -> None:
        super().__init__(app)
        self.sf = sf
        self._identity_store = identity_store
        self.expose_docs = os.environ.get("SAGEWAI_ADMIN_EXPOSE_DOCS", "") in {"1", "true"}

    def _identity(self):
        """The multi-tenant identity store (lazy — only constructed in multi mode)."""
        if self._identity_store is None:
            from sagewai.admin.identity_store import IdentityStore

            self._identity_store = IdentityStore()
        return self._identity_store

    async def dispatch(self, request, call_next):
        method, path = request.method, request.url.path
        if is_public(method, path, setup_complete=self.sf.is_setup_complete(),
                     expose_docs=self.expose_docs):
            return await call_next(request)

        raw, mech = _extract(request)
        if not raw:
            return JSONResponse({"detail": "Authentication required"}, status_code=401)

        if is_multi_tenant():
            resolved = await self._resolve_multi(request, raw)
            if isinstance(resolved, JSONResponse):
                return resolved
            principal, context = resolved
        else:
            principal = self._resolve(raw, mech)
            if principal is None:
                return JSONResponse({"detail": "Invalid or expired credential"}, status_code=401)
            # Single-org: scope is organizational, not a boundary — carry the header
            # hint so routes that read ctx behave like today's _project_id filter.
            context = single_org_context(
                actor_id=principal.subject_id,
                actor_label=principal.actor_label,
                scopes=principal.scopes,
                project_id=_project_hint(request),
            )

        need = required_scope(method, path, multi=is_multi_tenant())
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
        request.state.context = context
        return await call_next(request)

    async def _resolve_multi(self, request, raw: str):
        """Multi-tenant resolution: session -> RequestContext (tenancy from the
        session + membership, never from X-Project-ID). Returns (principal, context)
        or a JSONResponse error. API tokens are wired in W4; sessions only here.
        """
        from sagewai.admin.identity_store import TenantAccessError

        store = self._identity()
        sess = await store.resolve_session(raw)
        if sess is None:
            return JSONResponse({"detail": "Invalid or expired credential"}, status_code=401)
        try:
            context = await store.build_context(
                sess["org_id"], sess["user_id"], project_id=_project_hint(request)
            )
        except TenantAccessError:
            # Forged/foreign project, or selection required — hide existence (404).
            return JSONResponse({"detail": "Not found"}, status_code=404)
        principal = Principal(
            type="session",
            subject_id=sess["user_id"],
            token_id=_session_token_id(raw),
            scopes=_scopes_for_roles(context.roles),
            expires_at=None,
            actor_label=context.actor.label,
        )
        return principal, context

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
