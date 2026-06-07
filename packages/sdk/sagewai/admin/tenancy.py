# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenancy mode + per-request context (W0 of the multi-tenancy/RBAC roadmap).

Two run modes, selected by ``SAGEWAI_TENANCY_MODE``:

- ``single`` (default) — the foundation behaviour: one admin, one org, the
  ``X-Project-ID`` header is an organisational filter, not a boundary.
- ``multi`` — multiple isolated projects under one org; project scope is a
  hard, session-derived boundary (enforced by the store + later workstreams).

``RequestContext`` is the seam the store operations accept so tenancy, RBAC,
and audit attribution are threaded explicitly rather than read from forgeable
client input. In single-org mode it is populated trivially via
``single_org_context()``; in multi-tenant mode it is built from the
authenticated user + their memberships (see ``IdentityStore.build_context``).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

SINGLE_ORG = "single"
MULTI_TENANT = "multi"

# Namespaced roles — scope-qualified so an org admin is never confused with a
# project admin in a resolved role set.
ORG_ROLES = frozenset({"org:owner", "org:admin", "org:member"})
PROJECT_ROLES = frozenset({"project:admin", "project:member", "project:viewer"})
ALL_ROLES = ORG_ROLES | PROJECT_ROLES

# Coarse token scopes carried over from the foundation.
SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_ADMIN = "admin"
ALL_SCOPES = frozenset({SCOPE_READ, SCOPE_WRITE, SCOPE_ADMIN})

_MULTI_ALIASES = {"multi", "multi-tenant", "multitenant", "mt"}


def tenancy_mode() -> str:
    """Resolve the configured tenancy mode (defaults to single-org)."""
    raw = os.environ.get("SAGEWAI_TENANCY_MODE", SINGLE_ORG).strip().lower()
    return MULTI_TENANT if raw in _MULTI_ALIASES else SINGLE_ORG


def is_multi_tenant() -> bool:
    """True when running in multi-tenant mode."""
    return tenancy_mode() == MULTI_TENANT


def is_org_role(role: str) -> bool:
    return role in ORG_ROLES


def is_project_role(role: str) -> bool:
    return role in PROJECT_ROLES


@dataclass(frozen=True)
class UserRef:
    """The acting user, for audit attribution."""

    id: str
    label: str  # e.g. "alice@example.com" / "api-token:CI"


@dataclass(frozen=True)
class RequestContext:
    """Authorisation + tenancy context threaded into store operations.

    ``project_id is None`` means org scope (shared resources only); a set
    ``project_id`` means that project's isolated resources plus org-shared.
    The store enforces the scope from this object — never from a header.
    """

    actor: UserRef
    org_id: str
    project_id: str | None
    roles: frozenset[str]
    scopes: frozenset[str]
    request_id: str
    tenancy_mode: str

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    @property
    def is_org_admin(self) -> bool:
        return bool(self.roles & {"org:owner", "org:admin"})


def single_org_context(
    *,
    actor_id: str = "admin",
    actor_label: str = "admin",
    org_id: str = "default",
    project_id: str | None = None,
    scopes: frozenset[str] = ALL_SCOPES,
    request_id: str = "",
) -> RequestContext:
    """A trivially-populated context for the single-org self-hosted path.

    Preserves the foundation's behaviour: one admin holding all scopes and the
    org-admin role, with project scope acting as an organisational filter.
    """
    return RequestContext(
        actor=UserRef(id=actor_id, label=actor_label),
        org_id=org_id,
        project_id=project_id,
        roles=frozenset({"org:admin"}),
        scopes=frozenset(scopes),
        request_id=request_id,
        tenancy_mode=SINGLE_ORG,
    )
