# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Fleet worker registry — registration, approval, and enrollment key management.

Provides the ``FleetRegistry`` ABC and two implementations:

- ``InMemoryFleetRegistry``: for testing and development.
- ``PostgresFleetRegistry``: SQLAlchemy-backed (persistent on SQLite or Postgres),
  storing data in the ``workers`` and ``enrollment_keys`` tables (migrations 001/018).

Workers register with optional enrollment keys. A valid enrollment key
auto-approves the worker; without one the worker enters PENDING state.

Enrollment keys are single-use secrets: the raw key is returned once at
creation time; only a SHA-256 hash is persisted.

Usage::

    from sagewai.fleet.registry import InMemoryFleetRegistry

    registry = InMemoryFleetRegistry()
    key_record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="GPU cluster key", created_by="admin-1",
    )
    worker = await registry.register_worker(
        name="worker-gpu-01", org_id="org-1",
        capabilities=WorkerCapabilities(pool="gpu"),
        enrollment_key=raw_key,
    )
    assert worker.approval_status == WorkerApprovalStatus.APPROVED
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sagewai.fleet.models import (
    EnrollmentKey,
    WorkerApprovalStatus,
    WorkerCapabilities,
    WorkerRecord,
)
from sagewai.fleet.normalizer import ModelNormalizer

logger = logging.getLogger(__name__)


def _hash_key(raw: str) -> str:
    """Compute a SHA-256 hex digest for an enrollment key."""
    return hashlib.sha256(raw.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# Sentinel for list_workers(project_id=...): distinguishes "don't filter by project"
# (the default) from "filter by None" (org-global only). `== None` would otherwise be
# indistinguishable from the unset default.
_UNSET: object = object()


class FleetRegistry(ABC):
    """Abstract base for fleet worker and enrollment key management."""

    # --- Worker Registration ---

    @abstractmethod
    async def register_worker(
        self,
        name: str,
        org_id: str,
        capabilities: WorkerCapabilities,
        *,
        project_id: str | None = None,
        enrollment_key: str | None = None,
        secret_hash: str | None = None,
    ) -> WorkerRecord:
        """Register a new worker.

        If *enrollment_key* is provided and valid, the worker is
        auto-approved.  Otherwise it enters ``PENDING`` state.
        """

    @abstractmethod
    async def get_worker(self, worker_id: str) -> WorkerRecord | None:
        """Return a worker by ID, or ``None`` if not found."""

    @abstractmethod
    async def list_workers(
        self,
        org_id: str,
        status: WorkerApprovalStatus | None = None,
        pool: str | None = None,
        limit: int = 100,
        project_id: str | None | object = _UNSET,
    ) -> list[WorkerRecord]:
        """List workers for an organisation, optionally filtered.

        ``project_id`` (when provided) filters by exact project scope — ``None``
        for org-global workers, a slug for that project — matching the claim
        predicate. Omit it to list across all projects.
        """

    @abstractmethod
    async def approve_worker(
        self, worker_id: str, approved_by: str
    ) -> WorkerRecord:
        """Approve a pending worker."""

    @abstractmethod
    async def reject_worker(self, worker_id: str) -> WorkerRecord:
        """Reject a pending worker."""

    @abstractmethod
    async def revoke_worker(self, worker_id: str) -> WorkerRecord:
        """Revoke an approved worker."""

    @abstractmethod
    async def heartbeat(
        self, worker_id: str, *, pool_stats: dict | None = None,
    ) -> None:
        """Update the worker's ``last_heartbeat`` timestamp.
        Optionally cache the latest pool_stats snapshot for admin UI display.
        """

    @abstractmethod
    async def get_pool_stats(self, worker_id: str) -> dict | None:
        """Return the latest pool_stats snapshot received via heartbeat,
        or None if the worker is unknown or has not reported stats yet."""

    # --- Enrollment Keys ---

    @abstractmethod
    async def create_enrollment_key(
        self,
        org_id: str,
        name: str,
        created_by: str,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
        allowed_pools: list[str] | None = None,
        allowed_models: list[str] | None = None,
    ) -> tuple[EnrollmentKey, str]:
        """Create an enrollment key.

        Returns ``(key_record, raw_key_string)``.
        The raw key is shown **once** — only its hash is persisted.
        """

    @abstractmethod
    async def list_enrollment_keys(self, org_id: str) -> list[EnrollmentKey]:
        """List all enrollment keys for an organisation."""

    @abstractmethod
    async def revoke_enrollment_key(self, key_id: str) -> None:
        """Revoke an enrollment key so it can no longer be used."""

    @abstractmethod
    async def validate_enrollment_key(
        self,
        org_id: str,
        raw_key: str,
    ) -> EnrollmentKey | None:
        """Validate a raw enrollment key.

        Returns the key record if valid, ``None`` otherwise.
        Checks: not revoked, not expired, not exhausted, org matches.
        """

    @abstractmethod
    async def find_enrollment_key_by_hash(self, key_hash: str) -> EnrollmentKey | None:
        """Global lookup of a USABLE enrollment key by its hash (any org)."""
        ...


# ---------------------------------------------------------------------------
# In-Memory Implementation
# ---------------------------------------------------------------------------


class InMemoryFleetRegistry(FleetRegistry):
    """In-memory implementation for testing and development."""

    def __init__(self) -> None:
        self._workers: dict[str, WorkerRecord] = {}
        # Maps key_id -> (EnrollmentKey, raw_key_string)
        self._keys: dict[str, tuple[EnrollmentKey, str]] = {}
        self._pool_stats_cache: dict[str, dict] = {}

    # --- Workers ---

    async def register_worker(
        self,
        name: str,
        org_id: str,
        capabilities: WorkerCapabilities,
        *,
        project_id: str | None = None,
        enrollment_key: str | None = None,
        secret_hash: str | None = None,
    ) -> WorkerRecord:
        worker_id = str(uuid.uuid4())
        now = _now()

        # Normalize model capabilities
        capabilities = capabilities.model_copy(
            update={
                "models_canonical": ModelNormalizer.canonical_list(
                    capabilities.models_supported
                ),
            }
        )

        auto_approve = False
        if enrollment_key is not None:
            key_record = await self.validate_enrollment_key(org_id, enrollment_key)
            if key_record is not None and self._key_constraints_match(
                key_record, capabilities
            ):
                # Consume from the CURRENT stored state (re-read), not the validated
                # snapshot, so concurrent registrations can't both consume a
                # single-use key. No await between the re-check and the write, so
                # this is atomic under asyncio.
                stored, raw = self._keys[key_record.id]
                if stored.is_usable():
                    auto_approve = True
                    self._keys[key_record.id] = (
                        stored.model_copy(
                            update={"current_uses": stored.current_uses + 1}
                        ),
                        raw,
                    )

        worker = WorkerRecord(
            id=worker_id,
            name=name,
            org_id=org_id,
            project_id=project_id,
            capabilities=capabilities,
            approval_status=(
                WorkerApprovalStatus.APPROVED
                if auto_approve
                else WorkerApprovalStatus.PENDING
            ),
            registered_at=now,
            approved_at=now if auto_approve else None,
            approved_by="enrollment-key" if auto_approve else None,
            secret_hash=secret_hash,
        )
        self._workers[worker_id] = worker
        logger.info(
            "Registered worker %s (org=%s, status=%s)",
            worker_id,
            org_id,
            worker.approval_status.value,
        )
        return worker

    async def get_worker(self, worker_id: str) -> WorkerRecord | None:
        return self._workers.get(worker_id)

    async def list_workers(
        self,
        org_id: str,
        status: WorkerApprovalStatus | None = None,
        pool: str | None = None,
        limit: int = 100,
        project_id: str | None | object = _UNSET,
    ) -> list[WorkerRecord]:
        results: list[WorkerRecord] = []
        for w in self._workers.values():
            if w.org_id != org_id:
                continue
            if status is not None and w.approval_status != status:
                continue
            if pool is not None and w.capabilities.pool != pool:
                continue
            if project_id is not _UNSET and w.project_id != project_id:
                continue
            results.append(w)
            if len(results) >= limit:
                break
        return results

    async def approve_worker(
        self, worker_id: str, approved_by: str
    ) -> WorkerRecord:
        worker = self._workers.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker {worker_id} not found")
        if worker.approval_status != WorkerApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot approve worker in {worker.approval_status.value} state"
            )
        updated = worker.model_copy(
            update={
                "approval_status": WorkerApprovalStatus.APPROVED,
                "approved_at": _now(),
                "approved_by": approved_by,
            }
        )
        self._workers[worker_id] = updated
        return updated

    async def reject_worker(self, worker_id: str) -> WorkerRecord:
        worker = self._workers.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker {worker_id} not found")
        if worker.approval_status != WorkerApprovalStatus.PENDING:
            raise ValueError(
                f"Cannot reject worker in {worker.approval_status.value} state"
            )
        updated = worker.model_copy(
            update={"approval_status": WorkerApprovalStatus.REJECTED}
        )
        self._workers[worker_id] = updated
        return updated

    async def revoke_worker(self, worker_id: str) -> WorkerRecord:
        worker = self._workers.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker {worker_id} not found")
        if worker.approval_status != WorkerApprovalStatus.APPROVED:
            raise ValueError(
                f"Cannot revoke worker in {worker.approval_status.value} state"
            )
        updated = worker.model_copy(
            update={"approval_status": WorkerApprovalStatus.REVOKED}
        )
        self._workers[worker_id] = updated
        return updated

    async def heartbeat(
        self, worker_id: str, *, pool_stats: dict | None = None,
    ) -> None:
        """Update the worker's last_heartbeat timestamp."""
        record = self._workers.get(worker_id)
        if record is None:
            return
        self._workers[worker_id] = record.model_copy(update={"last_heartbeat": _now()})
        if pool_stats is not None:
            self._pool_stats_cache[worker_id] = pool_stats

    async def get_pool_stats(self, worker_id: str) -> dict | None:
        return self._pool_stats_cache.get(worker_id)

    # --- Enrollment Keys ---

    async def create_enrollment_key(
        self,
        org_id: str,
        name: str,
        created_by: str,
        max_uses: int | None = None,
        expires_at: datetime | None = None,
        allowed_pools: list[str] | None = None,
        allowed_models: list[str] | None = None,
    ) -> tuple[EnrollmentKey, str]:
        key_id = str(uuid.uuid4())
        raw_key = secrets.token_urlsafe(32)

        record = EnrollmentKey(
            id=key_id,
            org_id=org_id,
            name=name,
            key_hash=_hash_key(raw_key),
            max_uses=max_uses,
            current_uses=0,
            expires_at=expires_at,
            allowed_pools=allowed_pools or [],
            allowed_models=allowed_models or [],
            created_at=_now(),
            created_by=created_by,
            revoked=False,
        )
        self._keys[key_id] = (record, raw_key)
        logger.info("Created enrollment key %s (org=%s)", key_id, org_id)
        return record, raw_key

    async def list_enrollment_keys(self, org_id: str) -> list[EnrollmentKey]:
        return [
            record
            for record, _ in self._keys.values()
            if record.org_id == org_id
        ]

    async def revoke_enrollment_key(self, key_id: str) -> None:
        entry = self._keys.get(key_id)
        if entry is None:
            raise ValueError(f"Enrollment key {key_id} not found")
        record, raw = entry
        self._keys[key_id] = (record.model_copy(update={"revoked": True}), raw)

    async def validate_enrollment_key(
        self,
        org_id: str,
        raw_key: str,
    ) -> EnrollmentKey | None:
        hashed = _hash_key(raw_key)
        for record, _raw in self._keys.values():
            if record.org_id != org_id:
                continue
            if record.key_hash != hashed:
                continue
            if not record.is_usable():
                return None
            return record
        return None

    async def find_enrollment_key_by_hash(self, key_hash: str) -> EnrollmentKey | None:
        for record, _raw in self._keys.values():
            if record.key_hash == key_hash and record.is_usable():
                return record
        return None

    # --- Helpers ---

    @staticmethod
    def _key_constraints_match(
        key: EnrollmentKey, caps: WorkerCapabilities
    ) -> bool:
        """Check whether the worker capabilities satisfy the key constraints."""
        if key.allowed_pools and caps.pool not in key.allowed_pools:
            return False
        if key.allowed_models:
            canonical = set(caps.models_canonical)
            allowed = set(
                ModelNormalizer.canonical_list(key.allowed_models)
            )
            if not canonical & allowed:
                return False
        return True


# ---------------------------------------------------------------------------
# Postgres Implementation
# ---------------------------------------------------------------------------


class PostgresFleetRegistry(FleetRegistry):
    """SQLAlchemy-backed fleet registry (persistent: SQLite or Postgres).

    Rewritten in B0 to the modern store idiom (engine + SQLAlchemy Core) against
    the real `workers`/`enrollment_keys` schema. Fleet workers are written with
    ``status='fleet'`` so the core workflow load balancer never selects them.

    Isolation is **bidirectional**: this registry not only writes the ``'fleet'``
    sentinel, it also constrains *every* worker read/mutate to ``status='fleet'``
    so it can never see or modify a core workflow worker (``status='active'``) that
    shares the table.
    """

    # Sentinel in the shared ``workers.status`` column. Written on every fleet row
    # and required on every fleet query/update so the two worker systems sharing
    # the table stay mutually invisible.
    _FLEET_STATUS = "fleet"

    def __init__(self, *, engine=None, audit_backend=None) -> None:
        from sagewai.db import factory
        from sagewai.db.models import EnrollmentKeyModel, WorkerModel

        self._engine = engine or factory.get_engine()
        self._audit = audit_backend
        self._workers = WorkerModel.__table__
        self._keys = EnrollmentKeyModel.__table__
        self._inited = False

    async def init(self) -> None:
        """Idempotent. SQLite → build the schema. Non-SQLite → **fail-closed
        startup probe**: verify the DB is reachable AND the fleet schema is
        migrated (the probed columns exist). Any failure propagates so the app
        refuses to serve against an unreachable / unmigrated Postgres."""
        if self._inited:
            return
        from sagewai.db.models import Base

        eng = self._engine  # raw access here — the _begin/_connect helpers would recurse
        if eng.dialect.name == "sqlite":
            async with eng.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            from sqlalchemy import text

            async with eng.connect() as conn:  # connectivity + schema probe
                await conn.execute(
                    text("SELECT secret_hash, project_id FROM workers LIMIT 0")
                )
        self._inited = True

    async def _ensure_init(self) -> None:
        if not self._inited:
            await self.init()

    @asynccontextmanager
    async def _begin(self):
        """Transactional connection; lazily ensures the schema/probe first.
        Uses the RAW engine (not self._begin) — calling _begin here would recurse."""
        await self._ensure_init()
        async with self._engine.begin() as conn:
            yield conn

    @asynccontextmanager
    async def _connect(self):
        """Read connection; lazily ensures the schema/probe first.
        Uses the RAW engine (not self._connect) — calling _connect here would recurse."""
        await self._ensure_init()
        async with self._engine.connect() as conn:
            yield conn

    # -- mapping --------------------------------------------------------
    @staticmethod
    def _row_to_worker(row) -> WorkerRecord:
        m = row._mapping
        caps_data = m["capabilities"] or {}
        caps = WorkerCapabilities.model_validate(caps_data)
        return WorkerRecord(
            id=m["worker_id"], name=m["name"] or "worker", org_id=m["org_id"] or "",
            project_id=m["project_id"], capabilities=caps,
            approval_status=WorkerApprovalStatus(m["approval_status"]),
            last_heartbeat=m["last_heartbeat"], last_probe_at=m["last_probe_at"],
            probe_status=m["probe_status"], registered_at=m["registered_at"],
            approved_at=m["approved_at"], approved_by=m["approved_by"],
            secret_hash=m["secret_hash"],
        )

    # -- workers --------------------------------------------------------
    async def register_worker(self, name, org_id, capabilities, enrollment_key=None,
                              secret_hash=None, project_id=None) -> WorkerRecord:
        from sqlalchemy import insert

        from sagewai.fleet.normalizer import ModelNormalizer

        capabilities = capabilities.model_copy(update={
            "models_canonical": ModelNormalizer.canonical_list(capabilities.models_supported),
        })
        auto_approve = False
        if enrollment_key is not None:
            key = await self.validate_enrollment_key(org_id, enrollment_key)
            if key is not None and self._key_constraints_match(key, capabilities):
                # Consume capacity ATOMICALLY (conditional UPDATE enforcing
                # current_uses < max_uses + not-revoked + not-expired at write
                # time). Only auto-approve if we won the race — under concurrent
                # registrations a single-use key approves exactly one worker.
                auto_approve = await self._consume_enrollment_key(key.id)
        worker_id = str(uuid.uuid4())
        now = _now()
        async with self._begin() as conn:
            await conn.execute(insert(self._workers).values(
                worker_id=worker_id, name=name, org_id=org_id, project_id=project_id,
                status=self._FLEET_STATUS,  # discriminator (see class docstring)
                pool=capabilities.pool, labels=capabilities.labels,
                max_concurrent=capabilities.max_concurrent,
                models_supported=capabilities.models_supported,
                models_canonical=capabilities.models_canonical,
                approval_status=(WorkerApprovalStatus.APPROVED.value if auto_approve
                                 else WorkerApprovalStatus.PENDING.value),
                capabilities=capabilities.model_dump(mode="json"),
                registered_at=now, last_heartbeat=now,
                approved_at=now if auto_approve else None,
                approved_by="enrollment-key" if auto_approve else None,
                secret_hash=secret_hash,
            ))  # `metadata` column uses its default ({}); don't set it here

        return WorkerRecord(
            id=worker_id, name=name, org_id=org_id, project_id=project_id,
            capabilities=capabilities,
            approval_status=(WorkerApprovalStatus.APPROVED if auto_approve
                             else WorkerApprovalStatus.PENDING),
            registered_at=now, last_heartbeat=now,
            approved_at=now if auto_approve else None,
            approved_by="enrollment-key" if auto_approve else None,
            secret_hash=secret_hash,
        )

    async def get_worker(self, worker_id):
        from sqlalchemy import select

        async with self._connect() as conn:
            row = (await conn.execute(
                select(self._workers).where(
                    (self._workers.c.worker_id == worker_id)
                    & (self._workers.c.status == self._FLEET_STATUS)
                )
            )).first()
        return self._row_to_worker(row) if row else None

    async def list_workers(self, org_id, status=None, pool=None, limit=100, project_id=_UNSET):
        from sqlalchemy import select

        q = select(self._workers).where(
            (self._workers.c.org_id == org_id)
            & (self._workers.c.status == self._FLEET_STATUS)
        )
        if status is not None:
            q = q.where(self._workers.c.approval_status == status.value)
        if pool is not None:
            q = q.where(self._workers.c.pool == pool)
        if project_id is not _UNSET:
            # `== None` compiles to IS NULL → org-global; a slug → exact match.
            q = q.where(self._workers.c.project_id == project_id)
        q = q.limit(limit)
        async with self._connect() as conn:
            rows = (await conn.execute(q)).all()
        return [self._row_to_worker(r) for r in rows]

    async def _set_approval(self, worker_id, expect, new, *, approved_by=None):
        from sqlalchemy import update

        vals = {"approval_status": new.value}
        if new == WorkerApprovalStatus.APPROVED:
            vals["approved_at"] = _now()
            vals["approved_by"] = approved_by
        # Atomic compare-and-swap: the expected-state check is part of the WHERE,
        # so concurrent approve/reject can't both win (no Python read-then-write
        # race). rowcount == 0 means the row wasn't in the expected state (or isn't
        # a fleet worker / doesn't exist).
        async with self._begin() as conn:
            result = await conn.execute(update(self._workers).where(
                (self._workers.c.worker_id == worker_id)
                & (self._workers.c.status == self._FLEET_STATUS)
                & (self._workers.c.approval_status == expect.value)
            ).values(**vals))
        if result.rowcount == 0:
            worker = await self.get_worker(worker_id)
            if worker is None:
                raise ValueError(f"Worker {worker_id} not found")
            raise ValueError(f"Cannot transition worker in {worker.approval_status.value} state")
        return await self.get_worker(worker_id)

    async def approve_worker(self, worker_id, approved_by):
        return await self._set_approval(worker_id, WorkerApprovalStatus.PENDING,
                                        WorkerApprovalStatus.APPROVED, approved_by=approved_by)

    async def reject_worker(self, worker_id):
        return await self._set_approval(worker_id, WorkerApprovalStatus.PENDING,
                                        WorkerApprovalStatus.REJECTED)

    async def revoke_worker(self, worker_id):
        return await self._set_approval(worker_id, WorkerApprovalStatus.APPROVED,
                                        WorkerApprovalStatus.REVOKED)

    async def heartbeat(self, worker_id, *, pool_stats=None):
        from sqlalchemy import select, update

        fleet_row = (self._workers.c.worker_id == worker_id) & (
            self._workers.c.status == self._FLEET_STATUS
        )
        vals = {"last_heartbeat": _now()}
        if pool_stats is not None:
            async with self._connect() as conn:
                meta = (await conn.execute(
                    select(self._workers.c["metadata"]).where(fleet_row)
                )).scalar_one_or_none()
            meta = dict(meta or {})
            meta["pool_stats"] = pool_stats
            vals["metadata"] = meta
        async with self._begin() as conn:
            await conn.execute(update(self._workers).where(fleet_row).values(**vals))

    async def get_pool_stats(self, worker_id):
        from sqlalchemy import select

        async with self._connect() as conn:
            meta = (await conn.execute(
                select(self._workers.c["metadata"]).where(
                    (self._workers.c.worker_id == worker_id)
                    & (self._workers.c.status == self._FLEET_STATUS)
                )
            )).scalar_one_or_none()
        return (meta or {}).get("pool_stats")

    # -- enrollment keys ------------------------------------------------
    async def create_enrollment_key(self, org_id, name, created_by, max_uses=None,
                                    expires_at=None, allowed_pools=None, allowed_models=None):
        from sqlalchemy import insert

        key_id = str(uuid.uuid4())
        raw_key = secrets.token_urlsafe(32)
        now = _now()
        async with self._begin() as conn:
            await conn.execute(insert(self._keys).values(
                id=key_id, org_id=org_id, name=name, key_hash=_hash_key(raw_key),
                max_uses=max_uses, current_uses=0, expires_at=expires_at,
                allowed_pools=allowed_pools or [], allowed_models=allowed_models or [],
                created_at=now, created_by=created_by, revoked=False,
            ))
        return EnrollmentKey(
            id=key_id, org_id=org_id, name=name, key_hash=_hash_key(raw_key),
            max_uses=max_uses, current_uses=0, expires_at=expires_at,
            allowed_pools=allowed_pools or [], allowed_models=allowed_models or [],
            created_at=now, created_by=created_by, revoked=False,
        ), raw_key

    @staticmethod
    def _row_to_key(row) -> EnrollmentKey:
        m = row._mapping
        return EnrollmentKey(
            id=m["id"], org_id=m["org_id"], name=m["name"] or "", key_hash=m["key_hash"],
            max_uses=m["max_uses"], current_uses=m["current_uses"], expires_at=m["expires_at"],
            allowed_pools=list(m["allowed_pools"] or []), allowed_models=list(m["allowed_models"] or []),
            created_at=m["created_at"], created_by=m["created_by"] or "", revoked=m["revoked"],
        )

    async def list_enrollment_keys(self, org_id):
        from sqlalchemy import select

        async with self._connect() as conn:
            rows = (await conn.execute(
                select(self._keys).where(self._keys.c.org_id == org_id)
            )).all()
        return [self._row_to_key(r) for r in rows]

    async def revoke_enrollment_key(self, key_id):
        from sqlalchemy import update

        async with self._begin() as conn:
            result = await conn.execute(update(self._keys)
                                        .where(self._keys.c.id == key_id).values(revoked=True))
        if result.rowcount == 0:  # parity with InMemory: unknown key is an error
            raise ValueError(f"Enrollment key {key_id} not found")

    async def validate_enrollment_key(self, org_id, raw_key):
        from sqlalchemy import select

        async with self._connect() as conn:
            row = (await conn.execute(select(self._keys).where(
                (self._keys.c.org_id == org_id) & (self._keys.c.key_hash == _hash_key(raw_key))
            ))).first()
        if row is None:
            return None
        key = self._row_to_key(row)
        return key if key.is_usable() else None

    async def find_enrollment_key_by_hash(self, key_hash):
        from sqlalchemy import select

        async with self._connect() as conn:
            row = (await conn.execute(
                select(self._keys).where(self._keys.c.key_hash == key_hash)
            )).first()
        if row is None:
            return None
        key = self._row_to_key(row)
        return key if key.is_usable() else None

    async def _consume_enrollment_key(self, key_id) -> bool:
        """Atomically increment ``current_uses`` iff the key still has capacity and
        is not revoked/expired, all enforced in the UPDATE's WHERE. Returns True
        only if this call consumed a use (so the caller may auto-approve). Two
        concurrent registrations against a single-use key: exactly one gets True.
        """
        from sqlalchemy import or_, update

        cond = (
            (self._keys.c.id == key_id)
            & (self._keys.c.revoked.is_(False))
            & or_(
                self._keys.c.max_uses.is_(None),
                self._keys.c.current_uses < self._keys.c.max_uses,
            )
            & or_(
                self._keys.c.expires_at.is_(None),
                self._keys.c.expires_at > _now(),
            )
        )
        async with self._begin() as conn:
            result = await conn.execute(
                update(self._keys).where(cond).values(
                    current_uses=self._keys.c.current_uses + 1
                )
            )
        return result.rowcount == 1

    @staticmethod
    def _key_constraints_match(
        key: EnrollmentKey, caps: WorkerCapabilities
    ) -> bool:
        """Check whether the worker capabilities satisfy the key constraints."""
        if key.allowed_pools and caps.pool not in key.allowed_pools:
            return False
        if key.allowed_models:
            canonical = set(caps.models_canonical)
            allowed = set(
                ModelNormalizer.canonical_list(key.allowed_models)
            )
            if not canonical & allowed:
                return False
        return True
