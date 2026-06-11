# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tenant-scoped API-token store (machine/CI auth for multi-tenant mode).

A dual-dialect (SQLite + Postgres) async store over the ``api_token`` table
(:class:`sagewai.db.models.ApiTokenModel`, Alembic migration 017). Mirrors the
:class:`~sagewai.admin.admin_resource_store.AdminResourceStore` pattern:
engine-injectable, SQLAlchemy Core, schema created on SQLite via ``init()``; on
Postgres the schema comes from Alembic.

Scope model (W0 RFC §5/§6). A token carries ``read/write/admin`` scopes AND is
bound to a scope:

* ``project_id = P`` — acts only in project P;
* ``project_id = NULL`` — an **ORG-SHARED** token (org-shared resources only,
  **NOT** an all-projects wildcard).

The store stamps ``org_id``/``subject_user_id``/``project_id`` from the acting
``ctx`` — **never** from the request body — and enforces who may mint what:

* a **project-scoped** token (``project_id is not None``) must be minted by a
  ctx bound to **that same project** (``ctx.project_id == project_id``);
* an **org-shared** token (``project_id is None``) requires an **org owner/admin**
  ctx.

Only the SHA-256 ``token_hash`` is stored; the plaintext (``swt_`` + a urlsafe
secret) is returned **once** at creation and never again. Reads
(:meth:`list_for`) use the standard read scope (own project + inherited
org-shared) and are **redacted** — they never carry ``token_hash`` or the
plaintext, only a masked suffix plus metadata. :meth:`find_by_hash` is the
pre-context auth lookup and is therefore **not** ctx-scoped.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.admin import scoping
from sagewai.admin.tenancy import ALL_SCOPES, RequestContext
from sagewai.db import factory
from sagewai.db.models import ApiTokenModel, Base

_tbl = ApiTokenModel.__table__

_ORG_ADMINS = frozenset({"org:owner", "org:admin"})
_TOKEN_PREFIX = "swt_"


class TokenScopeError(PermissionError):
    """The acting ctx may not mint a token in the requested scope.

    A project ctx minting a token for a different project, or a non-org-admin
    minting an org-shared (``project_id is None``) token. The route maps this to
    403 (RFC §5: no token is a data-scope wildcard, and a project member cannot
    mint outside its own project)."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: Any) -> datetime | None:
    """Normalise a stored timestamp to a timezone-aware datetime (or None).

    SQLite round-trips DateTime as naive strings; Postgres returns aware
    datetimes. Treat naive values as UTC so expiry comparisons are consistent.
    """
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def hash_token(raw: str) -> str:
    """SHA-256 hex of a raw token — the at-rest form and the auth-lookup key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _redact(row: Any) -> dict[str, Any]:
    """A token record safe to return over the wire — never the hash or plaintext.

    Carries a masked ``suffix`` (last 4 chars of the hash) so an operator can
    recognise a token in a list without ever seeing the secret or its full hash.
    """
    h = row["token_hash"]
    return {
        "id": row["id"],
        "project_id": row["project_id"],
        "subject_user_id": row["subject_user_id"],
        "scopes": _split_scopes(row["scopes"]),
        "name": row["name"],
        "suffix": h[-4:] if h else None,
        "expires_at": row["expires_at"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
        "created_at": row["created_at"],
    }


def _join_scopes(scopes: set[str]) -> str:
    return ",".join(sorted(scopes))


def _split_scopes(stored: str | None) -> list[str]:
    if not stored:
        return []
    return [s for s in stored.split(",") if s]


class ApiTokenStore:
    """Async store for tenant-scoped API tokens (``api_token`` table).

    Constructor forms mirror the other tenant stores:

    * ``ApiTokenStore()`` — uses the process-wide engine from
      :func:`sagewai.db.factory.get_engine`.
    * ``ApiTokenStore(engine=my_engine)`` — injected engine (tests / DI).
    """

    def __init__(self, engine: AsyncEngine | None = None) -> None:
        self._engine = engine or factory.get_engine()

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------ writes

    async def create_for(
        self,
        ctx: RequestContext,
        *,
        name: str | None,
        scopes: set[str],
        project_id: str | None,
        expires_at: datetime | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Mint a token in ``ctx``'s scope; return ``(record, plaintext)``.

        ``org_id``/``subject_user_id`` are stamped from ``ctx`` (never the body).
        ``project_id`` must equal ``ctx.project_id`` for a project-scoped token,
        or be ``None`` (org-shared) only when ``ctx`` is an org owner/admin.
        ``scopes`` must be a subset of ``{read, write, admin}``. The plaintext
        (``swt_`` + secret) is returned ONCE; only its hash is stored.

        The returned ``record`` is the internal dict (it includes ``token_hash``
        for callers that need it, e.g. the route that strips it before responding
        and tests). Use :func:`_redact` / :meth:`list_for` for any wire response.
        """
        scoping.require_ctx(ctx)
        bad = set(scopes) - ALL_SCOPES
        if bad:
            raise ValueError(f"unknown token scopes: {sorted(bad)}")
        if not scopes:
            raise ValueError("a token must carry at least one scope")

        if project_id is None:
            # Org-shared token — org owner/admin only (RFC §5).
            if not (ctx.roles & _ORG_ADMINS):
                raise TokenScopeError("org-shared token requires org owner/admin")
        else:
            # Project-scoped token — must match the acting project context.
            if ctx.project_id != project_id:
                raise TokenScopeError(
                    "a project token must be minted in that project's context"
                )

        raw = _TOKEN_PREFIX + secrets.token_urlsafe(32)
        token_id = _new_id()
        now = _now()
        async with self._engine.begin() as conn:
            await conn.execute(
                _tbl.insert().values(
                    id=token_id,
                    org_id=ctx.org_id,
                    project_id=project_id,
                    subject_user_id=ctx.actor.id,
                    token_hash=hash_token(raw),
                    scopes=_join_scopes(set(scopes)),
                    name=name,
                    expires_at=expires_at,
                    created_at=now,
                )
            )
        record = {
            "id": token_id,
            "org_id": ctx.org_id,
            "project_id": project_id,
            "subject_user_id": ctx.actor.id,
            "token_hash": hash_token(raw),
            "scopes": sorted(set(scopes)),
            "name": name,
            "expires_at": expires_at,
            "last_used_at": None,
            "revoked_at": None,
            "created_at": now,
        }
        return record, raw

    async def revoke_for(self, ctx: RequestContext, token_id: str) -> bool:
        """Revoke ``token_id`` in ``ctx``'s write scope. True iff a live row went.

        The write-scope filter excludes inherited org-shared rows from a project
        ctx and rows owned by another project, so a cross-scope revoke matches
        zero rows and returns False (no leak, no error). Re-revoking an already
        revoked token returns False.
        """
        scoping.require_ctx(ctx)
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(_tbl)
                .where(
                    _tbl.c.id == token_id,
                    _tbl.c.revoked_at.is_(None),
                    scoping.write_scope_filter(_tbl, ctx),
                )
                .values(revoked_at=_now())
            )
        return result.rowcount == 1

    # ------------------------------------------------------------------- reads

    async def list_for(self, ctx: RequestContext) -> list[dict[str, Any]]:
        """List tokens visible to ``ctx`` (own project + inherited org-shared).

        REDACTED — never returns ``token_hash`` or the plaintext; each row is a
        masked record (:func:`_redact`) with scopes/name/expiry/last-used/revoked
        and a short suffix. Ordered newest-first.
        """
        scoping.require_ctx(ctx)
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        scoping.apply_scope(select(_tbl), _tbl, ctx).order_by(
                            _tbl.c.created_at.desc()
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [_redact(r) for r in rows]

    async def find_by_hash(self, token_hash: str) -> dict[str, Any] | None:
        """Resolve a token by its hash — the pre-context auth lookup.

        **Not** ctx-scoped (this runs before a context exists). Returns the
        fields the middleware needs to build a context and cap the token's scope:
        ``id``, ``org_id``, ``project_id``, ``subject_user_id``, ``scopes``,
        ``expires_at``, ``revoked_at``. ``None`` when no token has that hash.
        Revocation/expiry are reported (not filtered) so the caller decides the
        401; this keeps the lookup a pure existence query.
        """
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_tbl).where(_tbl.c.token_hash == token_hash)))
                .mappings()
                .first()
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "org_id": row["org_id"],
            "project_id": row["project_id"],
            "subject_user_id": row["subject_user_id"],
            "scopes": _split_scopes(row["scopes"]),
            "name": row["name"],
            "expires_at": _coerce_dt(row["expires_at"]),
            "revoked_at": _coerce_dt(row["revoked_at"]),
        }

    async def touch_last_used(self, token_id: str) -> None:
        """Best-effort ``last_used_at`` stamp (auth-path telemetry; never raises)."""
        try:
            async with self._engine.begin() as conn:
                await conn.execute(
                    update(_tbl).where(_tbl.c.id == token_id).values(last_used_at=_now())
                )
        except Exception:
            # last_used is telemetry — a failure must never break authentication.
            pass


__all__ = ["ApiTokenStore", "TokenScopeError", "hash_token"]
