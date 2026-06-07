# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The tenant data-scope primitive (W2 of the multi-tenancy/RBAC roadmap).

One rule, applied everywhere (W0 RFC §3) — there is no wildcard data-scope:

    ctx.project = P    -> org_id == ctx.org AND (project_id == P OR project_id IS NULL)
    ctx.project = None -> org_id == ctx.org AND project_id IS NULL   (org-shared only)

**Reads / executes** use :func:`apply_scope` / :func:`row_in_scope` (own +
inherited org-shared). **Mutations** (update / delete / select-for-update) use
the separate :func:`apply_write_scope` / :func:`row_writable`, which **exclude
inherited org-shared rows from a project context** — a project member may *use*
org-shared resources but never *mutate* them (RFC §3). Using the read filter for
a mutation would let a project actor delete/update an org-shared row by id; the
distinct write scope makes that impossible at the SQL layer (belt-and-suspenders
with the RBAC ``require()`` check).

Writes stamp ``(org_id, project_id)`` from the context via :func:`scope_values`
(never from the request body). In multi-tenant mode there is no unscoped path —
:func:`require_ctx` guards call sites that forgot to pass a context.

Tables used with this primitive must expose ``org_id`` and (nullable)
``project_id`` columns.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Select, Table, and_, or_

from sagewai.admin.tenancy import RequestContext


class UnscopedQueryError(RuntimeError):
    """A tenant-scoped operation was attempted without a RequestContext."""


def require_ctx(ctx: RequestContext | None) -> RequestContext:
    """Return ``ctx``, or raise if it is missing (the no-unscoped-path guard)."""
    if ctx is None:
        raise UnscopedQueryError("tenant-scoped operation requires a RequestContext")
    return ctx


def scope_filter(table: Table, ctx: RequestContext):
    """The §3 WHERE clause for ``ctx`` over ``table`` (needs org_id + project_id)."""
    org_c = table.c.org_id
    proj_c = table.c.project_id
    if ctx.project_id is None:
        return and_(org_c == ctx.org_id, proj_c.is_(None))
    return and_(org_c == ctx.org_id, or_(proj_c == ctx.project_id, proj_c.is_(None)))


def apply_scope(stmt: Select, table: Table, ctx: RequestContext) -> Select:
    """Append the read/execute scope filter to a SELECT (own + org-shared)."""
    return stmt.where(scope_filter(table, ctx))


def write_scope_filter(table: Table, ctx: RequestContext):
    """The WHERE clause for MUTATIONS — never includes inherited org-shared rows.

    A project context may mutate only its **own** rows (``project_id == P``); an
    org context (``project_id is None``) mutates org-shared rows. This is
    deliberately narrower than :func:`scope_filter` so update/delete/SELECT FOR
    UPDATE can't reach an org-shared row a project actor merely inherits for reads.
    """
    org_c = table.c.org_id
    proj_c = table.c.project_id
    if ctx.project_id is None:
        return and_(org_c == ctx.org_id, proj_c.is_(None))
    return and_(org_c == ctx.org_id, proj_c == ctx.project_id)


def apply_write_scope(stmt: Select, table: Table, ctx: RequestContext) -> Select:
    """Append the mutation scope filter (own rows only — no inherited shared)."""
    return stmt.where(write_scope_filter(table, ctx))


def scope_values(ctx: RequestContext) -> dict[str, Any]:
    """The (org_id, project_id) to stamp on an INSERT — taken from ctx, not the body.

    In a project context new rows are project-scoped; in org context they are
    org-shared (project_id IS NULL).
    """
    return {"org_id": ctx.org_id, "project_id": ctx.project_id}


def row_in_scope(row: Any, ctx: RequestContext) -> bool:
    """True if a fetched row (a mapping with org_id/project_id) is visible to ctx.

    Mirrors :func:`scope_filter` for post-fetch checks (e.g. a get-by-id that
    must 404 rather than reveal another tenant's row).
    """
    if _get(row, "org_id") != ctx.org_id:
        return False
    pid = _get(row, "project_id")
    if ctx.project_id is None:
        return pid is None
    return pid == ctx.project_id or pid is None


def row_writable(row: Any, ctx: RequestContext) -> bool:
    """True if ``row`` may be MUTATED by ctx — own rows only (no inherited shared).

    Mirrors :func:`write_scope_filter` for post-fetch update/delete guards: a
    project context can mutate only ``project_id == P``; an org context only
    org-shared rows.
    """
    if _get(row, "org_id") != ctx.org_id:
        return False
    pid = _get(row, "project_id")
    if ctx.project_id is None:
        return pid is None
    return pid == ctx.project_id


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, TypeError):
        return getattr(row, key, None)
