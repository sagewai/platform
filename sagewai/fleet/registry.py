# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Fleet worker registry — registration, approval, and enrollment key management.

Provides the ``FleetRegistry`` ABC and two implementations:

- ``InMemoryFleetRegistry``: for testing and development.
- ``PostgresFleetRegistry``: production-ready with asyncpg, storing data in the
  ``workers`` and ``enrollment_keys`` tables created by migration 005.

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
import json
import logging
import secrets
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from sagewai.fleet.audit import FleetAuditBackend, FleetAuditEvent, FleetAuditEventType
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


class FleetRegistry(ABC):
    """Abstract base for fleet worker and enrollment key management."""

    # --- Worker Registration ---

    @abstractmethod
    async def register_worker(
        self,
        name: str,
        org_id: str,
        capabilities: WorkerCapabilities,
        enrollment_key: str | None = None,
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
    ) -> list[WorkerRecord]:
        """List workers for an organisation, optionally filtered."""

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
    async def heartbeat(self, worker_id: str) -> None:
        """Update the worker's ``last_heartbeat`` timestamp."""

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


# ---------------------------------------------------------------------------
# In-Memory Implementation
# ---------------------------------------------------------------------------


class InMemoryFleetRegistry(FleetRegistry):
    """In-memory implementation for testing and development."""

    def __init__(self) -> None:
        self._workers: dict[str, WorkerRecord] = {}
        # Maps key_id -> (EnrollmentKey, raw_key_string)
        self._keys: dict[str, tuple[EnrollmentKey, str]] = {}

    # --- Workers ---

    async def register_worker(
        self,
        name: str,
        org_id: str,
        capabilities: WorkerCapabilities,
        enrollment_key: str | None = None,
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
            if key_record is not None:
                # Check pool/model constraints
                if self._key_constraints_match(key_record, capabilities):
                    auto_approve = True
                    # Increment usage
                    updated = key_record.model_copy(
                        update={"current_uses": key_record.current_uses + 1}
                    )
                    raw = self._keys[key_record.id][1]
                    self._keys[key_record.id] = (updated, raw)

        worker = WorkerRecord(
            id=worker_id,
            name=name,
            org_id=org_id,
            capabilities=capabilities,
            approval_status=(
                WorkerApprovalStatus.APPROVED
                if auto_approve
                else WorkerApprovalStatus.PENDING
            ),
            registered_at=now,
            approved_at=now if auto_approve else None,
            approved_by="enrollment-key" if auto_approve else None,
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
    ) -> list[WorkerRecord]:
        results: list[WorkerRecord] = []
        for w in self._workers.values():
            if w.org_id != org_id:
                continue
            if status is not None and w.approval_status != status:
                continue
            if pool is not None and w.capabilities.pool != pool:
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

    async def heartbeat(self, worker_id: str) -> None:
        worker = self._workers.get(worker_id)
        if worker is None:
            raise ValueError(f"Worker {worker_id} not found")
        self._workers[worker_id] = worker.model_copy(
            update={"last_heartbeat": _now()}
        )

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
    """Postgres-backed fleet registry using asyncpg.

    Uses the ``workers`` and ``enrollment_keys`` tables from migration 005.
    Optionally records audit events on all state changes.
    """

    def __init__(
        self,
        pool,  # asyncpg.Pool
        audit_backend=None,
    ) -> None:
        self._pool = pool
        self._audit = audit_backend

    # --- Workers ---

    async def register_worker(
        self,
        name: str,
        org_id: str,
        capabilities: WorkerCapabilities,
        enrollment_key: str | None = None,
    ) -> WorkerRecord:
        worker_id = str(uuid.uuid4())
        now = _now()

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
            if key_record is not None:
                if self._key_constraints_match(key_record, capabilities):
                    auto_approve = True
                    await self._pool.execute(
                        """UPDATE enrollment_keys
                           SET current_uses = current_uses + 1
                           WHERE id = $1""",
                        key_record.id,
                    )

        status = (
            WorkerApprovalStatus.APPROVED
            if auto_approve
            else WorkerApprovalStatus.PENDING
        )

        caps_json = capabilities.model_dump_json()
        await self._pool.execute(
            """INSERT INTO workers
               (id, name, org_id, fleet_capabilities, fleet_approval_status,
                fleet_registered_at, fleet_approved_at, fleet_approved_by,
                pool, labels)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)""",
            worker_id,
            name,
            org_id,
            caps_json,
            status.value,
            now,
            now if auto_approve else None,
            "enrollment-key" if auto_approve else None,
            capabilities.pool,
            json.dumps(capabilities.labels),
        )

        worker = WorkerRecord(
            id=worker_id,
            name=name,
            org_id=org_id,
            capabilities=capabilities,
            approval_status=status,
            registered_at=now,
            approved_at=now if auto_approve else None,
            approved_by="enrollment-key" if auto_approve else None,
        )

        if self._audit:
            await self._audit.record(FleetAuditEvent(
                org_id=org_id,
                worker_id=worker_id,
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                details={"auto_approved": auto_approve},
            ))

        logger.info(
            "Registered worker %s (org=%s, status=%s)",
            worker_id,
            org_id,
            status.value,
        )
        return worker

    async def get_worker(self, worker_id: str) -> WorkerRecord | None:
        row = await self._pool.fetchrow(
            """SELECT id, name, org_id, fleet_capabilities,
                      fleet_approval_status, last_heartbeat,
                      fleet_registered_at, fleet_approved_at,
                      fleet_approved_by
               FROM workers WHERE id = $1""",
            worker_id,
        )
        if row is None:
            return None
        return self._row_to_worker(row)

    async def list_workers(
        self,
        org_id: str,
        status: WorkerApprovalStatus | None = None,
        pool: str | None = None,
        limit: int = 100,
    ) -> list[WorkerRecord]:
        query = (
            "SELECT id, name, org_id, fleet_capabilities,"
            " fleet_approval_status, last_heartbeat,"
            " fleet_registered_at, fleet_approved_at,"
            " fleet_approved_by"
            " FROM workers WHERE org_id = $1"
        )
        params: list = [org_id]
        idx = 2

        if status is not None:
            query += f" AND fleet_approval_status = ${idx}"
            params.append(status.value)
            idx += 1

        if pool is not None:
            query += f" AND pool = ${idx}"
            params.append(pool)
            idx += 1

        query += f" ORDER BY fleet_registered_at DESC LIMIT ${idx}"
        params.append(limit)

        rows = await self._pool.fetch(query, *params)
        return [self._row_to_worker(r) for r in rows]

    async def approve_worker(
        self, worker_id: str, approved_by: str
    ) -> WorkerRecord:
        now = _now()
        result = await self._pool.execute(
            """UPDATE workers
               SET fleet_approval_status = $1,
                   fleet_approved_at = $2,
                   fleet_approved_by = $3
               WHERE id = $4
                 AND fleet_approval_status = $5""",
            WorkerApprovalStatus.APPROVED.value,
            now,
            approved_by,
            worker_id,
            WorkerApprovalStatus.PENDING.value,
        )
        if result == "UPDATE 0":
            worker = await self.get_worker(worker_id)
            if worker is None:
                raise ValueError(f"Worker {worker_id} not found")
            raise ValueError(
                f"Cannot approve worker in {worker.approval_status.value} state"
            )

        if self._audit:
            worker = await self.get_worker(worker_id)
            if worker:
                await self._audit.record(FleetAuditEvent(
                    org_id=worker.org_id,
                    worker_id=worker_id,
                    event_type=FleetAuditEventType.WORKER_APPROVED,
                    details={"approved_by": approved_by},
                ))

        worker = await self.get_worker(worker_id)
        assert worker is not None
        return worker

    async def reject_worker(self, worker_id: str) -> WorkerRecord:
        result = await self._pool.execute(
            """UPDATE workers
               SET fleet_approval_status = $1
               WHERE id = $2
                 AND fleet_approval_status = $3""",
            WorkerApprovalStatus.REJECTED.value,
            worker_id,
            WorkerApprovalStatus.PENDING.value,
        )
        if result == "UPDATE 0":
            worker = await self.get_worker(worker_id)
            if worker is None:
                raise ValueError(f"Worker {worker_id} not found")
            raise ValueError(
                f"Cannot reject worker in {worker.approval_status.value} state"
            )

        if self._audit:
            worker = await self.get_worker(worker_id)
            if worker:
                await self._audit.record(FleetAuditEvent(
                    org_id=worker.org_id,
                    worker_id=worker_id,
                    event_type=FleetAuditEventType.WORKER_REJECTED,
                    details={},
                ))

        worker = await self.get_worker(worker_id)
        assert worker is not None
        return worker

    async def revoke_worker(self, worker_id: str) -> WorkerRecord:
        result = await self._pool.execute(
            """UPDATE workers
               SET fleet_approval_status = $1
               WHERE id = $2
                 AND fleet_approval_status = $3""",
            WorkerApprovalStatus.REVOKED.value,
            worker_id,
            WorkerApprovalStatus.APPROVED.value,
        )
        if result == "UPDATE 0":
            worker = await self.get_worker(worker_id)
            if worker is None:
                raise ValueError(f"Worker {worker_id} not found")
            raise ValueError(
                f"Cannot revoke worker in {worker.approval_status.value} state"
            )

        if self._audit:
            worker = await self.get_worker(worker_id)
            if worker:
                await self._audit.record(FleetAuditEvent(
                    org_id=worker.org_id,
                    worker_id=worker_id,
                    event_type=FleetAuditEventType.WORKER_REVOKED,
                    details={},
                ))

        worker = await self.get_worker(worker_id)
        assert worker is not None
        return worker

    async def heartbeat(self, worker_id: str) -> None:
        result = await self._pool.execute(
            "UPDATE workers SET last_heartbeat = $1 WHERE id = $2",
            _now(),
            worker_id,
        )
        if result == "UPDATE 0":
            raise ValueError(f"Worker {worker_id} not found")

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
        key_hash = _hash_key(raw_key)
        now = _now()

        pools = allowed_pools or []
        models = allowed_models or []

        await self._pool.execute(
            """INSERT INTO enrollment_keys
               (id, org_id, name, key_hash, max_uses, current_uses,
                expires_at, allowed_pools, allowed_models,
                created_at, created_by, revoked)
               VALUES ($1, $2, $3, $4, $5, 0, $6, $7, $8, $9, $10, false)""",
            key_id,
            org_id,
            name,
            key_hash,
            max_uses,
            expires_at,
            json.dumps(pools),
            json.dumps(models),
            now,
            created_by,
        )

        record = EnrollmentKey(
            id=key_id,
            org_id=org_id,
            name=name,
            key_hash=key_hash,
            max_uses=max_uses,
            current_uses=0,
            expires_at=expires_at,
            allowed_pools=pools,
            allowed_models=models,
            created_at=now,
            created_by=created_by,
            revoked=False,
        )

        if self._audit:
            await self._audit.record(FleetAuditEvent(
                org_id=org_id,
                event_type=FleetAuditEventType.ENROLLMENT_KEY_CREATED,
                details={"key_id": key_id, "name": name},
            ))

        logger.info("Created enrollment key %s (org=%s)", key_id, org_id)
        return record, raw_key

    async def list_enrollment_keys(self, org_id: str) -> list[EnrollmentKey]:
        rows = await self._pool.fetch(
            """SELECT id, org_id, name, key_hash, max_uses, current_uses,
                      expires_at, allowed_pools, allowed_models,
                      created_at, created_by, revoked
               FROM enrollment_keys WHERE org_id = $1
               ORDER BY created_at DESC""",
            org_id,
        )
        return [self._row_to_enrollment_key(r) for r in rows]

    async def revoke_enrollment_key(self, key_id: str) -> None:
        result = await self._pool.execute(
            "UPDATE enrollment_keys SET revoked = true WHERE id = $1",
            key_id,
        )
        if result == "UPDATE 0":
            raise ValueError(f"Enrollment key {key_id} not found")

        if self._audit:
            row = await self._pool.fetchrow(
                "SELECT org_id FROM enrollment_keys WHERE id = $1", key_id
            )
            if row:
                await self._audit.record(FleetAuditEvent(
                    org_id=row["org_id"],
                    event_type=FleetAuditEventType.ENROLLMENT_KEY_REVOKED,
                    details={"key_id": key_id},
                ))

    async def validate_enrollment_key(
        self,
        org_id: str,
        raw_key: str,
    ) -> EnrollmentKey | None:
        key_hash = _hash_key(raw_key)
        row = await self._pool.fetchrow(
            """SELECT id, org_id, name, key_hash, max_uses, current_uses,
                      expires_at, allowed_pools, allowed_models,
                      created_at, created_by, revoked
               FROM enrollment_keys
               WHERE org_id = $1 AND key_hash = $2 AND revoked = false""",
            org_id,
            key_hash,
        )
        if row is None:
            return None
        record = self._row_to_enrollment_key(row)
        if not record.is_usable():
            return None
        return record

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

    @staticmethod
    def _row_to_worker(row) -> WorkerRecord:
        caps_data = row["fleet_capabilities"]
        if isinstance(caps_data, str):
            caps = WorkerCapabilities.model_validate_json(caps_data)
        else:
            caps = WorkerCapabilities.model_validate(caps_data)

        return WorkerRecord(
            id=row["id"],
            name=row["name"],
            org_id=row["org_id"],
            capabilities=caps,
            approval_status=WorkerApprovalStatus(row["fleet_approval_status"]),
            last_heartbeat=row.get("last_heartbeat"),
            registered_at=row["fleet_registered_at"],
            approved_at=row.get("fleet_approved_at"),
            approved_by=row.get("fleet_approved_by"),
        )

    @staticmethod
    def _row_to_enrollment_key(row) -> EnrollmentKey:
        pools = row["allowed_pools"]
        if isinstance(pools, str):
            pools = json.loads(pools)
        models = row["allowed_models"]
        if isinstance(models, str):
            models = json.loads(models)

        return EnrollmentKey(
            id=row["id"],
            org_id=row["org_id"],
            name=row["name"],
            key_hash=row["key_hash"],
            max_uses=row["max_uses"],
            current_uses=row["current_uses"],
            expires_at=row["expires_at"],
            allowed_pools=pools or [],
            allowed_models=models or [],
            created_at=row["created_at"],
            created_by=row["created_by"],
            revoked=row["revoked"],
        )
