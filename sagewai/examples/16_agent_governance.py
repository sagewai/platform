#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 16 — Agent governance — version, approve, audit.

Demonstrates an agent lifecycle governance pattern:

1. **Register** an agent with version metadata
2. **Version** it when the prompt or model changes
3. **Approval gate** before promotion to production
4. **Audit trail** of all changes via the observability layer

This example uses in-memory stores. In production, back the
registry and audit logger with Postgres for persistence.

Requirements::

    pip install sagewai

Usage::

    python 16_agent_governance.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from sagewai.core.registry import AgentRegistry
from sagewai.engines.universal import UniversalAgent
from sagewai.observability.audit import AuditEvent, AuditLogger, InMemoryAuditBackend


async def main() -> None:
    """Walk through agent versioning and governance."""
    print("=" * 55)
    print("  Agent Governance — Version, Approve, Audit")
    print("=" * 55)
    print()

    registry = AgentRegistry()
    backend = InMemoryAuditBackend()
    audit = AuditLogger(backends=[backend])

    # ── Step 1: Register v1 of an agent ─────────────────────────
    agent_v1 = UniversalAgent(
        name="support-agent",
        model="claude-haiku-4-5-20251001",
        system_prompt="You help customers with account questions.",
    )
    registry.register(agent_v1, capabilities=["support", "customer-service"])

    audit.log(AuditEvent(
        action="agent.registered",
        agent_name="support-agent",
        metadata={"version": "1.0.0", "model": agent_v1.config.model},
    ))
    print("v1.0.0 registered: support-agent (Haiku)")
    print()

    # ── Step 2: Upgrade to v2 with a better model ───────────────
    agent_v2 = UniversalAgent(
        name="support-agent",
        model="claude-sonnet-4-5-20250929",
        system_prompt=(
            "You help customers with account questions. "
            "Be empathetic and provide step-by-step solutions."
        ),
    )

    # Simulate approval gate
    approval = {
        "approved_by": "eng-lead@acme.com",
        "approved_at": datetime.now(timezone.utc).isoformat(),
        "reason": "Upgraded to Sonnet for better response quality",
        "version_from": "1.0.0",
        "version_to": "2.0.0",
    }
    print(f"Approval: {approval['approved_by']}")
    print(f"  Reason: {approval['reason']}")
    print()

    # Replace in registry after approval
    registry.unregister("support-agent")
    registry.register(agent_v2, capabilities=["support", "customer-service"])

    audit.log(AuditEvent(
        action="agent.upgraded",
        agent_name="support-agent",
        metadata={**approval, "model": agent_v2.config.model},
    ))
    print("v2.0.0 promoted: support-agent (Sonnet)")
    print()

    # ── Step 3: Review audit trail ──────────────────────────────
    # Flush buffer to backend so we can query
    await audit.flush()
    print("Audit trail:")
    for event in backend.events:
        ts = datetime.fromtimestamp(event.timestamp, tz=timezone.utc)
        print(f"  [{ts:%H:%M:%S}] {event.action} ({event.agent_name})")
        for k, v in event.metadata.items():
            print(f"           {k}: {v}")
    print()

    # ── Step 4: Current registry state ──────────────────────────
    print("Current registry:")
    for name, caps in registry.list_agents().items():
        agent = registry.get(name)
        model = agent.config.model if agent else "unknown"
        print(f"  {name} -> model={model}, capabilities={caps}")
    print()

    print("In production, back the audit logger with Postgres")
    print("and require approval records before registry updates.")

    registry.clear()


if __name__ == "__main__":
    asyncio.run(main())
