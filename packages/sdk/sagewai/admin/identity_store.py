# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Multi-tenant identity & tenancy store (W1 of the multi-tenancy/RBAC roadmap).

A dual-dialect (SQLite + Postgres) async store over the tenancy tables defined
in :mod:`sagewai.db.models` (``org``, ``user_account``, ``project``,
``membership``, ``invitation``, ``user_session``). Mirrors the
``PostgresAgentStore`` pattern: engine-injectable, SQLAlchemy Core, schema
created on SQLite via ``init()``; on Postgres the schema comes from Alembic
migration 009.

Scope model (see the W0 RFC): one org (shared umbrella) -> many isolated
projects. Roles are namespaced (``org:*`` / ``project:*``). The store resolves
an authenticated user + their memberships into a :class:`RequestContext`, which
later workstreams thread into every tenant-scoped operation. A project context
is only built for a user who is a member of that project (or an org admin);
otherwise :class:`TenantAccessError` is raised (a 404 at the HTTP layer — no
existence leak).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

# Reuse the foundation's canonical hashing (single source of truth).
from sagewai.admin.state_file import (
    _hash_password,
    _hash_token,
    _make_token,
    _verify_password,
)
from sagewai.admin.tenancy import (
    ALL_SCOPES,
    ORG_ROLES,
    PROJECT_ROLES,
    RequestContext,
    UserRef,
    is_org_role,
    is_project_role,
)
from sagewai.db import factory
from sagewai.db.engine import create_engine
from sagewai.db.models import (
    AccountModel,
    Base,
    MembershipModel,
    OrgInvitationModel,
    OrgModel,
    ProjectModel,
    UserSessionModel,
)

_org = OrgModel.__table__
_user = AccountModel.__table__
_project = ProjectModel.__table__
_membership = MembershipModel.__table__
_invite = OrgInvitationModel.__table__
_session = UserSessionModel.__table__

_DEFAULT_INVITE_TTL = 7 * 24 * 3600
_DEFAULT_SESSION_TTL = 7 * 24 * 3600


class TenantAccessError(Exception):
    """Raised when a user is asked to act in a project they cannot access.

    Maps to HTTP 404 (existence-hiding) at the route layer, not 403.
    """


class InvitationError(Exception):
    """Raised when an invitation token is invalid, expired, or already used."""


def _new_id() -> str:
    return uuid.uuid4().hex


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _project_record(row: Any) -> dict[str, Any]:
    """A project dict safe to forward to callers — wrapped key material removed.

    ``data_key_ref`` holds the ``fernet:``-wrapped per-project data key; per the
    W0 secret-isolation gate, responses must never carry ``fernet:`` ciphertext,
    so it is omitted from the generic record. Read the key only through
    :meth:`IdentityStore.get_project_data_key` (see :mod:`sagewai.admin.tenant_keys`).
    """
    record = dict(row)
    record.pop("data_key_ref", None)
    return record


class IdentityStore:
    """Async store for orgs, users, projects, memberships, invitations, sessions."""

    def __init__(
        self,
        engine: AsyncEngine | None = None,
        *,
        database_url: str | None = None,
    ) -> None:
        if engine is not None:
            self._engine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()

    async def init(self) -> None:
        """Create the schema on SQLite (no-op on Postgres — Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------ org
    async def bootstrap_org(
        self,
        name: str,
        slug: str,
        *,
        contact_email: str | None = None,
        tz: str = "UTC",
        org_id: str | None = None,
        master_key_ref: str | None = None,
    ) -> dict[str, Any]:
        oid = org_id or _new_id()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_org).values(
                    id=oid,
                    name=name,
                    slug=slug,
                    contact_email=contact_email,
                    timezone=tz,
                    settings={},
                    master_key_ref=master_key_ref,
                )
            )
        org = await self.get_org(oid)
        assert org is not None
        return org

    async def get_org(self, org_id: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(_org).where(_org.c.id == org_id))).mappings().first()
        return dict(row) if row else None

    async def get_org_by_slug(self, slug: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (await conn.execute(select(_org).where(_org.c.slug == slug))).mappings().first()
        return dict(row) if row else None

    async def list_orgs(self) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = (await conn.execute(select(_org).order_by(_org.c.slug))).mappings().all()
        return [dict(r) for r in rows]

    async def update_org(self, org_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
        allowed = {"name", "contact_email", "timezone", "settings"}
        values = {k: v for k, v in patch.items() if k in allowed}
        if values:
            async with self._engine.begin() as conn:
                result = await conn.execute(
                    update(_org).where(_org.c.id == org_id).values(**values)
                )
            if result.rowcount == 0:
                return None
        return await self.get_org(org_id)

    # ----------------------------------------------------------------- users
    async def _insert_account(
        self,
        org_id: str,
        email: str,
        *,
        password: str | None,
        name: str | None,
        user_id: str | None = None,
    ) -> str:
        uid = user_id or _new_id()
        pw_hash, pw_salt = (None, None)
        if password is not None:
            pw_hash, pw_salt = _hash_password(password)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_user).values(
                    id=uid,
                    org_id=org_id,
                    email=email,
                    name=name,
                    password_hash=pw_hash,
                    password_salt=pw_salt,
                    status="active",
                )
            )
        return uid

    async def create_user(
        self,
        org_id: str,
        email: str,
        *,
        password: str | None = None,
        name: str | None = None,
        role: str = "org:member",
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Create a user + an org-level membership with ``role`` (an ``org:*`` role)."""
        if not is_org_role(role):
            raise ValueError(f"create_user requires an org-level role, got {role!r}")
        uid = await self._insert_account(
            org_id, email, password=password, name=name, user_id=user_id
        )
        await self.add_membership(org_id, uid, role)
        user = await self.get_user(org_id, uid)
        assert user is not None
        return user

    async def get_user(self, org_id: str, user_id: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_user).where(_user.c.org_id == org_id, _user.c.id == user_id)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def get_user_by_email(self, org_id: str, email: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_user).where(_user.c.org_id == org_id, _user.c.email == email)
                    )
                )
                .mappings()
                .first()
            )
        return dict(row) if row else None

    async def update_user_profile(
        self, org_id: str, user_id: str, *, name: str | None
    ) -> dict[str, Any] | None:
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(_user)
                .where(_user.c.org_id == org_id, _user.c.id == user_id)
                .values(name=name)
            )
        if result.rowcount == 0:
            return None
        return await self.get_user(org_id, user_id)

    async def set_password(self, org_id: str, user_id: str, password: str) -> None:
        pw_hash, pw_salt = _hash_password(password)
        async with self._engine.begin() as conn:
            await conn.execute(
                update(_user)
                .where(_user.c.org_id == org_id, _user.c.id == user_id)
                .values(password_hash=pw_hash, password_salt=pw_salt)
            )

    async def verify_credentials(
        self, org_id: str, email: str, password: str
    ) -> dict[str, Any] | None:
        """Return the user dict if email + password match, else None."""
        user = await self.get_user_by_email(org_id, email)
        if user is None or not user.get("password_hash") or not user.get("password_salt"):
            return None
        if not _verify_password(password, user["password_hash"], user["password_salt"]):
            return None
        async with self._engine.begin() as conn:
            await conn.execute(
                update(_user).where(_user.c.id == user["id"]).values(last_login_at=_now())
            )
        return user

    # -------------------------------------------------------------- projects
    async def create_project(
        self,
        org_id: str,
        slug: str,
        name: str,
        *,
        project_id: str | None = None,
        environment: str = "production",
        status: str = "active",
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        pid = project_id or _new_id()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_project).values(
                    id=pid,
                    org_id=org_id,
                    slug=slug,
                    name=name,
                    environment=environment,
                    status=status,
                    settings=settings or {},
                )
            )
        proj = await self.get_project(org_id, pid)
        assert proj is not None
        return proj

    async def get_project(self, org_id: str, project_id: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_project).where(
                            _project.c.org_id == org_id, _project.c.id == project_id
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _project_record(row) if row else None

    async def get_project_by_slug(self, org_id: str, slug: str) -> dict[str, Any] | None:
        async with self._engine.connect() as conn:
            row = (
                (
                    await conn.execute(
                        select(_project).where(
                            _project.c.org_id == org_id, _project.c.slug == slug
                        )
                    )
                )
                .mappings()
                .first()
            )
        return _project_record(row) if row else None

    async def update_project(
        self,
        org_id: str,
        project_id: str,
        *,
        name: str | None = None,
        environment: str | None = None,
        status: str | None = None,
        settings_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        existing = await self.get_project(org_id, project_id)
        if existing is None:
            return None
        values: dict[str, Any] = {}
        if name is not None:
            values["name"] = name
        if environment is not None:
            values["environment"] = environment
        if status is not None:
            values["status"] = status
        if settings_patch:
            merged = dict(existing.get("settings") or {})
            merged.update(settings_patch)
            values["settings"] = merged
        if values:
            values["updated_at"] = _now()
            async with self._engine.begin() as conn:
                await conn.execute(
                    update(_project)
                    .where(_project.c.org_id == org_id, _project.c.id == project_id)
                    .values(**values)
                )
        return await self.get_project(org_id, project_id)

    async def get_project_data_key(self, org_id: str, project_id: str) -> str | None:
        """Return the wrapped per-project data key (``project.data_key_ref``).

        ``None`` when the project has no data key yet or does not exist. The
        value is the data key encrypted under the org master key — never the raw
        key (see :mod:`sagewai.admin.tenant_keys`).
        """
        async with self._engine.connect() as conn:
            row = (
                await conn.execute(
                    select(_project.c.data_key_ref).where(
                        _project.c.org_id == org_id, _project.c.id == project_id
                    )
                )
            ).first()
        return row[0] if row else None

    async def set_project_data_key(self, org_id: str, project_id: str, wrapped: str) -> None:
        """Unconditionally persist the wrapped per-project data key (used by rotation).

        Raises :class:`TenantAccessError` if the project is unknown. For
        first-use minting use :meth:`set_project_data_key_if_absent`, which is
        race-safe.
        """
        async with self._engine.begin() as conn:
            result = await conn.execute(
                update(_project)
                .where(_project.c.org_id == org_id, _project.c.id == project_id)
                .values(data_key_ref=wrapped)
            )
        if result.rowcount == 0:
            raise TenantAccessError(f"unknown project {project_id!r}")

    async def set_project_data_key_if_absent(
        self, org_id: str, project_id: str, wrapped: str
    ) -> str:
        """Atomically set the wrapped data key only if absent; return the effective key.

        Race-safe first-use minting: the conditional ``UPDATE ... WHERE
        data_key_ref IS NULL`` means at most one of any racing callers stores its
        key; the value is then read back inside the same transaction, so every
        caller (winner and losers) converges on the one stored data key. Raises
        :class:`TenantAccessError` if the project is unknown.
        """
        async with self._engine.begin() as conn:
            await conn.execute(
                update(_project)
                .where(
                    _project.c.org_id == org_id,
                    _project.c.id == project_id,
                    _project.c.data_key_ref.is_(None),
                )
                .values(data_key_ref=wrapped)
            )
            row = (
                await conn.execute(
                    select(_project.c.data_key_ref).where(
                        _project.c.org_id == org_id, _project.c.id == project_id
                    )
                )
            ).first()
        if row is None:
            raise TenantAccessError(f"unknown project {project_id!r}")
        return row[0]

    async def list_projects(self, org_id: str) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(_project)
                        .where(_project.c.org_id == org_id)
                        .order_by(_project.c.slug)
                    )
                )
                .mappings()
                .all()
            )
        return [_project_record(r) for r in rows]

    # ------------------------------------------------------------ memberships
    async def add_membership(
        self,
        org_id: str,
        user_id: str,
        role: str,
        *,
        project_id: str | None = None,
        membership_id: str | None = None,
    ) -> dict[str, Any]:
        """Add an org-level (project_id=None, org:* role) or project-level membership."""
        if project_id is None and not is_org_role(role):
            raise ValueError(f"org-level membership needs an org:* role, got {role!r}")
        if project_id is not None and not is_project_role(role):
            raise ValueError(f"project-level membership needs a project:* role, got {role!r}")
        mid = membership_id or _new_id()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_membership).values(
                    id=mid,
                    user_id=user_id,
                    org_id=org_id,
                    project_id=project_id,
                    role=role,
                )
            )
        return {
            "id": mid,
            "user_id": user_id,
            "org_id": org_id,
            "project_id": project_id,
            "role": role,
        }

    async def list_memberships(self, org_id: str, user_id: str) -> list[dict[str, Any]]:
        async with self._engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        select(_membership).where(
                            _membership.c.org_id == org_id, _membership.c.user_id == user_id
                        )
                    )
                )
                .mappings()
                .all()
            )
        return [dict(r) for r in rows]

    async def resolve_roles(self, org_id: str, user_id: str) -> frozenset[str]:
        """All roles a user holds across the org and every project."""
        rows = await self.list_memberships(org_id, user_id)
        return frozenset(r["role"] for r in rows)

    # ------------------------------------------------------------ invitations
    async def create_invitation(
        self,
        org_id: str,
        email: str,
        role: str,
        invited_by: str,
        *,
        project_id: str | None = None,
        ttl_seconds: int = _DEFAULT_INVITE_TTL,
    ) -> tuple[dict[str, Any], str]:
        """Create an invitation; returns (record, raw_token). Only the hash is stored."""
        if project_id is None and not is_org_role(role):
            raise ValueError(f"org invitation needs an org:* role, got {role!r}")
        if project_id is not None and not is_project_role(role):
            raise ValueError(f"project invitation needs a project:* role, got {role!r}")
        iid = _new_id()
        raw = _make_token()
        expires_at = _now() + timedelta(seconds=ttl_seconds)
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_invite).values(
                    id=iid,
                    org_id=org_id,
                    project_id=project_id,
                    email=email,
                    role=role,
                    token_hash=_hash_token(raw),
                    expires_at=expires_at,
                    invited_by=invited_by,
                )
            )
        record = {
            "id": iid,
            "org_id": org_id,
            "project_id": project_id,
            "email": email,
            "role": role,
            "expires_at": expires_at,
            "invited_by": invited_by,
        }
        return record, raw

    async def accept_invitation(
        self,
        raw_token: str,
        *,
        password: str | None = None,
        name: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Accept an invitation: create the user + the invited membership.

        A project invitation yields a project-only user (project membership,
        no org-level membership). An org invitation yields an org-level member.
        """
        token_hash = _hash_token(raw_token)
        async with self._engine.connect() as conn:
            inv = (
                (await conn.execute(select(_invite).where(_invite.c.token_hash == token_hash)))
                .mappings()
                .first()
            )
        if inv is None:
            raise InvitationError("unknown invitation token")
        if inv["accepted_at"] is not None:
            raise InvitationError("invitation already accepted")
        if inv["expires_at"] is not None and _coerce_dt(inv["expires_at"]) < _now():
            raise InvitationError("invitation expired")

        uid = await self._insert_account(
            inv["org_id"], inv["email"], password=password, name=name, user_id=user_id
        )
        await self.add_membership(inv["org_id"], uid, inv["role"], project_id=inv["project_id"])
        async with self._engine.begin() as conn:
            await conn.execute(
                update(_invite).where(_invite.c.id == inv["id"]).values(accepted_at=_now())
            )
        user = await self.get_user(inv["org_id"], uid)
        assert user is not None
        return user

    # --------------------------------------------------------------- sessions
    async def issue_session(
        self, org_id: str, user_id: str, *, ttl_seconds: int = _DEFAULT_SESSION_TTL
    ) -> str:
        """Issue a per-user session; returns the raw token (hash stored at rest)."""
        raw = _make_token()
        async with self._engine.begin() as conn:
            await conn.execute(
                insert(_session).values(
                    id=_new_id(),
                    org_id=org_id,
                    user_id=user_id,
                    token_hash=_hash_token(raw),
                    expires_at=_now() + timedelta(seconds=ttl_seconds),
                )
            )
        return raw

    async def resolve_session(self, raw_token: str) -> dict[str, Any] | None:
        """Return {org_id, user_id} for a valid, unexpired session, else None."""
        token_hash = _hash_token(raw_token)
        async with self._engine.connect() as conn:
            row = (
                (await conn.execute(select(_session).where(_session.c.token_hash == token_hash)))
                .mappings()
                .first()
            )
        if row is None:
            return None
        if _coerce_dt(row["expires_at"]) < _now():
            return None
        async with self._engine.begin() as conn:
            await conn.execute(
                update(_session).where(_session.c.id == row["id"]).values(last_used_at=_now())
            )
        return {"org_id": row["org_id"], "user_id": row["user_id"]}

    async def revoke_session(self, raw_token: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(
                delete(_session).where(_session.c.token_hash == _hash_token(raw_token))
            )

    # --------------------------------------------------------------- context
    async def build_context(
        self,
        org_id: str,
        user_id: str,
        *,
        project_id: str | None = None,
        scopes: frozenset[str] = ALL_SCOPES,
        request_id: str = "",
    ) -> RequestContext:
        """Build a RequestContext for an authenticated user.

        Project resolution (W0 RFC §4):
        - explicit ``project_id`` -> the user must be a member of that project
          (or an org owner/admin) and the project must exist, else
          TenantAccessError (a 404 at the HTTP layer);
        - no ``project_id`` and the user holds an org-level role -> org scope
          (``project_id=None``);
        - no ``project_id`` and the user is project-only -> NO bare org scope:
          default to their single project, or raise TenantAccessError when
          selection is required (zero or multiple project memberships).
        """
        user = await self.get_user(org_id, user_id)
        if user is None:
            raise TenantAccessError("unknown user")
        memberships = await self.list_memberships(org_id, user_id)
        org_roles = {m["role"] for m in memberships if m["project_id"] is None}
        project_ids = [m["project_id"] for m in memberships if m["project_id"] is not None]

        if project_id is None and not org_roles:
            # Project-only actor: never falls back to org scope (RFC §4).
            if len(project_ids) == 1:
                project_id = project_ids[0]
            elif not project_ids:
                raise TenantAccessError("user has no memberships")
            else:
                raise TenantAccessError("project selection required")

        if project_id is not None:
            is_org_admin = bool(org_roles & {"org:owner", "org:admin"})
            if await self.get_project(org_id, project_id) is None:
                raise TenantAccessError("unknown project")
            if not is_org_admin and project_id not in project_ids:
                raise TenantAccessError("not a member of project")

        roles = set(org_roles)
        if project_id is not None:
            roles |= {m["role"] for m in memberships if m["project_id"] == project_id}

        label = user.get("email") or user_id
        return RequestContext(
            actor=UserRef(id=user_id, label=label),
            org_id=org_id,
            project_id=project_id,
            roles=frozenset(roles),
            scopes=frozenset(scopes),
            request_id=request_id,
            tenancy_mode="multi",
        )


def _coerce_dt(value: Any) -> datetime:
    """Normalise a stored timestamp to a timezone-aware datetime.

    SQLite round-trips DateTime as naive strings; Postgres returns aware
    datetimes. Treat naive values as UTC so comparisons are consistent.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# Re-exported for callers that only need the role sets.
__all__ = [
    "IdentityStore",
    "TenantAccessError",
    "InvitationError",
    "ORG_ROLES",
    "PROJECT_ROLES",
]
