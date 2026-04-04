#!/usr/bin/env python3
# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Example 06 — Enterprise Safety Built In, Not Bolted On.

Sagewai agents can enforce guardrails on both input and output:
PII detection, content filtering, and token budget limits.

This example shows PII redaction and content filtering in action.

Requirements::

    pip install sagewai

Usage::

    export OPENAI_API_KEY=sk-...
    python 06_guardrails.py
"""

from __future__ import annotations

import asyncio

from sagewai.engines.universal import UniversalAgent
from sagewai.safety.guardrails import ContentFilter
from sagewai.safety.pii import PIIGuard


async def main() -> None:
    # Set up guardrails
    pii_guard = PIIGuard(action="redact")
    content_filter = ContentFilter(
        blocked_terms=["confidential", "top secret"],
        action="block",
    )

    agent = UniversalAgent(
        name="safe-agent",
        model="gpt-4o-mini",
        guardrails=[pii_guard, content_filter],
        system_prompt="You are a helpful assistant.",
    )

    # Test 1: PII gets redacted
    print("--- Test 1: PII Redaction ---")
    try:
        response = await agent.chat(
            "My email is alice@example.com and my SSN is 123-45-6789. "
            "Can you confirm you received my info?"
        )
        print(f"  Response: {response[:200]}\n")
    except Exception as e:
        print(f"  Guardrail triggered: {e}\n")

    # Test 2: Clean message goes through
    print("--- Test 2: Clean Message ---")
    response = await agent.chat("What is the capital of France?")
    print(f"  Response: {response[:200]}\n")

    # Test 3: Blocked content
    print("--- Test 3: Content Filter ---")
    try:
        response = await agent.chat("Tell me about top secret operations.")
        print(f"  Response: {response[:200]}\n")
    except Exception as e:
        print(f"  Blocked: {e}\n")

    print("Guardrails: PII redacted, clean messages passed, blocked terms caught.")


if __name__ == "__main__":
    asyncio.run(main())
