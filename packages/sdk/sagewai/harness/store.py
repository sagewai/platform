# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""In-memory stores for the LLM Harness.

Provides storage for policies, keys, spend records, and audit events.
For production, use the Postgres-backed stores.
"""

from __future__ import annotations

import hashlib
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


class InMemoryHarnessStore:
    """In-memory implementation of all harness stores.

    Suitable for development and testing. Data is lost on restart.
    """

    def __init__(self) -> None:
        self._policies: dict[str, PolicyRule] = {}
        self._keys: dict[str, HarnessKey] = {}
        self._spend: list[SpendRecord] = []
        self._audit: list[HarnessAuditEvent] = []

    # --- Policy Store ---

    async def list_policies(self, *, org_id: str | None = None) -> list[PolicyRule]:
        """List all policies, optionally filtered by org."""
        policies = list(self._policies.values())
        if org_id:
            policies = [
                p
                for p in policies
                if p.scope.org_id is None or p.scope.org_id == org_id
            ]
        return policies

    async def get_policy(self, policy_id: str) -> PolicyRule | None:
        """Get a policy by ID."""
        return self._policies.get(policy_id)

    async def create_policy(self, rule: PolicyRule) -> PolicyRule:
        """Create a new policy."""
        self._policies[rule.id] = rule
        return rule

    async def update_policy(self, policy_id: str, rule: PolicyRule) -> PolicyRule | None:
        """Update an existing policy."""
        if policy_id not in self._policies:
            return None
        rule.id = policy_id
        self._policies[policy_id] = rule
        return rule

    async def delete_policy(self, policy_id: str) -> bool:
        """Delete a policy."""
        return self._policies.pop(policy_id, None) is not None

    # --- Key Store ---

    async def create_key(self, key: HarnessKey) -> str:
        """Create a new harness key. Returns the plaintext key (shown once).

        The key is stored as a SHA-256 hash; the plaintext is only
        returned at creation time.
        """
        plaintext = _KEY_PREFIX + secrets.token_hex(_KEY_LENGTH)
        key.key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        key.key_suffix = plaintext[-4:]
        self._keys[key.id] = key
        return plaintext

    async def validate_key(self, plaintext: str) -> HarnessIdentity | None:
        """Validate a plaintext key and return the associated identity.

        Returns None if the key is invalid, disabled, or expired.
        """
        key_hash = hashlib.sha256(plaintext.encode()).hexdigest()
        for key in self._keys.values():
            if key.key_hash == key_hash:
                if not key.enabled or key.is_expired():
                    return None
                return key.to_identity()
        return None

    async def list_keys(self, *, org_id: str | None = None) -> list[HarnessKey]:
        """List all keys, optionally filtered by org."""
        keys = list(self._keys.values())
        if org_id:
            keys = [k for k in keys if k.org_id == org_id]
        return keys

    async def get_key(self, key_id: str) -> HarnessKey | None:
        """Get a key by ID."""
        return self._keys.get(key_id)

    async def revoke_key(self, key_id: str) -> bool:
        """Revoke (disable) a key."""
        key = self._keys.get(key_id)
        if key is None:
            return False
        key.enabled = False
        return True

    async def delete_key(self, key_id: str) -> bool:
        """Delete a key."""
        return self._keys.pop(key_id, None) is not None

    # --- Spend Store ---

    async def record_spend(self, record: SpendRecord) -> None:
        """Record a spend event."""
        self._spend.append(record)

    async def get_spend(
        self,
        *,
        org_id: str | None = None,
        user_id: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[SpendRecord]:
        """Query spend records with optional filters."""
        records = self._spend
        if org_id:
            records = [r for r in records if r.org_id == org_id]
        if user_id:
            records = [r for r in records if r.user_id == user_id]
        if since:
            records = [r for r in records if r.timestamp >= since]
        # Most recent first
        records = sorted(records, key=lambda r: r.timestamp, reverse=True)
        return records[:limit]

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

        records = self._spend
        if org_id:
            records = [r for r in records if r.org_id == org_id]
        if user_id:
            records = [r for r in records if r.user_id == user_id]

        daily = [r for r in records if r.timestamp >= day_start]
        monthly = [r for r in records if r.timestamp >= month_start]

        return {
            "daily_cost_usd": sum(r.cost_usd for r in daily),
            "daily_requests": len(daily),
            "monthly_cost_usd": sum(r.cost_usd for r in monthly),
            "monthly_requests": len(monthly),
            "total_cost_usd": sum(r.cost_usd for r in records),
            "total_requests": len(records),
        }

    async def get_spend_by_model(
        self,
        *,
        org_id: str | None = None,
        since: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get spend breakdown by model."""
        records = self._spend
        if org_id:
            records = [r for r in records if r.org_id == org_id]
        if since:
            records = [r for r in records if r.timestamp >= since]

        by_model: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.model_used not in by_model:
                by_model[r.model_used] = {
                    "cost_usd": 0.0,
                    "requests": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
            entry = by_model[r.model_used]
            entry["cost_usd"] += r.cost_usd
            entry["requests"] += 1
            entry["input_tokens"] += r.input_tokens
            entry["output_tokens"] += r.output_tokens

        return by_model

    async def get_spend_by_user(
        self,
        *,
        org_id: str | None = None,
        since: float | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Get spend breakdown by user."""
        records = self._spend
        if org_id:
            records = [r for r in records if r.org_id == org_id]
        if since:
            records = [r for r in records if r.timestamp >= since]

        by_user: dict[str, dict[str, Any]] = {}
        for r in records:
            if r.user_id not in by_user:
                by_user[r.user_id] = {"cost_usd": 0.0, "requests": 0}
            entry = by_user[r.user_id]
            entry["cost_usd"] += r.cost_usd
            entry["requests"] += 1

        return by_user

    # --- Audit Store ---

    async def record_audit(self, event: HarnessAuditEvent) -> None:
        """Record an audit event."""
        self._audit.append(event)

    async def get_audit(
        self,
        *,
        org_id: str | None = None,
        event_type: str | None = None,
        since: float | None = None,
        limit: int = 100,
    ) -> list[HarnessAuditEvent]:
        """Query audit events with optional filters."""
        events = self._audit
        if org_id:
            events = [e for e in events if e.org_id == org_id]
        if event_type:
            events = [e for e in events if e.event_type == event_type]
        if since:
            events = [e for e in events if e.timestamp >= since]
        events = sorted(events, key=lambda e: e.timestamp, reverse=True)
        return events[:limit]
