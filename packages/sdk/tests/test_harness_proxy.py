# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the LLM Harness router, budget, and proxy modules."""

from __future__ import annotations

import pytest

from sagewai.admin.budget import BudgetManager
from sagewai.harness.budget import HarnessBudgetManager, HarnessBudgetResult
from sagewai.harness.classifier import ComplexityTier, RequestClassifier
from sagewai.harness.models import (
    HarnessConfig,
    HarnessIdentity,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
    RoutingDecision,
)
from sagewai.harness.policy import PolicyEngine
from sagewai.harness.proxy import HarnessProxy
from sagewai.harness.router import HarnessRouter
from sagewai.harness.store import InMemoryHarnessStore


@pytest.fixture
def budget_manager() -> BudgetManager:
    return BudgetManager()


@pytest.fixture
def harness_budget(budget_manager: BudgetManager) -> HarnessBudgetManager:
    return HarnessBudgetManager(budget_manager)


@pytest.fixture
def store() -> InMemoryHarnessStore:
    return InMemoryHarnessStore()


@pytest.fixture
def tier_config() -> ModelTierConfig:
    return ModelTierConfig(simple="haiku", medium="sonnet", complex="opus")


@pytest.fixture
def identity() -> HarnessIdentity:
    return HarnessIdentity(
        key_id="k1",
        user_id="alice",
        org_id="acme",
        team_id="eng",
        project_id="frontend",
    )


class TestHarnessBudgetManager:
    """Test the multi-scope budget wrapper."""

    def test_configure_and_check_user_budget(
        self, harness_budget: HarnessBudgetManager
    ) -> None:
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=1.0, max_monthly_usd=10.0
        )
        result = harness_budget.check_budget(user_id="alice")
        assert result.allowed

    def test_user_budget_exceeded(
        self, harness_budget: HarnessBudgetManager
    ) -> None:
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=0.05, max_monthly_usd=1.0, action="stop"
        )
        harness_budget.record_spend(user_id="alice", cost_usd=0.06)
        result = harness_budget.check_budget(user_id="alice")
        assert not result.allowed
        assert result.action == "stop"

    def test_team_budget_recorded(
        self, harness_budget: HarnessBudgetManager
    ) -> None:
        harness_budget.configure_team_budget(
            "eng", max_daily_usd=1.0, max_monthly_usd=10.0, action="warn"
        )
        harness_budget.record_spend(
            user_id="alice", team_id="eng", cost_usd=0.50
        )
        result = harness_budget.check_budget(user_id="alice", team_id="eng")
        assert result.allowed

    def test_most_restrictive_scope_wins(
        self, harness_budget: HarnessBudgetManager
    ) -> None:
        """If user budget is OK but team budget is exceeded, result is denied."""
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=10.0, max_monthly_usd=100.0
        )
        harness_budget.configure_team_budget(
            "eng", max_daily_usd=0.01, max_monthly_usd=0.1, action="stop"
        )
        harness_budget.record_spend(
            user_id="alice", team_id="eng", cost_usd=0.02
        )
        result = harness_budget.check_budget(user_id="alice", team_id="eng")
        assert not result.allowed
        assert result.action == "stop"

    def test_get_budget_status(
        self, harness_budget: HarnessBudgetManager
    ) -> None:
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=5.0, max_monthly_usd=50.0
        )
        harness_budget.record_spend(user_id="alice", cost_usd=1.0)
        status = harness_budget.get_budget_status(user_id="alice")
        assert status["allowed"] is True
        assert "scopes" in status

    @pytest.mark.asyncio
    async def test_check_identity_adapter(
        self, harness_budget: HarnessBudgetManager, identity: HarnessIdentity
    ) -> None:
        """The async check() method should accept an identity object."""
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=5.0, max_monthly_usd=50.0
        )
        result = await harness_budget.check(identity)
        assert isinstance(result, HarnessBudgetResult)
        assert not result.exceeded

    @pytest.mark.asyncio
    async def test_check_identity_exceeded(
        self, harness_budget: HarnessBudgetManager, identity: HarnessIdentity
    ) -> None:
        harness_budget.configure_user_budget(
            "alice", max_daily_usd=0.01, max_monthly_usd=0.1, action="stop"
        )
        harness_budget.record_spend(user_id="alice", cost_usd=0.02)
        result = await harness_budget.check(identity)
        assert result.exceeded
        assert result.action == "stop"


class TestHarnessRouter:
    """Test the routing decision engine."""

    @pytest.mark.asyncio
    async def test_default_classification_routing(
        self,
        store: InMemoryHarnessStore,
        harness_budget: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        identity: HarnessIdentity,
    ) -> None:
        """Without policies or budget, routes based on classification."""
        router = HarnessRouter(
            classifier=RequestClassifier(),
            policy_engine=PolicyEngine(store=store),
            budget_manager=harness_budget,
            tier_config=tier_config,
        )
        decision = await router.route(
            identity=identity,
            messages=[{"role": "user", "content": "fix typo"}],
        )
        assert isinstance(decision, RoutingDecision)
        assert decision.tier == ComplexityTier.SIMPLE
        assert decision.target_model == "haiku"

    @pytest.mark.asyncio
    async def test_policy_overrides_classification(
        self,
        store: InMemoryHarnessStore,
        harness_budget: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        identity: HarnessIdentity,
    ) -> None:
        """Policy should override the default classification."""
        await store.create_policy(PolicyRule(
            name="force-sonnet",
            scope=PolicyScope(org_id="acme"),
            force_model="sonnet",
        ))
        router = HarnessRouter(
            classifier=RequestClassifier(),
            policy_engine=PolicyEngine(store=store),
            budget_manager=harness_budget,
            tier_config=tier_config,
        )
        decision = await router.route(
            identity=identity,
            messages=[{"role": "user", "content": "fix typo"}],
        )
        assert decision.target_model == "sonnet"
        assert decision.policy_applied == "force-sonnet"

    @pytest.mark.asyncio
    async def test_budget_stop_raises(
        self,
        store: InMemoryHarnessStore,
        harness_budget: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        identity: HarnessIdentity,
    ) -> None:
        """Budget exceeded with 'stop' action should raise."""
        from sagewai.errors import SagewaiBudgetExceededError

        harness_budget.configure_user_budget(
            "alice", max_daily_usd=0.01, max_monthly_usd=0.1, action="stop"
        )
        harness_budget.record_spend(user_id="alice", cost_usd=0.02)

        router = HarnessRouter(
            classifier=RequestClassifier(),
            policy_engine=PolicyEngine(store=store),
            budget_manager=harness_budget,
            tier_config=tier_config,
        )
        with pytest.raises(SagewaiBudgetExceededError):
            await router.route(
                identity=identity,
                messages=[{"role": "user", "content": "hello"}],
            )

    @pytest.mark.asyncio
    async def test_budget_downgrade(
        self,
        store: InMemoryHarnessStore,
        harness_budget: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        identity: HarnessIdentity,
    ) -> None:
        """Budget exceeded with 'downgrade' should route to cheapest model."""
        harness_budget.configure_user_budget(
            "alice",
            max_daily_usd=0.01,
            max_monthly_usd=0.1,
            action="throttle",  # BudgetManager uses "throttle" not "downgrade"
        )
        harness_budget.record_spend(user_id="alice", cost_usd=0.02)

        router = HarnessRouter(
            classifier=RequestClassifier(),
            policy_engine=PolicyEngine(store=store),
            budget_manager=harness_budget,
            tier_config=tier_config,
        )
        decision = await router.route(
            identity=identity,
            messages=[{"role": "user", "content": "complex architecture plan"}],
        )
        # Should be downgraded to cheapest
        assert decision.target_model == "haiku"
        assert decision.budget_action == "downgrade"

    @pytest.mark.asyncio
    async def test_force_model_override(
        self,
        store: InMemoryHarnessStore,
        harness_budget: HarnessBudgetManager,
        tier_config: ModelTierConfig,
        identity: HarnessIdentity,
    ) -> None:
        """User can override model via force_model_header when policy allows."""
        router = HarnessRouter(
            classifier=RequestClassifier(),
            policy_engine=PolicyEngine(store=store),
            budget_manager=harness_budget,
            tier_config=tier_config,
            allow_override=True,
        )
        decision = await router.route(
            identity=identity,
            messages=[{"role": "user", "content": "fix typo"}],
            force_model_header="gpt-4o",
        )
        assert decision.target_model == "gpt-4o"
        assert "override" in decision.reason.lower()


class TestHarnessProxy:
    """Test the proxy orchestrator."""

    @pytest.mark.asyncio
    async def test_authenticate_valid_key(self, store: InMemoryHarnessStore) -> None:
        """Valid key should return identity."""
        key = HarnessKey(name="test", user_id="alice", org_id="acme")
        plaintext = await store.create_key(key)

        proxy = HarnessProxy(
            store=store,
            router=None,  # type: ignore[arg-type]
            backends={},
            config=HarnessConfig(),
        )
        identity = await proxy.authenticate(f"Bearer {plaintext}")
        assert identity.user_id == "alice"

    @pytest.mark.asyncio
    async def test_authenticate_invalid_key_raises(
        self, store: InMemoryHarnessStore
    ) -> None:
        from fastapi import HTTPException

        proxy = HarnessProxy(
            store=store,
            router=None,  # type: ignore[arg-type]
            backends={},
            config=HarnessConfig(),
        )
        with pytest.raises(HTTPException) as exc_info:
            await proxy.authenticate("Bearer sk-harness-invalid")
        assert exc_info.value.status_code == 401

    def test_detect_provider(self) -> None:
        proxy = HarnessProxy(
            store=InMemoryHarnessStore(),
            router=None,  # type: ignore[arg-type]
            backends={},
            config=HarnessConfig(),
        )
        assert proxy._detect_provider("claude-opus-4-6") == "anthropic"
        assert proxy._detect_provider("claude-sonnet-4-5-20250929") == "anthropic"
        assert proxy._detect_provider("gpt-4o") == "openai"
        assert proxy._detect_provider("gpt-4o-mini") == "openai"
        assert proxy._detect_provider("o3-mini") == "openai"
        assert proxy._detect_provider("gemini-2.5-flash") == "google"
        assert proxy._detect_provider("some-other-model") == "default"

    def test_estimate_cost(self) -> None:
        proxy = HarnessProxy(
            store=InMemoryHarnessStore(),
            router=None,  # type: ignore[arg-type]
            backends={},
            config=HarnessConfig(),
        )
        cost = proxy._estimate_cost("claude-opus-4-6", 1000, 500)
        # Opus: $15/M input, $75/M output
        expected = (1000 * 15.0 / 1_000_000) + (500 * 75.0 / 1_000_000)
        assert abs(cost - expected) < 0.001

    def test_build_transparency_headers(self) -> None:
        proxy = HarnessProxy(
            store=InMemoryHarnessStore(),
            router=None,  # type: ignore[arg-type]
            backends={},
            config=HarnessConfig(),
        )
        decision = RoutingDecision(
            target_model="haiku",
            tier=ComplexityTier.SIMPLE,
            original_model="opus",
            reason="test",
        )
        headers = proxy._build_transparency_headers(decision, 0.01)
        assert headers["X-Harness-Model-Used"] == "haiku"
        assert headers["X-Harness-Complexity-Tier"] == "simple"
