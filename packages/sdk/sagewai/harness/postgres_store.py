# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""PostgreSQL-backed stores for the LLM Harness.

Drop-in replacement for :class:`InMemoryHarnessStore`. Uses an asyncpg
connection pool for safe concurrent access.

Usage::

    import asyncpg
    from sagewai.harness.postgres_store import PostgresHarnessStore

    pool = await asyncpg.create_pool("postgresql://user:pass@host/db")
    store = PostgresHarnessStore(pool)
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from typing import Any

from sagewai.harness.models import (
    HarnessAuditEvent,
    HarnessIdentity,
    HarnessKey,
    PolicyRule,
    SpendRecord,
)

logger = logging.getLogger(__name__)

_KEY_PREFIX = "sk-harness-"
_KEY_LENGTH = 32


class PostgresHarnessStore:
    """PostgreSQL-backed implementation of all harness stores.

    Expects an already-initialised :class:`asyncpg.Pool`. Tables must
    exist (created by Alembic migration).

    Tables used:
        - ``harness_policies``
        - ``harness_keys``
        - ``harness_spend``
        - ``harness_audit``
    """

    def __init__(self, pool: Any) -> None:
        self._pool = pool

    # ------------------------------------------------------------------
    # Policy Store
    # ------------------------------------------------------------------

    async def list_policies(
        self, *, org_id: str | None = None
    ) -> list[PolicyRule]:
        """List all policies, optionally filtered by org."""
        if org_id:
            rows = await self._pool.fetch(
                "SELECT id, data FROM harness_policies"
                " WHERE data->'scope'->>'org_id' IS NULL"
                " OR data->'scope'->>'org_id' = $1"
                " ORDER BY (data->>'priority')::int DESC",
                org_id,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT id, data FROM harness_policies"
                " ORDER BY (data->>'priority')::int DESC",
            )
        return [PolicyRule(**json.loads(r["data"])) for r in rows]

    async def get_policy(self, policy_id: str) -> PolicyRule | None:
        """Get a policy by ID."""
        row = await self._pool.fetchrow(
            "SELECT data FROM harness_policies WHERE id = $1",
            policy_id,
        )
        if row is None:
            return None
        return PolicyRule(**json.loads(row["data"]))

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Create a new policy."""
        await self._pool.execute(
            "INSERT INTO harness_policies (id, data) VALUES ($1, $2)",
            rule.id,
            json.dumps(rule.model_dump()),
        )
        return rule

    async def update_policy(
        self, policy_id: str, rule: PolicyRule
    ) -> PolicyRule | None:
        """Update an existing policy."""
        rule.id = policy_id
        result = await self._pool.execute(
            "UPDATE harness_policies SET data = $1 WHERE id = $2",
            json.dumps(rule.model_dump()),
            policy_id,
        )
        if result == "UPDATE 0":
            return None
        return rule

    async def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy."""
        result = await self._pool.execute(
            "DELETE FROM harness_policies WHERE id = $1",
            policy_id,
        )
        return result != "DELETE 0"

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

        await self._pool.execute(
            "INSERT INTO harness_keys"
            " (id, key_hash, key_suffix, name, user_id, org_id,"
            "  team_id, project_id, allowed_models, max_budget_daily_usd,"
            "  max_budget_monthly_usd, enabled, created_at, expires_at)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,"
            "  $10, $11, $12, $13, $14)",
            key.id,
            key.key_hash,
            key.key_suffix,
            key.name,
            key.user_id,
            key.org_id,
            key.team_id,
            key.project_id,
            json.dumps(key.allowed_models),
            key.max_budget_daily_usd,
            key.max_budget_monthly_usd,
            key.enabled,
            key.created_at,
            key.expires_at,
        )
        return plaintext

    async def validate_key(self, plaintext: str) -> HarnessIdentity | None:
        """Validate a plaintext key and return the associated identity.

        Returns None if the key is invalid, disabled, or expired.
        """
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        row = await self._pool.fetchrow(
            "SELECT id, key_hash, key_suffix, name, user_id, org_id,"
            " team_id, project_id, allowed_models,"
            " max_budget_daily_usd, max_budget_monthly_usd,"
            " enabled, created_at, expires_at"
            " FROM harness_keys WHERE key_hash = $1",
            key_hash,
        )
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
        if org_id:
            rows = await self._pool.fetch(
                "SELECT id, key_hash, key_suffix, name, user_id,"
                " org_id, team_id, project_id, allowed_models,"
                " max_budget_daily_usd, max_budget_monthly_usd,"
                " enabled, created_at, expires_at"
                " FROM harness_keys WHERE org_id = $1"
                " ORDER BY created_at DESC",
                org_id,
            )
        else:
            rows = await self._pool.fetch(
                "SELECT id, key_hash, key_suffix, name, user_id,"
                " org_id, team_id, project_id, allowed_models,"
                " max_budget_daily_usd, max_budget_monthly_usd,"
                " enabled, created_at, expires_at"
                " FROM harness_keys ORDER BY created_at DESC",
            )
        return [self._row_to_key(r) for r in rows]

    async def get_key(self, key_id: str) -> HarnessKey | None:
        """Get a key by ID."""
        row = await self._pool.fetchrow(
            "SELECT id, key_hash, key_suffix, name, user_id,"
            " org_id, team_id, project_id, allowed_models,"
            " max_budget_daily_usd, max_budget_monthly_usd,"
            " enabled, created_at, expires_at"
            " FROM harness_keys WHERE id = $1",
            key_id,
        )
        if row is None:
            return None
        return self._row_to_key(row)

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke (disable) a key."""
        result = await self._pool.execute(
            "UPDATE harness_keys SET enabled = false WHERE id = $1",
            key_id,
        )
        return result != "UPDATE 0"

    async def delete_key(self, key_id: str) -> bool:
        """Delete a key."""
        result = await self._pool.execute(
            "DELETE FROM harness_keys WHERE id = $1",
            key_id,
        )
        return result != "DELETE 0"

    # ------------------------------------------------------------------
    # Spend Store
    # ------------------------------------------------------------------

    async def record_spend(self, record: SpendRecord) -> None:
        """Record a spend event."""
        await self._pool.execute(
            "INSERT INTO harness_spend"
            " (id, timestamp, user_id, team_id, project_id, org_id,"
            "  model_requested, model_used, complexity_tier,"
            "  input_tokens, output_tokens, cost_usd, latency_ms,"
            "  policy_applied, budget_action, key_id)"
            " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9,"
            "  $10, $11, $12, $13, $14, $15, $16)",
            record.id,
            record.timestamp,
            record.user_id,
            record.team_id,
            record.project_id,
            record.org_id,
            record.model_requested,
            record.model_used,
            record.complexity_tier,
            record.input_tokens,
            record.output_tokens,
            record.cost_usd,
            record.latency_ms,
            record.policy_applied,
            record.budget_action,
            record.key_id,
        )

    async def get_spend(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[SpendRecord]:
        """Query spend records with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if org_id:
            conditions.append(f"org_id = ${idx}")
            params.append(org_id)
            idx += 1
        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1
        if since:
            conditions.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        limit_clause = f" LIMIT ${idx}"
        params.append(limit)

        query = (
            "SELECT id, timestamp, user_id, team_id, project_id,"
            " org_id, model_requested, model_used, complexity_tier,"
            " input_tokens, output_tokens, cost_usd, latency_ms,"
            " policy_applied, budget_action, key_id"
            f" FROM harness_spend{where}"
            f" ORDER BY timestamp DESC{limit_clause}"
        )
        rows = await self._pool.fetch(query, *params)
        return [self._row_to_spend(r) for r in rows]

    async def get_spend_summary(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get aggregated spend summary."""
        now = time.time()
        day_start = now - (now % 86400)
        month_start = now - (30 * 86400)

        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if org_id:
            conditions.append(f"org_id = ${idx}")
            params.append(org_id)
            idx += 1
        if user_id:
            conditions.append(f"user_id = ${idx}")
            params.append(user_id)
            idx += 1

        extra_where = (
            " AND " + " AND ".join(conditions)
        ) if conditions else ""

        # Daily
        daily_row = await self._pool.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost,"
            " COUNT(*) AS cnt"
            f" FROM harness_spend WHERE timestamp >= ${idx}{extra_where}",
            *params,
            day_start,
        )
        # Monthly
        monthly_row = await self._pool.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost,"
            " COUNT(*) AS cnt"
            f" FROM harness_spend WHERE timestamp >= ${idx}{extra_where}",
            *params,
            month_start,
        )
        # Total
        total_where = (
            " WHERE " + " AND ".join(conditions)
        ) if conditions else ""
        total_row = await self._pool.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0) AS cost,"
            " COUNT(*) AS cnt"
            f" FROM harness_spend{total_where}",
            *params,
        )

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
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if org_id:
            conditions.append(f"org_id = ${idx}")
            params.append(org_id)
            idx += 1
        if since:
            conditions.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (
            "SELECT model_used,"
            " COALESCE(SUM(cost_usd), 0) AS cost,"
            " COUNT(*) AS cnt,"
            " COALESCE(SUM(input_tokens), 0) AS inp,"
            " COALESCE(SUM(output_tokens), 0) AS outp"
            f" FROM harness_spend{where}"
            " GROUP BY model_used"
        )
        rows = await self._pool.fetch(query, *params)

        by_model: dict[str, dict[str, Any]] = {}
        for r in rows:
            by_model[r["model_used"]] = {
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
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if org_id:
            conditions.append(f"org_id = ${idx}")
            params.append(org_id)
            idx += 1
        if since:
            conditions.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        query = (
            "SELECT user_id,"
            " COALESCE(SUM(cost_usd), 0) AS cost,"
            " COUNT(*) AS cnt"
            f" FROM harness_spend{where}"
            " GROUP BY user_id"
        )
        rows = await self._pool.fetch(query, *params)

        by_user: dict[str, dict[str, Any]] = {}
        for r in rows:
            by_user[r["user_id"]] = {
                "cost_usd": float(r["cost"]),
                "requests": int(r["cnt"]),
            }
        return by_user

    # ------------------------------------------------------------------
    # Audit Store
    # ------------------------------------------------------------------

    async def record_audit(self, event: HarnessAuditEvent) -> None:
        """Record an audit event."""
        await self._pool.execute(
            "INSERT INTO harness_audit"
            " (id, timestamp, event_type, user_id, org_id, details)"
            " VALUES ($1, $2, $3, $4, $5, $6)",
            event.id,
            event.timestamp,
            event.event_type,
            event.user_id,
            event.org_id,
            json.dumps(event.details),
        )

    async def get_audit(
        self,
        *,
        org_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[HarnessAuditEvent]:
        """Query audit events with optional filters."""
        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if org_id:
            conditions.append(f"org_id = ${idx}")
            params.append(org_id)
            idx += 1
        if event_type:
            conditions.append(f"event_type = ${idx}")
            params.append(event_type)
            idx += 1
        if since:
            conditions.append(f"timestamp >= ${idx}")
            params.append(since)
            idx += 1

        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        limit_clause = f" LIMIT ${idx}"
        params.append(limit)

        query = (
            "SELECT id, timestamp, event_type, user_id, org_id, details"
            f" FROM harness_audit{where}"
            f" ORDER BY timestamp DESC{limit_clause}"
        )
        rows = await self._pool.fetch(query, *params)
        return [
            HarnessAuditEvent(
                id=r["id"],
                timestamp=float(r["timestamp"]),
                event_type=r["event_type"],
                user_id=r["user_id"],
                org_id=r["org_id"],
                details=json.loads(r["details"])
                if isinstance(r["details"], str)
                else r["details"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_key(row: Any) -> HarnessKey:
        """Convert an asyncpg Row to a HarnessKey."""
        allowed = row["allowed_models"]
        if isinstance(allowed, str):
            allowed = json.loads(allowed)
        return HarnessKey(
            id=row["id"],
            key_hash=row["key_hash"],
            key_suffix=row["key_suffix"],
            name=row["name"],
            user_id=row["user_id"],
            org_id=row["org_id"],
            team_id=row["team_id"],
            project_id=row["project_id"],
            allowed_models=allowed or [],
            max_budget_daily_usd=row["max_budget_daily_usd"],
            max_budget_monthly_usd=row["max_budget_monthly_usd"],
            enabled=row["enabled"],
            created_at=float(row["created_at"]),
            expires_at=(
                float(row["expires_at"]) if row["expires_at"] else None
            ),
        )

    @staticmethod
    def _row_to_spend(row: Any) -> SpendRecord:
        """Convert an asyncpg Row to a SpendRecord."""
        return SpendRecord(
            id=row["id"],
            timestamp=float(row["timestamp"]),
            user_id=row["user_id"],
            team_id=row["team_id"],
            project_id=row["project_id"],
            org_id=row["org_id"],
            model_requested=row["model_requested"],
            model_used=row["model_used"],
            complexity_tier=row["complexity_tier"],
            input_tokens=int(row["input_tokens"]),
            output_tokens=int(row["output_tokens"]),
            cost_usd=float(row["cost_usd"]),
            latency_ms=float(row["latency_ms"]),
            policy_applied=row["policy_applied"],
            budget_action=row["budget_action"],
            key_id=row["key_id"],
        )
