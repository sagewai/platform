"""Tier 5: Safety, Auth & Observability — real guardrails, auth, costs.

Scenarios 22-28:
22. ContentFilter blocks prohibited input
23. TokenBudgetGuard enforces cost limits
24. OutputSchemaGuard enforces JSON
25. JWT + API key auth
26. Cost tracking across workflow
27. OTel tracing
28. Prometheus metrics
"""

from __future__ import annotations

import pytest

from sagewai.auth.api_key import APIKeyAuth
from sagewai.auth.jwt import JWTAuth
from sagewai.core.workflows import SequentialAgent
from sagewai.engines.universal import UniversalAgent
from sagewai.observability.costs import CostTracker
from sagewai.safety.guardrails import (
    ContentFilter,
    GuardrailViolationError,
    OutputSchemaGuard,
    TokenBudgetGuard,
)

# --- Scenario 22: ContentFilter ---


@pytest.mark.integration
async def test_content_filter_blocks_input():
    """ContentFilter prevents prohibited content from reaching LLM."""
    agent = UniversalAgent(
        name="filtered-agent",
        model="claude-haiku-4-5-20251001",
        guardrails=[ContentFilter(blocklist=["password", "secret_key"])],
    )
    with pytest.raises(GuardrailViolationError, match="password"):
        await agent.chat("What is my password for the admin account?")


# --- Scenario 23: TokenBudgetGuard ---


@pytest.mark.integration
async def test_budget_guard_enforcement():
    """TokenBudgetGuard halts agent when budget exceeded."""
    guard = TokenBudgetGuard(max_usd=0.001)
    # Simulate having exceeded budget
    result = await guard.check_input("test", {"cost_usd_so_far": 0.01})
    assert not result.passed
    assert "exceeded" in result.violation.lower()


# --- Scenario 24: OutputSchemaGuard ---


@pytest.mark.integration
async def test_output_schema_guard():
    """OutputSchemaGuard validates LLM JSON output."""
    guard = OutputSchemaGuard(schema={"type": "object", "required": ["name", "email"]})
    # Valid JSON
    result = await guard.check_output('{"name": "Alice", "email": "a@b.com"}', {})
    assert result.passed

    # Invalid — missing field
    result = await guard.check_output('{"name": "Alice"}', {})
    assert not result.passed
    assert "email" in result.violation.lower()

    # Not JSON at all
    result = await guard.check_output("This is plain text", {})
    assert not result.passed


# --- Scenario 25: Auth ---


@pytest.mark.integration
async def test_api_key_auth():
    """API key generation, validation, and revocation."""
    auth = APIKeyAuth()
    key = APIKeyAuth.generate_key()
    assert key.startswith("sk-sage-")

    auth.add_key(key)
    assert auth.is_valid(key)

    auth.revoke_key(key)
    assert not auth.is_valid(key)


@pytest.mark.integration
async def test_jwt_auth():
    """JWT token creation and verification."""
    jwt_auth = JWTAuth(secret="test-secret-for-validation")
    token = jwt_auth.create_token(payload={"user_id": "user-123", "role": "admin"})
    assert isinstance(token, str)

    claims = jwt_auth.verify_token(token)
    assert claims["user_id"] == "user-123"
    assert claims["role"] == "admin"


# --- Scenario 26: Cost tracking ---


@pytest.mark.integration
async def test_cost_tracking_workflow():
    """CostTracker aggregates costs across a multi-agent workflow."""
    tracker = CostTracker()

    agent1 = UniversalAgent(name="agent-1", model="claude-haiku-4-5-20251001")
    agent2 = UniversalAgent(name="agent-2", model="claude-haiku-4-5-20251001")

    # Register event hooks
    agent1.on_event(tracker.event_hook)
    agent2.on_event(tracker.event_hook)

    pipeline = SequentialAgent(name="cost-pipeline", agents=[agent1, agent2])
    await pipeline.chat("Explain recursion briefly.")

    assert tracker.total_cost > 0, "Expected non-zero cost after real LLM calls"
    assert tracker.total_tokens > 0, "Expected non-zero tokens"
    summary = tracker.summary()
    assert summary["total_runs"] > 0
    print(f"\nCost tracking: ${tracker.total_cost:.6f}, {tracker.total_tokens} tokens")
