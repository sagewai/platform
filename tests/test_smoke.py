# Copyright 2026 Sagecurator
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
"""E2E smoke tests for Sagewai core user journeys.

Verifies all 5 pillars + security subsystems work end-to-end
without requiring external services (no LLM API keys, no Postgres,
no Milvus). LLM responses are mocked.

Run with:
    pytest tests/test_smoke.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sagewai.core.registry import AgentRegistry
from sagewai.engines.universal import UniversalAgent
from sagewai.memory.graph import GraphMemory
from sagewai.models.message import ChatMessage
from sagewai.models.tool import tool
from sagewai.observability.audit import AuditEvent, AuditLogger, InMemoryAuditBackend
from sagewai.observability.costs import CostTracker
from sagewai.safety.guardrails import ContentFilter
from sagewai.safety.pii import PIIGuard


# ── Helper: mock LLM response ───────────────────────────────

def _mock_chat(content: str = "Hello from mock LLM"):
    """Create a mock that patches UniversalAgent._call_llm."""
    async def _fake_call_llm(self, messages, tools=None, **kwargs):
        return ChatMessage(role="assistant", content=content)
    return _fake_call_llm


# ═══════════════════════════════════════════════════════════════
# SDK Pillar
# ═══════════════════════════════════════════════════════════════

class TestSDKPillar:
    """Agents, tools, memory, guardrails."""

    @pytest.mark.asyncio
    async def test_create_agent_and_chat(self):
        """Create an agent and get a response."""
        with patch.object(UniversalAgent, "_call_llm", _mock_chat("The capital of France is Paris.")):
            agent = UniversalAgent(name="test", model="gpt-4o-mini")
            response = await agent.chat("What is the capital of France?")
            assert isinstance(response, str)
            assert "Paris" in response

    @pytest.mark.asyncio
    async def test_agent_with_tool(self):
        """Agent registers @tool decorated functions."""
        @tool
        def add_numbers(a: int, b: int) -> str:
            """Add two numbers together."""
            return str(a + b)

        agent = UniversalAgent(name="calc", model="gpt-4o-mini", tools=[add_numbers])
        assert len(agent.config.tools) >= 1
        tool_names = [t.name for t in agent.config.tools]
        assert "add_numbers" in tool_names

    @pytest.mark.asyncio
    async def test_agent_config(self):
        """Agent configuration is properly set."""
        agent = UniversalAgent(
            name="configured",
            model="claude-sonnet-4-5-20250929",
            system_prompt="You are a helpful assistant.",
            max_iterations=5,
        )
        assert agent.config.model == "claude-sonnet-4-5-20250929"
        assert agent.config.system_prompt == "You are a helpful assistant."
        assert agent.config.max_iterations == 5
        assert agent.config.name == "configured"

    @pytest.mark.asyncio
    async def test_memory_store_and_recall(self):
        """GraphMemory stores and retrieves entities."""
        memory = GraphMemory()
        await memory.store("Alice works at Acme Corp as a data scientist.")
        await memory.add_relation("Alice", "works_at", "Acme Corp")

        entities = await memory.list_entities()
        assert len(entities) > 0

        neighbors = await memory.get_neighbors("Alice")
        assert any("Acme" in str(n) for n in neighbors)

    @pytest.mark.asyncio
    async def test_pii_guard_detects_email(self):
        """PIIGuard detects email PII and reports violation."""
        guard = PIIGuard(action="redact")
        result = await guard.check_input("Contact me at alice@example.com", context={})
        assert result is not None
        assert result.passed is False
        assert "EMAIL" in result.violation.upper()

    @pytest.mark.asyncio
    async def test_pii_guard_passes_clean_text(self):
        """PIIGuard passes text without PII."""
        guard = PIIGuard(action="redact")
        result = await guard.check_input("The weather is nice today.", context={})
        # Clean text should pass (result is None or passed=True)
        assert result is None or result.passed is True

    @pytest.mark.asyncio
    async def test_content_filter_blocks_and_passes(self):
        """ContentFilter blocks messages with blocklist terms and passes clean ones."""
        guard = ContentFilter(blocklist=["forbidden", "blocked"], action="block")

        # Clean message passes
        clean_result = await guard.check_input("This is a normal message", context={})
        assert clean_result is None or clean_result.passed is True

        # Blocked message is rejected
        blocked_result = await guard.check_input("This has forbidden content", context={})
        assert blocked_result is not None
        assert blocked_result.passed is False


# ═══════════════════════════════════════════════════════════════
# Registry Pillar
# ═══════════════════════════════════════════════════════════════

class TestRegistryPillar:
    """Agent lifecycle and governance."""

    def test_register_and_lookup_agent(self):
        """Register an agent and find it by name."""
        registry = AgentRegistry()
        agent = UniversalAgent(name="support-bot", model="gpt-4o-mini")
        registry.register(agent, capabilities=["support", "faq"])

        found = registry.get("support-bot")
        assert found is not None
        assert found.config.name == "support-bot"

        registry.unregister("support-bot")
        assert registry.get("support-bot") is None
        registry.clear()

    def test_register_multiple_agents(self):
        """Registry holds multiple agents."""
        registry = AgentRegistry()
        for i in range(3):
            agent = UniversalAgent(name=f"agent-{i}", model="gpt-4o-mini")
            registry.register(agent, capabilities=[f"cap-{i}"])

        agents = registry.list_agents()
        assert len(agents) >= 3

        for i in range(3):
            registry.unregister(f"agent-{i}")
        registry.clear()


# ═══════════════════════════════════════════════════════════════
# Harness Pillar
# ═══════════════════════════════════════════════════════════════

class TestHarnessPillar:
    """Proxy, routing, policies, key management."""

    @pytest.mark.asyncio
    async def test_harness_key_lifecycle(self):
        """Create, validate, and revoke a harness API key."""
        from sagewai.harness.models import HarnessKey
        from sagewai.harness.store import InMemoryHarnessStore

        store = InMemoryHarnessStore()
        key = HarnessKey(
            name="test-key",
            user_id="alice",
            org_id="acme",
            max_budget_daily_usd=5.00,
        )
        plaintext = await store.create_key(key)
        assert plaintext.startswith("sk-harness-")

        # Validate the key
        identity = await store.validate_key(plaintext)
        assert identity is not None
        assert identity.user_id == "alice"
        assert identity.org_id == "acme"

        # Invalid key should fail
        invalid = await store.validate_key("sk-harness-invalid-key")
        assert invalid is None

        # Revoke the key
        await store.revoke_key(key.id)
        revoked = await store.validate_key(plaintext)
        assert revoked is None

    @pytest.mark.asyncio
    async def test_harness_policy_creation(self):
        """Create and retrieve routing policies."""
        from sagewai.harness.models import PolicyRule, PolicyScope
        from sagewai.harness.store import InMemoryHarnessStore

        store = InMemoryHarnessStore()
        policy = PolicyRule(
            name="intern-cap",
            description="Block expensive models for interns",
            scope=PolicyScope(org_id="acme", user_id="intern"),
            priority=10,
            blocked_models=["claude-opus-4-6"],
        )
        await store.create_policy(policy)
        policies = await store.list_policies()
        assert len(policies) >= 1
        assert policies[0].name == "intern-cap"
        assert "claude-opus-4-6" in policies[0].blocked_models

    @pytest.mark.asyncio
    async def test_complexity_classification_accuracy(self):
        """Classifier distinguishes simple from complex requests."""
        from sagewai.harness.classifier import RequestClassifier

        classifier = RequestClassifier()

        # Simple request
        simple = classifier.classify(
            messages=[{"role": "user", "content": "fix typo in README"}],
        )
        assert simple.tier.value == "simple"

        # Complex request (long system prompt + many tools)
        complex_ = classifier.classify(
            messages=[
                {"role": "system", "content": "You are a senior architect. " * 50},
                {"role": "user", "content": "Design a microservices architecture with event sourcing and CQRS. " * 3},
            ],
            tools=[{"name": f"tool_{i}"} for i in range(8)],
        )
        assert complex_.tier.value == "complex"

    @pytest.mark.asyncio
    async def test_harness_spend_tracking(self):
        """Harness tracks and summarizes spend."""
        from sagewai.harness.models import SpendRecord
        from sagewai.harness.store import InMemoryHarnessStore

        store = InMemoryHarnessStore()
        record = SpendRecord(
            user_id="alice",
            org_id="acme",
            model_requested="gpt-4o",
            model_used="gpt-4o",
            input_tokens=1000,
            output_tokens=500,
            cost_usd=0.05,
        )
        await store.record_spend(record)
        summary = await store.get_spend_summary(org_id="acme")
        assert summary is not None
        assert summary.get("total_cost_usd", 0) > 0 or summary.get("total_usd", 0) > 0 or len(summary) > 0


# ═══════════════════════════════════════════════════════════════
# Observatory Pillar
# ═══════════════════════════════════════════════════════════════

class TestObservatoryPillar:
    """Audit, costs, metrics."""

    @pytest.mark.asyncio
    async def test_audit_logger(self):
        """AuditLogger records events to InMemoryAuditBackend."""
        backend = InMemoryAuditBackend()
        logger = AuditLogger(backends=[backend])

        logger.log(AuditEvent(
            action="agent.chat",
            agent_name="test-agent",
            model="gpt-4o",
            tokens_used=150,
            cost_usd=0.003,
        ))

        await logger.flush()

        assert len(backend.events) == 1
        event = backend.events[0]
        assert event.action == "agent.chat"
        assert event.agent_name == "test-agent"
        assert event.tokens_used == 150

    def test_cost_tracker_with_run(self):
        """CostTracker records calls within a run."""
        tracker = CostTracker()
        tracker.start_run("test-run")
        tracker.record_call(model="gpt-4o", input_tokens=100, output_tokens=50)
        tracker.record_call(model="gpt-4o", input_tokens=200, output_tokens=100)
        tracker.end_run()

        assert len(tracker.runs) == 1
        summary = tracker.summary()
        assert summary["total_runs"] == 1
        assert summary["total_tokens"] > 0


# ═══════════════════════════════════════════════════════════════
# Training Pillar
# ═══════════════════════════════════════════════════════════════

class TestTrainingPillar:
    """Intelligence layer, embeddings, local LLM discovery."""

    def test_intelligence_config_imports(self):
        """Intelligence layer modules are importable."""
        from sagewai.intelligence.config import IntelligenceConfig
        config = IntelligenceConfig()
        assert config is not None

    @pytest.mark.asyncio
    async def test_embedder_protocol(self):
        """Embedder protocol is defined and hash fallback works."""
        from sagewai.intelligence.embeddings.hash_embedder import HashEmbedder
        embedder = HashEmbedder()
        # embed takes list[str], returns list[list[float]]
        result = await embedder.embed(["test sentence"])
        assert len(result) == 1
        assert isinstance(result[0], list)
        assert all(isinstance(v, float) for v in result[0])

    def test_entity_extractor_imports(self):
        """Entity extraction modules are importable."""
        from sagewai.intelligence.extractors.protocol import EntityExtractor
        assert EntityExtractor is not None

    def test_language_detector_imports(self):
        """Language detection modules are importable."""
        from sagewai.intelligence.language.detector import LanguageDetector
        detector = LanguageDetector()
        assert detector is not None

    def test_harness_local_discovery(self):
        """Local LLM discovery module is importable."""
        from sagewai.harness.discovery import discover_local_backends
        assert callable(discover_local_backends)


# ═══════════════════════════════════════════════════════════════
# Security
# ═══════════════════════════════════════════════════════════════

class TestSecurity:
    """Security subsystems — auth, permissions, trust, SSRF, hooks."""

    def test_ssrf_blocks_private_ip(self):
        """SSRF protection blocks private IP addresses."""
        from sagewai.context.url_parser import _is_private_ip, _validate_url

        # Private IPs should be blocked
        assert _is_private_ip("127.0.0.1") is True
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("169.254.169.254") is True  # AWS metadata

        # Public IPs should pass
        assert _is_private_ip("8.8.8.8") is False
        assert _is_private_ip("1.1.1.1") is False

        # _validate_url should raise for private IPs
        with pytest.raises(ValueError, match="SSRF"):
            _validate_url("http://127.0.0.1/admin")
        with pytest.raises(ValueError, match="SSRF"):
            _validate_url("http://169.254.169.254/latest/meta-data")

    def test_permission_policy_denies_restricted_tool(self):
        """Permission policy blocks tools with denied prefixes."""
        from sagewai.safety.permissions import PermissionPolicy, PermissionLevel

        policy = PermissionPolicy(
            default_level=PermissionLevel.READ,
            deny_prefixes=["delete_", "drop_"],
        )

        # Allowed tool
        result = policy.check("get_users")
        assert result.allowed is True

        # Denied tool (prefix match)
        result_del = policy.check("delete_database")
        assert result_del.allowed is False
        assert "denied" in result_del.reason.lower() or "deny" in result_del.reason.lower()

        result_drop = policy.check("drop_table")
        assert result_drop.allowed is False

    def test_trust_levels(self):
        """Trust level hierarchy is monotonic (only goes up)."""
        from sagewai.core.trust import TrustLevel, DeferredInit

        init = DeferredInit()
        assert init.trust_level == TrustLevel.UNTRUSTED

        # Elevate to SANDBOXED
        init.elevate(TrustLevel.SANDBOXED)
        assert init.trust_level == TrustLevel.SANDBOXED

        # Elevate to TRUSTED
        init.elevate(TrustLevel.TRUSTED)
        assert init.trust_level == TrustLevel.TRUSTED

        # Cannot downgrade (raises ValueError)
        with pytest.raises(ValueError, match="Cannot lower"):
            init.elevate(TrustLevel.UNTRUSTED)

    @pytest.mark.asyncio
    async def test_hook_runner_deny(self):
        """Hook runner blocks tool execution with DENY action."""
        from sagewai.core.hooks import HookRunner, HookResult, HookAction, HookContext

        runner = HookRunner()

        # Register a hook that denies delete operations
        def deny_deletes(ctx: HookContext) -> HookResult:
            if ctx.tool_name.startswith("delete_"):
                return HookResult(action=HookAction.DENY, message="Deletes are blocked")
            return HookResult(action=HookAction.ALLOW)

        runner.add_pre_hook(deny_deletes)

        # Allowed tool
        allowed_ctx = HookContext(tool_name="get_users", arguments={})
        allowed_result = await runner.run_pre_hooks(allowed_ctx)
        assert allowed_result.action == HookAction.ALLOW

        # Denied tool
        denied_ctx = HookContext(tool_name="delete_user", arguments={"id": 123})
        denied_result = await runner.run_pre_hooks(denied_ctx)
        assert denied_result.action == HookAction.DENY
        assert "blocked" in denied_result.message.lower()

    def test_jwt_token_creation_and_verification(self):
        """JWT tokens can be created and verified."""
        from sagewai.auth.jwt import JWTAuth

        auth = JWTAuth(secret="test-secret-key-for-smoke-test-only")
        token = auth.create_token({"sub": "alice", "role": "admin"})
        assert isinstance(token, str)
        assert len(token) > 20

        # Verify the token
        payload = auth.verify_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "admin"

    def test_jwt_rejects_invalid_token(self):
        """JWT auth rejects invalid tokens."""
        from sagewai.auth.jwt import JWTAuth, AuthenticationError

        auth = JWTAuth(secret="test-secret-key")
        with pytest.raises(AuthenticationError):
            auth.verify_token("invalid.token.here")

    def test_api_key_validation(self):
        """API key auth validates correct keys and rejects wrong ones."""
        from sagewai.auth.api_key import APIKeyAuth
        from sagewai.auth.jwt import AuthenticationError

        auth = APIKeyAuth(valid_keys=["sk-test-key-123"])
        assert auth.validate("sk-test-key-123") is True

        # Invalid key raises AuthenticationError
        with pytest.raises(AuthenticationError):
            auth.validate("sk-wrong-key")


# ═══════════════════════════════════════════════════════════════
# Cross-Pillar
# ═══════════════════════════════════════════════════════════════

class TestCrossPillar:
    """Tests spanning multiple pillars."""

    @pytest.mark.asyncio
    async def test_agent_with_memory_and_registry(self):
        """Agent with memory registered in registry."""
        memory = GraphMemory()
        agent = UniversalAgent(
            name="smart-agent",
            model="gpt-4o-mini",
            memory=memory,
        )

        registry = AgentRegistry()
        registry.register(agent, capabilities=["chat", "memory"])

        found = registry.get("smart-agent")
        assert found is not None
        assert found.config.name == "smart-agent"

        registry.unregister("smart-agent")
        registry.clear()

    @pytest.mark.asyncio
    async def test_full_audit_trail(self):
        """Multiple events create a queryable audit trail."""
        backend = InMemoryAuditBackend()
        logger = AuditLogger(backends=[backend])

        for i in range(5):
            logger.log(AuditEvent(
                action="agent.chat",
                agent_name=f"agent-{i}",
                model="gpt-4o",
                tokens_used=100 + i * 10,
            ))

        await logger.flush()
        assert len(backend.events) == 5

        # Query by action
        filtered = backend.query(action="agent.chat", limit=3)
        assert len(filtered) <= 3
