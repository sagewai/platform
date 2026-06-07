# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""SQLAlchemy Core-backed stores for the LLM Harness.

Drop-in replacement for :class:`InMemoryHarnessStore`.  Works on both
SQLite (default, via aiosqlite) and PostgreSQL (via asyncpg).

Usage::

    from sagewai.harness.postgres_store import PostgresHarnessStore

    # Default engine (from sagewai.db.factory):
    store = PostgresHarnessStore()

    # Explicit engine:
    from sagewai.db.engine import create_engine
    engine = create_engine("postgresql+asyncpg://user:pass@host/db")
    store = PostgresHarnessStore(engine=engine)

    # Back-compat: pass a URL string positionally or via keyword:
    store = PostgresHarnessStore("postgresql+asyncpg://...")
    store = PostgresHarnessStore(database_url="postgresql+asyncpg://...")
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncEngine

from sagewai.db import factory
from sagewai.db.dialect import upsert
from sagewai.db.engine import create_engine
from sagewai.db.models import (
    Base,
    HarnessAuditModel,
    HarnessKeyModel,
    HarnessPolicyModel,
    HarnessSpendModel,
)
from sagewai.harness.models import (
    HarnessAuditEvent,
    HarnessIdentity,
    HarnessKey,
    PolicyRule,
    PolicyScope,
    SpendRecord,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "sk-harness-"
_KEY_LENGTH = 32


def _to_dt(ts: float) -> datetime:
    """Convert a Unix float timestamp to timezone-aware datetime."""
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _from_dt(dt: Any) -> float:
    """Convert a datetime (or numeric) to a Unix float timestamp."""
    if dt is None:
        return 0.0
    if hasattr(dt, "timestamp"):
        return dt.timestamp()
    return float(dt)


class PostgresHarnessStore:
    """SQLAlchemy Core-backed implementation of all harness stores.

    Works on both SQLite (default) and PostgreSQL.  Tables must exist before
    use — on SQLite they are created by :meth:`init`; on PostgreSQL Alembic
    migration 001 creates them.

    Tables used:
        - ``harness_policies``
        - ``harness_keys``
        - ``harness_spend``
        - ``harness_audit``

    Policy filtering / ordering strategy (Option A — Python-side)
    -------------------------------------------------------------
    Policies are a small set so all candidate rows are fetched and the
    org-scope filter + priority DESC sort are applied in Python.  This
    exactly matches :class:`InMemoryHarnessStore`'s semantics and is
    dialect-agnostic (no ``data->>'...'`` JSON-path operators needed).

    Specifically::

        # InMemoryHarnessStore.list_policies:
        if org_id:
            policies = [p for p in policies
                        if p.scope.org_id is None or p.scope.org_id == org_id]
        # (no explicit sort in InMemoryHarnessStore — insertion order)
        # We add priority DESC sort to match the PostgresHarnessStore intent.

    Constructor back-compat
    -----------------------
    Previous callers passed an asyncpg pool as the first positional argument.
    That contract is preserved via the ``pool`` parameter (accepted, ignored).
    """

    def __init__(
        self,
        database_url: str | None = None,
        pool: Any = None,  # kept for API back-compat with asyncpg callers; ignored
        *,
        engine: AsyncEngine | None = None,
    ) -> None:
        if engine is not None:
            self._engine: AsyncEngine = engine
        elif database_url is not None:
            self._engine = create_engine(database_url)
        else:
            self._engine = factory.get_engine()
        # pool is intentionally ignored — SQLAlchemy engine owns connection pooling

    async def init(self) -> None:
        """Bootstrap the schema when using SQLite; no-op on PostgreSQL (Alembic owns it)."""
        if self._engine.dialect.name == "sqlite":
            async with self._engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

    # ------------------------------------------------------------------
    # Policy Store
    # ------------------------------------------------------------------

    async def list_policies(
        self, *, org_id: str | None = None
    ) -> list[PolicyRule]:
        """List policies filtered by org scope, sorted by priority DESC.

        Uses Python-side filtering (Option A) to match InMemoryHarnessStore:
        - With org_id: include rows where scope.org_id is None OR == org_id.
        - Without org_id: include all rows.
        All results are sorted by priority DESC (highest first).
        """
        tbl = HarnessPolicyModel.__table__
        stmt = select(tbl)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        policies = [self._row_to_policy(r) for r in rows]

        # Python-side scope filter — matches InMemoryHarnessStore exactly
        if org_id:
            policies = [
                p for p in policies
                if p.scope.org_id is None or p.scope.org_id == org_id
            ]

        # Priority DESC sort
        policies.sort(key=lambda p: p.priority, reverse=True)
        return policies

    async def get_policy(self, policy_id: str) -> PolicyRule | None:
        """Get a policy by ID."""
        tbl = HarnessPolicyModel.__table__
        stmt = select(tbl).where(tbl.c.id == policy_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_policy(row)

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Create (or upsert on PK conflict) a policy."""
        values = self._policy_to_values(rule)
        stmt = upsert(
            HarnessPolicyModel.__table__,
            values,
            index_elements=["id"],
            dialect=self._engine.dialect.name,
        )
        async with self._engine.begin() as conn:
            await conn.execute(stmt)
        return rule

    async def update_policy(
        self, policy_id: str, rule: PolicyRule
    ) -> PolicyRule | None:
        """Update an existing policy. Returns None if not found."""
        rule.id = policy_id
        values = self._policy_to_values(rule)
        # Remove 'id' and 'created_at' from update set
        update_values = {k: v for k, v in values.items() if k not in ("id", "created_at")}
        update_values["updated_at"] = datetime.now(timezone.utc)

        tbl = HarnessPolicyModel.__table__
        stmt = (
            sa_update(tbl)
            .where(tbl.c.id == policy_id)
            .values(**update_values)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        if result.rowcount == 0:
            return None
        return rule

    async def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy. Returns True if deleted, False if not found."""
        tbl = HarnessPolicyModel.__table__
        stmt = sa_delete(tbl).where(tbl.c.id == policy_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount != 0

    # ------------------------------------------------------------------
    # Key Store
    # ------------------------------------------------------------------

    async def create_key(self, key: HarnessKey) -> str:
        """Create a new harness key. Returns the plaintext key (shown once).

        The key is stored as a SHA-256 hash; the plaintext is only
        returned at creation time.
        """
        plaintext = _KEY_PREFIX + secrets.token_hex(_KEY_LENGTH)
        key.key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key.key_suffix = plaintext[-4:]

        now = datetime.now(timezone.utc)
        created_dt = _to_dt(key.created_at) if key.created_at else now
        expires_dt = _to_dt(key.expires_at) if key.expires_at else None

        values = {
            "id": key.id,
            "key_hash": key.key_hash,
            "key_suffix": key.key_suffix,
            "name": key.name or None,
            "user_id": key.user_id,
            "org_id": key.org_id,
            "team_id": key.team_id,
            "project_id": key.project_id,
            "allowed_models": key.allowed_models,
            "max_budget_daily_usd": key.max_budget_daily_usd,
            "max_budget_monthly_usd": key.max_budget_monthly_usd,
            "enabled": key.enabled,
            "created_at": created_dt,
            "expires_at": expires_dt,
        }
        async with self._engine.begin() as conn:
            await conn.execute(HarnessKeyModel.__table__.insert().values(**values))
        return plaintext

    async def validate_key(self, plaintext: str) -> HarnessIdentity | None:
        """Validate a plaintext key and return the associated identity.

        Returns None if the key is invalid, disabled, or expired.
        """
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        tbl = HarnessKeyModel.__table__
        stmt = select(tbl).where(tbl.c.key_hash == key_hash)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None

        key = self._row_to_key(row)
        if not key.enabled or key.is_expired():
            return None
        return key.to_identity()

    async def list_keys(
        self, *, org_id: str | None = None
    ) -> list[HarnessKey]:
        """List all keys, optionally filtered by org."""
        tbl = HarnessKeyModel.__table__
        stmt = select(tbl).order_by(tbl.c.created_at.desc())
        if org_id:
            stmt = stmt.where(tbl.c.org_id == org_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_key(r) for r in rows]

    async def get_key(self, key_id: str) -> HarnessKey | None:
        """Get a key by ID."""
        tbl = HarnessKeyModel.__table__
        stmt = select(tbl).where(tbl.c.id == key_id)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            row = result.mappings().first()
        if row is None:
            return None
        return self._row_to_key(row)

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke (disable) a key."""
        tbl = HarnessKeyModel.__table__
        stmt = (
            sa_update(tbl)
            .where(tbl.c.id == key_id)
            .values(enabled=False)
        )
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount != 0

    async def delete_key(self, key_id: str) -> bool:
        """Delete a key."""
        tbl = HarnessKeyModel.__table__
        stmt = sa_delete(tbl).where(tbl.c.id == key_id)
        async with self._engine.begin() as conn:
            result = await conn.execute(stmt)
        return result.rowcount != 0

    # ------------------------------------------------------------------
    # Spend Store
    # ------------------------------------------------------------------

    async def record_spend(self, record: SpendRecord) -> None:
        """Record a spend event."""
        ts_dt = _to_dt(record.timestamp)
        values = {
            "id": record.id,
            "timestamp": ts_dt,
            "user_id": record.user_id or None,
            "team_id": record.team_id,
            "project_id": record.project_id,
            "org_id": record.org_id,
            "model_requested": record.model_requested or None,
            "model_used": record.model_used or None,
            "complexity_tier": record.complexity_tier or None,
            "input_tokens": record.input_tokens,
            "output_tokens": record.output_tokens,
            "cost_usd": record.cost_usd,
            "latency_ms": record.latency_ms,
            "policy_applied": record.policy_applied,
            "budget_action": record.budget_action,
            "key_id": record.key_id or None,
        }
        async with self._engine.begin() as conn:
            await conn.execute(HarnessSpendModel.__table__.insert().values(**values))

    async def get_spend(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[SpendRecord]:
        """Query spend records with optional filters."""
        tbl = HarnessSpendModel.__table__
        stmt = select(tbl).order_by(tbl.c.timestamp.desc()).limit(limit)
        if org_id:
            stmt = stmt.where(tbl.c.org_id == org_id)
        if user_id:
            stmt = stmt.where(tbl.c.user_id == user_id)
        if since is not None:
            since_dt = _to_dt(since)
            stmt = stmt.where(tbl.c.timestamp >= since_dt)
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_spend(r) for r in rows]

    async def get_spend_summary(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get aggregated spend summary."""
        now = time.time()
        day_start = _to_dt(now - (now % 86400))
        month_start = _to_dt(now - (30 * 86400))

        tbl = HarnessSpendModel.__table__

        def _base_stmt():
            s = select(
                func.coalesce(func.sum(tbl.c.cost_usd), 0).label("cost"),
                func.count().label("cnt"),
            )
            if org_id:
                s = s.where(tbl.c.org_id == org_id)
            if user_id:
                s = s.where(tbl.c.user_id == user_id)
            return s

        daily_stmt = _base_stmt().where(tbl.c.timestamp >= day_start)
        monthly_stmt = _base_stmt().where(tbl.c.timestamp >= month_start)
        total_stmt = _base_stmt()

        async with self._engine.connect() as conn:
            daily_row = (await conn.execute(daily_stmt)).mappings().first()
            monthly_row = (await conn.execute(monthly_stmt)).mappings().first()
            total_row = (await conn.execute(total_stmt)).mappings().first()

        return {
            "daily_cost_usd": float(daily_row["cost"]),
            "daily_requests": int(daily_row["cnt"]),
            "monthly_cost_usd": float(monthly_row["cost"]),
            "monthly_requests": int(monthly_row["cnt"]),
            "total_cost_usd": float(total_row["cost"]),
            "total_requests": int(total_row["cnt"]),
        }

    async def get_spend_by_model(
        self,
        *,
        org_id: str | None = None,
        since: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get spend breakdown by model."""
        tbl = HarnessSpendModel.__table__
        stmt = select(
            tbl.c.model_used,
            func.coalesce(func.sum(tbl.c.cost_usd), 0).label("cost"),
            func.count().label("cnt"),
            func.coalesce(func.sum(tbl.c.input_tokens), 0).label("inp"),
            func.coalesce(func.sum(tbl.c.output_tokens), 0).label("outp"),
        ).group_by(tbl.c.model_used)
        if org_id:
            stmt = stmt.where(tbl.c.org_id == org_id)
        if since is not None:
            stmt = stmt.where(tbl.c.timestamp >= _to_dt(since))

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        by_model: dict[str, dict[str, Any]] = {}
        for r in rows:
            model = r["model_used"] or ""
            by_model[model] = {
                "cost_usd": float(r["cost"]),
                "requests": int(r["cnt"]),
                "input_tokens": int(r["inp"]),
                "output_tokens": int(r["outp"]),
            }
        return by_model

    async def get_spend_by_user(
        self,
        *,
        org_id: str | None = None,
        since: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get spend breakdown by user."""
        tbl = HarnessSpendModel.__table__
        stmt = select(
            tbl.c.user_id,
            func.coalesce(func.sum(tbl.c.cost_usd), 0).label("cost"),
            func.count().label("cnt"),
        ).group_by(tbl.c.user_id)
        if org_id:
            stmt = stmt.where(tbl.c.org_id == org_id)
        if since is not None:
            stmt = stmt.where(tbl.c.timestamp >= _to_dt(since))

        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()

        by_user: dict[str, dict[str, Any]] = {}
        for r in rows:
            user = r["user_id"] or ""
            by_user[user] = {
                "cost_usd": float(r["cost"]),
                "requests": int(r["cnt"]),
            }
        return by_user

    # ------------------------------------------------------------------
    # Audit Store
    # ------------------------------------------------------------------

    async def record_audit(self, event: HarnessAuditEvent) -> None:
        """Record an audit event."""
        ts_dt = _to_dt(event.timestamp)
        values = {
            "id": event.id,
            "timestamp": ts_dt,
            "event_type": event.event_type,
            "user_id": event.user_id or None,
            "org_id": event.org_id,
            "details": event.details,
        }
        async with self._engine.begin() as conn:
            await conn.execute(HarnessAuditModel.__table__.insert().values(**values))

    async def get_audit(
        self,
        *,
        org_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[HarnessAuditEvent]:
        """Query audit events with optional filters."""
        tbl = HarnessAuditModel.__table__
        stmt = select(tbl).order_by(tbl.c.timestamp.desc()).limit(limit)
        if org_id:
            stmt = stmt.where(tbl.c.org_id == org_id)
        if event_type:
            stmt = stmt.where(tbl.c.event_type == event_type)
        if since is not None:
            stmt = stmt.where(tbl.c.timestamp >= _to_dt(since))
        async with self._engine.connect() as conn:
            result = await conn.execute(stmt)
            rows = result.mappings().all()
        return [self._row_to_audit(r) for r in rows]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _policy_to_values(rule: PolicyRule) -> dict[str, Any]:
        """Convert a PolicyRule to a flat dict for DB insert/update."""
        now = datetime.now(timezone.utc)
        return {
            "id": rule.id,
            "name": rule.name,
            "description": rule.description,
            "org_id": rule.scope.org_id,
            "team_id": rule.scope.team_id,
            "project_id": rule.scope.project_id,
            "user_id": rule.scope.user_id,
            "priority": rule.priority,
            "tier_overrides": rule.tier_overrides,
            "blocked_models": rule.blocked_models,
            "allowed_models": rule.allowed_models,
            "max_tier": rule.max_tier.value if rule.max_tier is not None else None,
            "force_model": rule.force_model,
            "allow_override": rule.allow_override,
            "enabled": rule.enabled,
            "created_at": now,
            "updated_at": now,
        }

    @staticmethod
    def _row_to_policy(row: Any) -> PolicyRule:
        """Convert a SQLAlchemy row mapping to a PolicyRule."""
        allowed_models = row["allowed_models"]
        if isinstance(allowed_models, str):
            import json
            allowed_models = json.loads(allowed_models)
        blocked_models = row["blocked_models"]
        if isinstance(blocked_models, str):
            import json
            blocked_models = json.loads(blocked_models)
        tier_overrides = row["tier_overrides"]
        if isinstance(tier_overrides, str):
            import json
            tier_overrides = json.loads(tier_overrides)

        from sagewai.harness.models import ComplexityTier
        max_tier = row["max_tier"]
        if max_tier:
            max_tier = ComplexityTier(max_tier)

        return PolicyRule(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "",
            scope=PolicyScope(
                org_id=row["org_id"],
                team_id=row["team_id"],
                project_id=row["project_id"],
                user_id=row["user_id"],
            ),
            priority=row["priority"],
            tier_overrides=tier_overrides or {},
            blocked_models=blocked_models or [],
            allowed_models=allowed_models or [],
            max_tier=max_tier,
            force_model=row["force_model"],
            allow_override=bool(row["allow_override"]),
            enabled=bool(row["enabled"]),
        )

    @staticmethod
    def _row_to_key(row: Any) -> HarnessKey:
        """Convert a SQLAlchemy row mapping to a HarnessKey."""
        allowed = row["allowed_models"]
        if isinstance(allowed, str):
            import json
            allowed = json.loads(allowed)
        return HarnessKey(
            id=row["id"],
            key_hash=row["key_hash"],
            key_suffix=row["key_suffix"],
            name=row["name"] or "",
            user_id=row["user_id"],
            org_id=row["org_id"],
            team_id=row["team_id"],
            project_id=row["project_id"],
            allowed_models=allowed or [],
            max_budget_daily_usd=row["max_budget_daily_usd"],
            max_budget_monthly_usd=row["max_budget_monthly_usd"],
            enabled=bool(row["enabled"]),
            created_at=_from_dt(row["created_at"]),
            expires_at=(
                _from_dt(row["expires_at"]) if row["expires_at"] else None
            ),
        )

    @staticmethod
    def _row_to_spend(row: Any) -> SpendRecord:
        """Convert a SQLAlchemy row mapping to a SpendRecord."""
        return SpendRecord(
            id=row["id"],
            timestamp=_from_dt(row["timestamp"]),
            user_id=row["user_id"] or "",
            team_id=row["team_id"],
            project_id=row["project_id"],
            org_id=row["org_id"] or "default",
            model_requested=row["model_requested"] or "",
            model_used=row["model_used"] or "",
            complexity_tier=row["complexity_tier"] or "",
            input_tokens=int(row["input_tokens"] or 0),
            output_tokens=int(row["output_tokens"] or 0),
            cost_usd=float(row["cost_usd"] or 0.0),
            latency_ms=float(row["latency_ms"] or 0.0),
            policy_applied=row["policy_applied"],
            budget_action=row["budget_action"],
            key_id=row["key_id"] or "",
        )

    @staticmethod
    def _row_to_audit(row: Any) -> HarnessAuditEvent:
        """Convert a SQLAlchemy row mapping to a HarnessAuditEvent."""
        details = row["details"]
        if isinstance(details, str):
            import json
            details = json.loads(details)
        return HarnessAuditEvent(
            id=row["id"],
            timestamp=_from_dt(row["timestamp"]),
            event_type=row["event_type"],
            user_id=row["user_id"] or "",
            org_id=row["org_id"] or "default",
            details=details or {},
        )
