# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sealed-iii.A — mid-run revocation API + pool reset hook.

See docs/superpowers/specs/2026-04-25-sealed-iii-a-revocation-design.md.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class Revocation(BaseModel):
    """A single revocation row from sealed_revocations."""

    model_config = ConfigDict(extra="forbid")

    id: int
    profile_id: str
    secret_key: str
    revoked_at: datetime
    revoked_by: str | None = None
    reason: str
    hard: bool = False
    lifted_at: datetime | None = None
    lifted_by: str | None = None


class CleanupResult(BaseModel):
    """Returned by SealedSecretProvider.cleanup_run."""

    model_config = ConfigDict(extra="forbid")

    env_keys_to_unset: list[str]
    audit_emitted: bool
    had_active_revocations: list[str] = Field(default_factory=list)
    error: str | None = None


class SecretRevokedError(RuntimeError):  # noqa: N818
    """Raised when a profile/key is in the active revocation set.

    Surfaced from enqueue (cascade resolver check) and from sandbox
    start (SealedSecretProvider.env_for).
    """

    def __init__(
        self,
        profile_id: str,
        secret_key: str,
        revocation_id: int,
        reason: str,
    ) -> None:
        super().__init__(
            f"profile {profile_id!r} key {secret_key!r} is revoked "
            f"(id={revocation_id}, reason={reason!r})"
        )
        self.profile_id = profile_id
        self.secret_key = secret_key
        self.revocation_id = revocation_id
        self.reason = reason


class RevocationCheckUnavailableError(RuntimeError):  # noqa: N818
    """Raised when the registry can't be consulted (DB unreachable, etc.).

    Callers MUST fail closed: never proceed without a confirmed check.
    """


from typing import Any  # noqa: E402

from sagewai.sealed.audit import AuditWriter  # noqa: E402


class RevocationConflictError(RuntimeError):  # noqa: N818
    """Raised when a revocation operation conflicts with existing state.

    409 in the REST layer; CLI surfaces with retry hint.
    """


class RevocationRegistry:
    """Read/write surface for the sealed_revocations table.

    Separates CRUD (this class) from cross-table fan-out (hard revoke,
    runs_using_revocation — see Task 4 extensions).
    """

    def __init__(
        self,
        store: Any,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        self._store = store
        self._audit = audit_writer

    async def revoke(
        self,
        *,
        profile_id: str,
        secret_key: str | None,
        reason: str,
        actor_id: str | None = None,
        hard: bool = False,
        current_keys: list[str] | None = None,
    ) -> list[Revocation]:
        """Insert one or more revocation rows.

        secret_key=None: bulk profile revoke. current_keys must be passed
        (caller enumerates from the profile's metadata at revoke time).

        On conflict: raises RevocationConflictError; bulk variants are
        wrapped in a transaction so partial commits are impossible.
        """
        if secret_key is not None:
            return [await self._insert_one(
                profile_id=profile_id,
                secret_key=secret_key,
                reason=reason,
                actor_id=actor_id,
                hard=hard,
            )]

        # Bulk profile revoke: requires explicit current_keys list
        if current_keys is None:
            raise ValueError(
                "secret_key=None (bulk profile revoke) requires current_keys"
            )
        if not current_keys:
            return []

        async with self._store._pool.acquire() as conn:
            async with conn.transaction():
                rows: list[Revocation] = []
                for key in current_keys:
                    try:
                        row = await self._insert_one_using_conn(
                            conn=conn,
                            profile_id=profile_id,
                            secret_key=key,
                            reason=reason,
                            actor_id=actor_id,
                            hard=hard,
                        )
                        rows.append(row)
                    except Exception as exc:
                        # Rollback the entire bulk on any failure
                        raise RevocationConflictError(
                            f"bulk profile revoke for {profile_id!r} failed at "
                            f"key {key!r}: {exc}"
                        ) from exc
                # Emit one audit for the whole bulk
                if self._audit:
                    await self._audit.emit(
                        event_type="secret.revoked",
                        profile_id=profile_id,
                        details={
                            "bulk": True,
                            "keys": [r.secret_key for r in rows],
                            "reason": reason,
                            "hard": hard,
                            "actor_id": actor_id,
                            "revocation_ids": [r.id for r in rows],
                        },
                    )
                return rows

    async def _insert_one(self, **kwargs: Any) -> Revocation:
        async with self._store._pool.acquire() as conn:
            return await self._insert_one_using_conn(conn=conn, **kwargs)

    async def _insert_one_using_conn(
        self,
        *,
        conn: Any,
        profile_id: str,
        secret_key: str,
        reason: str,
        actor_id: str | None,
        hard: bool,
    ) -> Revocation:
        try:
            row = await conn.fetchrow(
                """
                INSERT INTO sealed_revocations
                  (profile_id, secret_key, revoked_by, reason, hard)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING id, profile_id, secret_key, revoked_at,
                          revoked_by, reason, hard, lifted_at, lifted_by
                """,
                profile_id,
                secret_key,
                actor_id,
                reason,
                hard,
            )
        except Exception as exc:
            # asyncpg raises UniqueViolationError; we surface as our error type
            if "duplicate key" in str(exc).lower() or "idx_sealed_revocations_active" in str(exc):
                raise RevocationConflictError(
                    f"active revocation already exists for "
                    f"({profile_id!r}, {secret_key!r})"
                ) from exc
            raise

        revocation = Revocation(**dict(row))

        affected_runs: list[str] = []
        if hard:
            # Fan-out: mark in-flight runs as revoked. Use a fresh pool query
            # rather than the txn `conn`, since the workflow_runs UPDATE may
            # span runs that are concurrently being claimed.
            affected_runs = await self._mark_runs_revoked(
                revocation=revocation, reason=reason,
            )

        if self._audit:
            event_type = "secret.hard_revoked" if hard else "secret.revoked"
            details: dict[str, Any] = {
                "bulk": False,
                "reason": reason,
                "hard": hard,
                "actor_id": actor_id,
                "revocation_id": revocation.id,
            }
            if hard:
                details["affected_runs"] = affected_runs
            await self._audit.emit(
                event_type=event_type,
                profile_id=profile_id,
                secret_key=secret_key,
                details=details,
            )
        return revocation

    async def lift(
        self,
        revocation_id: int,
        *,
        actor_id: str | None = None,
    ) -> Revocation:
        row = await self._store._pool.fetchrow(
            """
            SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                   reason, hard, lifted_at, lifted_by
            FROM sealed_revocations
            WHERE id = $1
            """,
            revocation_id,
        )
        if row is None:
            raise LookupError(f"revocation id {revocation_id} not found")
        if row["lifted_at"] is not None:
            raise RevocationConflictError(
                f"revocation {revocation_id} was already lifted at {row['lifted_at']}"
            )
        updated = await self._store._pool.fetchrow(
            """
            UPDATE sealed_revocations
            SET lifted_at = NOW(), lifted_by = $2
            WHERE id = $1
            RETURNING id, profile_id, secret_key, revoked_at, revoked_by,
                      reason, hard, lifted_at, lifted_by
            """,
            revocation_id,
            actor_id,
        )
        revocation = Revocation(**dict(updated))
        if self._audit:
            await self._audit.emit(
                event_type="secret.lifted",
                profile_id=revocation.profile_id,
                secret_key=revocation.secret_key,
                details={
                    "revocation_id": revocation.id,
                    "lifted_by": actor_id,
                    "original_reason": revocation.reason,
                    "original_revoked_at": revocation.revoked_at.isoformat(),
                },
            )
        return revocation

    async def is_revoked(
        self,
        *,
        profile_id: str,
        secret_key: str,
    ) -> Revocation | None:
        row = await self._store._pool.fetchrow(
            """
            SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                   reason, hard, lifted_at, lifted_by
            FROM sealed_revocations
            WHERE profile_id = $1 AND secret_key = $2 AND lifted_at IS NULL
            """,
            profile_id,
            secret_key,
        )
        return Revocation(**dict(row)) if row else None

    async def find_active_for_keys(
        self,
        *,
        profile_id: str,
        secret_keys: list[str],
    ) -> dict[str, Revocation]:
        if not secret_keys:
            return {}
        rows = await self._store._pool.fetch(
            """
            SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                   reason, hard, lifted_at, lifted_by
            FROM sealed_revocations
            WHERE profile_id = $1 AND secret_key = ANY($2::text[])
              AND lifted_at IS NULL
            """,
            profile_id,
            list(secret_keys),
        )
        return {row["secret_key"]: Revocation(**dict(row)) for row in rows}

    async def list_active(
        self,
        *,
        profile_id: str | None = None,
        limit: int = 200,
    ) -> list[Revocation]:
        if profile_id:
            rows = await self._store._pool.fetch(
                """
                SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                       reason, hard, lifted_at, lifted_by
                FROM sealed_revocations
                WHERE lifted_at IS NULL AND profile_id = $1
                ORDER BY revoked_at DESC
                LIMIT $2
                """,
                profile_id,
                limit,
            )
        else:
            rows = await self._store._pool.fetch(
                """
                SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                       reason, hard, lifted_at, lifted_by
                FROM sealed_revocations
                WHERE lifted_at IS NULL
                ORDER BY revoked_at DESC
                LIMIT $1
                """,
                limit,
            )
        return [Revocation(**dict(r)) for r in rows]

    async def list_all(
        self,
        *,
        profile_id: str | None = None,
        include_lifted: bool = False,
        limit: int = 200,
    ) -> list[Revocation]:
        # Compose WHERE clause cleanly
        where_clauses = []
        args: list[Any] = []
        if profile_id:
            args.append(profile_id)
            where_clauses.append(f"profile_id = ${len(args)}")
        if not include_lifted:
            where_clauses.append("lifted_at IS NULL")
        where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""
        args.append(limit)
        rows = await self._store._pool.fetch(
            f"""
            SELECT id, profile_id, secret_key, revoked_at, revoked_by,
                   reason, hard, lifted_at, lifted_by
            FROM sealed_revocations
            {where_sql}
            ORDER BY revoked_at DESC
            LIMIT ${len(args)}
            """,
            *args,
        )
        return [Revocation(**dict(r)) for r in rows]

    async def runs_using_revocation(
        self,
        revocation_id: int,
    ) -> list[str]:
        """Return run_ids of in-flight runs that injected the revocation's key."""
        rev = await self._store._pool.fetchrow(
            """
            SELECT profile_id, secret_key
            FROM sealed_revocations WHERE id = $1
            """,
            revocation_id,
        )
        if rev is None:
            raise LookupError(f"revocation id {revocation_id} not found")
        rows = await self._store._pool.fetch(
            """
            SELECT run_id FROM workflow_runs
            WHERE status = 'running'
              AND security_profile_ref = $1
              AND $2 = ANY(effective_secret_keys)
            """,
            rev["profile_id"],
            rev["secret_key"],
        )
        return [r["run_id"] for r in rows]

    async def _mark_runs_revoked(
        self,
        *,
        revocation: Revocation,
        reason: str,
    ) -> list[str]:
        """Set workflow_runs.revoked_at + revoke_reason on affected runs."""
        rows = await self._store._pool.fetch(
            """
            UPDATE workflow_runs
            SET revoked_at = NOW(), revoke_reason = $3
            WHERE status = 'running'
              AND security_profile_ref = $1
              AND $2 = ANY(effective_secret_keys)
              AND revoked_at IS NULL
            RETURNING run_id
            """,
            revocation.profile_id,
            revocation.secret_key,
            reason,
        )
        return [r["run_id"] for r in rows]
