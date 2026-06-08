# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""RBAC enforcement primitive (W3 of the multi-tenancy/RBAC roadmap).

A single ``require(permission, ctx, on=...)`` gate, used across routes, that
implements the W0 RFC §5 model. RBAC is *secondary* to tenancy: this checks what
an actor may do **within a scope it already holds**; the data-scope filter
(``scoping.py``) is what decides which rows a query returns.

Two failure modes, matching the RFC:
- **403 (PermissionDeniedError)** — the actor is in scope but lacks the role.
- **404 (TenantHiddenError)** — the target belongs to another tenant; existence is
  hidden (never 403, which would leak that it exists).

Org-shared resources (``project_id is None``) are readable/executable by
inheritance but **writable only by org owners/admins**; project-scoped
resources follow that project's roles.

Token scopes also gate: the effective permission is the **intersection of the
token scope and the role** (RFC §5) — a ``read``-scoped token can never write,
execute, or perform an admin action even if its role would otherwise allow it.
"""

from __future__ import annotations

from dataclasses import dataclass

from sagewai.admin.tenancy import (
    ALL_ROLES,
    SCOPE_ADMIN,
    SCOPE_READ,
    SCOPE_WRITE,
    RequestContext,
)

_ORG_ADMINS = frozenset({"org:owner", "org:admin"})


class PermissionDeniedError(Exception):
    """In scope but under-privileged — maps to HTTP 403."""


class TenantHiddenError(Exception):
    """Target belongs to another tenant — maps to HTTP 404 (no existence leak)."""


@dataclass(frozen=True)
class Resource:
    """A permission target. ``project_id is None`` means an org-shared resource."""

    org_id: str
    project_id: str | None = None


# Org-level named permissions (no per-project target) -> roles that grant them.
_GRANTS: dict[str, frozenset[str]] = {
    "org:manage": _ORG_ADMINS,
    "user:invite": _ORG_ADMINS,
    "user:manage": _ORG_ADMINS,
    "project:create": _ORG_ADMINS,
    "project:list": _ORG_ADMINS,
    "project:manage": _ORG_ADMINS,
}

# Project-scoped named permissions — MUST carry a target so the cross-scope
# check (_in_scope) runs; a project admin may act only on their own project,
# and an org admin only through a concrete resolved project context.
_TARGETED_GRANTS: dict[str, frozenset[str]] = {
    "project:member": _ORG_ADMINS | {"project:admin"},
    "audit:read": _ORG_ADMINS | {"project:admin"},
}

_RESOURCE_VERBS = ("read", "write", "execute")

# Required token scope per permission (effective permission = token scope ∩ role,
# RFC §5). Admin operations need 'admin'; resource verbs map read->read and
# write/execute->write.
_NAMED_SCOPE: dict[str, str] = {
    "org:manage": SCOPE_ADMIN,
    "user:invite": SCOPE_ADMIN,
    "user:manage": SCOPE_ADMIN,
    "project:create": SCOPE_ADMIN,
    "project:list": SCOPE_ADMIN,
    "project:manage": SCOPE_ADMIN,
    "project:member": SCOPE_ADMIN,
    "audit:read": SCOPE_ADMIN,
}
_VERB_SCOPE: dict[str, str] = {
    "read": SCOPE_READ,
    "write": SCOPE_WRITE,
    "execute": SCOPE_WRITE,
}


def _required_scope(permission: str) -> str:
    if permission in _NAMED_SCOPE:
        return _NAMED_SCOPE[permission]
    if permission.startswith("resource:"):
        verb = permission.split(":", 1)[1]
        if verb not in _VERB_SCOPE:
            raise ValueError(f"unknown resource permission: {permission!r}")
        return _VERB_SCOPE[verb]
    raise ValueError(f"unknown permission: {permission!r}")


def _resource_grant(verb: str, *, shared: bool) -> frozenset[str]:
    """Roles allowed to perform ``verb`` on an org-shared vs project-scoped target."""
    if verb == "read":
        # Org-shared resources are readable by every role (inheritance);
        # project resources by that project's roles (incl. viewer).
        return (
            ALL_ROLES
            if shared
            else frozenset(
                {"org:owner", "org:admin", "project:admin", "project:member", "project:viewer"}
            )
        )
    if verb == "execute":
        return frozenset({"org:owner", "org:admin", "project:admin", "project:member"})
    if verb == "write":
        # Org-shared writes are org-admin only; project writes by project admins/members.
        return (
            _ORG_ADMINS
            if shared
            else frozenset({"org:owner", "org:admin", "project:admin", "project:member"})
        )
    raise ValueError(f"unknown resource verb: {verb!r}")


def _granting_roles(permission: str, on: Resource | None) -> frozenset[str]:
    if permission in _GRANTS:
        return _GRANTS[permission]
    if permission in _TARGETED_GRANTS:
        if on is None:
            raise ValueError(f"{permission!r} requires a target (on=Resource(...))")
        return _TARGETED_GRANTS[permission]
    if permission.startswith("resource:"):
        verb = permission.split(":", 1)[1]
        if verb not in _RESOURCE_VERBS:
            raise ValueError(f"unknown resource permission: {permission!r}")
        if on is None:
            raise ValueError(f"{permission!r} requires a target (on=Resource(...))")
        return _resource_grant(verb, shared=on.project_id is None)
    raise ValueError(f"unknown permission: {permission!r}")


def _in_scope(ctx: RequestContext, on: Resource) -> bool:
    """True if ``on`` is within ctx's data scope (org match + project match/shared)."""
    if on.org_id != ctx.org_id:
        return False
    # Org-shared targets are in scope for the whole org; a project-scoped target
    # is in scope only when ctx is bound to that exact project.
    return on.project_id is None or on.project_id == ctx.project_id


def can(permission: str, ctx: RequestContext, *, on: Resource | None = None) -> bool:
    """Boolean form of :func:`require` (for UI gating); never raises on a denial."""
    if on is not None and not _in_scope(ctx, on):
        return False
    if not ctx.has_scope(_required_scope(permission)):
        return False
    return bool(ctx.roles & _granting_roles(permission, on))


def require(permission: str, ctx: RequestContext, *, on: Resource | None = None) -> None:
    """Enforce ``permission`` for ``ctx`` (optionally on a target ``on``).

    Order: :class:`TenantHiddenError` (404) if the target is another tenant's,
    then :class:`PermissionDeniedError` (403) if the **token scope** or the
    actor's **roles** don't grant it (effective permission = scope ∩ role, §5).
    """
    if on is not None and not _in_scope(ctx, on):
        raise TenantHiddenError(f"{permission}: target not in scope")
    grant = _granting_roles(permission, on)
    needed = _required_scope(permission)
    if not ctx.has_scope(needed):
        raise PermissionDeniedError(f"{permission}: requires '{needed}' token scope")
    if not (ctx.roles & grant):
        raise PermissionDeniedError(f"{permission}: requires one of {sorted(grant)}")


def require_org_admin(ctx: RequestContext) -> None:
    """Require org owner/admin for an org/system-level route. No-op in single-org.

    The bounded set of org/system surfaces (org + project settings, API tokens,
    sealed/security config, credential revocation, directive policies, fleet
    enrollment, autopilot enable/disable, …) must never be reachable by a plain
    project member. Raises :class:`PermissionDeniedError` (403) otherwise. Project
    *isolation* is the data layer's job; this is purely the org-authority gate.
    """
    if ctx.tenancy_mode != "multi":
        return
    if not (ctx.roles & _ORG_ADMINS):
        raise PermissionDeniedError("requires org owner/admin")


def in_read_scope(record_project_id: str | None, ctx: RequestContext) -> bool:
    """True if a record is readable by ``ctx`` (own project + inherited org-shared).

    Permissive (always True) in single-org mode. Use to FILTER list results so a
    project never sees another project's rows.
    """
    if ctx.tenancy_mode != "multi":
        return True
    return record_project_id == ctx.project_id or record_project_id is None


def require_in_project_scope(
    record_project_id: str | None, ctx: RequestContext, *, write: bool = False
) -> None:
    """Tenant-isolation gate for a record fetched by id/name. No-op in single-org.

    The universal by-id guard: a cross-project record is hidden
    (:class:`TenantHiddenError` → 404, never 403); a project member writing an
    inherited org-shared (``project_id is None``) record is denied
    (:class:`PermissionDeniedError` → 403). Read = own + inherited org-shared;
    write = own project only (org-shared writes need org owner/admin).
    """
    if ctx.tenancy_mode != "multi":
        return
    pid = ctx.project_id
    if record_project_id is not None and record_project_id != pid:
        raise TenantHiddenError("target not in scope")
    if pid is not None and record_project_id is None and not write:
        return  # project member reading an inherited org-shared record — allowed
    if write and record_project_id is None and not (ctx.roles & _ORG_ADMINS):
        raise PermissionDeniedError("org-shared write requires org owner/admin")
    if pid is None and record_project_id is not None:
        # org-scope actor must never reach a project-scoped record by id
        raise TenantHiddenError("target not in scope")
