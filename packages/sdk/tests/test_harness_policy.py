# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for the LLM Harness policy engine and store."""

from __future__ import annotations

import pytest

from sagewai.harness.models import (
    ComplexityTier,
    HarnessIdentity,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
    SpendRecord,
)
from sagewai.harness.policy import PolicyDecision, PolicyEngine
from sagewai.harness.store import InMemoryHarnessStore


@pytest.fixture
def store() -> InMemoryHarnessStore:
    return InMemoryHarnessStore()


@pytest.fixture
def tier_config() -> ModelTierConfig:
    return ModelTierConfig(
        simple="haiku",
        medium="sonnet",
        complex="opus",
    )


@pytest.fixture
def identity() -> HarnessIdentity:
    return HarnessIdentity(
        key_id="k1",
        user_id="alice",
        org_id="acme",
        team_id="engineering",
        project_id="frontend",
    )


class TestPolicyEngine:
    """Test policy resolution logic."""

    @pytest.mark.asyncio
    async def test_no_policies_returns_default(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """No policies → PolicyDecision with no model override."""
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model is None
        assert decision.allow_override is True

    @pytest.mark.asyncio
    async def test_matching_org_policy(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """Org-scoped policy should match."""
        await store.create_policy(PolicyRule(
            name="org-default",
            scope=PolicyScope(org_id="acme"),
            force_model="sonnet",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.COMPLEX, tier_config)
        assert decision.model == "sonnet"
        assert decision.policy_name == "org-default"

    @pytest.mark.asyncio
    async def test_user_scope_beats_org_scope(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """User-scoped policy should beat org-scoped."""
        await store.create_policy(PolicyRule(
            name="org-policy",
            scope=PolicyScope(org_id="acme"),
            force_model="haiku",
        ))
        await store.create_policy(PolicyRule(
            name="user-policy",
            scope=PolicyScope(org_id="acme", user_id="alice"),
            force_model="opus",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model == "opus"
        assert decision.policy_name == "user-policy"

    @pytest.mark.asyncio
    async def test_project_scope_beats_team_scope(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """Project-scoped beats team-scoped."""
        await store.create_policy(PolicyRule(
            name="team-policy",
            scope=PolicyScope(org_id="acme", team_id="engineering"),
            force_model="haiku",
        ))
        await store.create_policy(PolicyRule(
            name="project-policy",
            scope=PolicyScope(org_id="acme", project_id="frontend"),
            force_model="sonnet",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model == "sonnet"
        assert decision.policy_name == "project-policy"

    @pytest.mark.asyncio
    async def test_priority_breaks_same_specificity(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """Higher priority wins at the same scope specificity."""
        await store.create_policy(PolicyRule(
            name="low-priority",
            scope=PolicyScope(org_id="acme"),
            priority=1,
            force_model="haiku",
        ))
        await store.create_policy(PolicyRule(
            name="high-priority",
            scope=PolicyScope(org_id="acme"),
            priority=10,
            force_model="opus",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model == "opus"
        assert decision.policy_name == "high-priority"

    @pytest.mark.asyncio
    async def test_disabled_policy_skipped(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """Disabled policies should be ignored."""
        await store.create_policy(PolicyRule(
            name="disabled",
            scope=PolicyScope(org_id="acme"),
            force_model="opus",
            enabled=False,
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model is None  # No matching policy

    @pytest.mark.asyncio
    async def test_max_tier_caps_complexity(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """max_tier should cap the classified tier."""
        await store.create_policy(PolicyRule(
            name="cap-medium",
            scope=PolicyScope(org_id="acme"),
            max_tier=ComplexityTier.MEDIUM,
        ))
        engine = PolicyEngine(store=store)
        # COMPLEX request capped to MEDIUM
        decision = await engine.resolve(identity, ComplexityTier.COMPLEX, tier_config)
        assert decision.model == "sonnet"  # Medium model

    @pytest.mark.asyncio
    async def test_tier_overrides(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """tier_overrides should map specific tiers to custom models."""
        await store.create_policy(PolicyRule(
            name="custom-tiers",
            scope=PolicyScope(org_id="acme"),
            tier_overrides={"simple": "gpt-4o-mini", "complex": "gpt-4o"},
        ))
        engine = PolicyEngine(store=store)

        # Simple → custom model
        decision = await engine.resolve(identity, ComplexityTier.SIMPLE, tier_config)
        assert decision.model == "gpt-4o-mini"

        # Complex → custom model
        decision = await engine.resolve(identity, ComplexityTier.COMPLEX, tier_config)
        assert decision.model == "gpt-4o"

        # Medium → default (no override for medium)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model == "sonnet"

    @pytest.mark.asyncio
    async def test_non_matching_org_ignored(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """Policies for different orgs should not match."""
        await store.create_policy(PolicyRule(
            name="other-org",
            scope=PolicyScope(org_id="other-corp"),
            force_model="opus",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.MEDIUM, tier_config)
        assert decision.model is None  # No match

    @pytest.mark.asyncio
    async def test_allow_override_propagated(
        self, store: InMemoryHarnessStore, identity: HarnessIdentity, tier_config: ModelTierConfig
    ) -> None:
        """allow_override setting should propagate to the decision."""
        await store.create_policy(PolicyRule(
            name="no-override",
            scope=PolicyScope(org_id="acme"),
            allow_override=False,
            force_model="haiku",
        ))
        engine = PolicyEngine(store=store)
        decision = await engine.resolve(identity, ComplexityTier.COMPLEX, tier_config)
        assert decision.allow_override is False


class TestPolicyScope:
    """Test PolicyScope matching and specificity."""

    def test_empty_scope_matches_all(self) -> None:
        scope = PolicyScope()
        identity = HarnessIdentity(key_id="k1", user_id="alice", org_id="acme")
        assert scope.matches(identity)

    def test_org_scope_matches_same_org(self) -> None:
        scope = PolicyScope(org_id="acme")
        identity = HarnessIdentity(key_id="k1", user_id="alice", org_id="acme")
        assert scope.matches(identity)

    def test_org_scope_rejects_different_org(self) -> None:
        scope = PolicyScope(org_id="other")
        identity = HarnessIdentity(key_id="k1", user_id="alice", org_id="acme")
        assert not scope.matches(identity)

    def test_specificity_ordering(self) -> None:
        org = PolicyScope(org_id="acme")
        team = PolicyScope(org_id="acme", team_id="eng")
        project = PolicyScope(org_id="acme", project_id="web")
        user = PolicyScope(org_id="acme", user_id="alice")

        assert org.specificity() < team.specificity()
        assert team.specificity() < project.specificity()
        assert project.specificity() < user.specificity()


class TestModelTierConfig:
    """Test tier-to-model mapping."""

    def test_model_for_tier(self) -> None:
        config = ModelTierConfig(simple="a", medium="b", complex="c")
        assert config.model_for_tier(ComplexityTier.SIMPLE) == "a"
        assert config.model_for_tier(ComplexityTier.MEDIUM) == "b"
        assert config.model_for_tier(ComplexityTier.COMPLEX) == "c"


class TestInMemoryHarnessStore:
    """Test the in-memory store implementation."""

    @pytest.mark.asyncio
    async def test_policy_crud(self, store: InMemoryHarnessStore) -> None:
        """Test create, read, update, delete for policies."""
        policy = PolicyRule(name="test", scope=PolicyScope(org_id="acme"))
        created = await store.create_policy(policy)
        assert created.name == "test"

        fetched = await store.get_policy(created.id)
        assert fetched is not None
        assert fetched.name == "test"

        updated = await store.update_policy(
            created.id, PolicyRule(name="updated", scope=PolicyScope(org_id="acme"))
        )
        assert updated is not None
        assert updated.name == "updated"

        deleted = await store.delete_policy(created.id)
        assert deleted is True

        assert await store.get_policy(created.id) is None

    @pytest.mark.asyncio
    async def test_key_create_and_validate(self, store: InMemoryHarnessStore) -> None:
        """Test key creation and validation."""
        key = HarnessKey(name="test-key", user_id="alice", org_id="acme")
        plaintext = await store.create_key(key)

        assert plaintext.startswith("sk-harness-")
        assert key.key_suffix == plaintext[-4:]

        identity = await store.validate_key(plaintext)
        assert identity is not None
        assert identity.user_id == "alice"
        assert identity.org_id == "acme"

    @pytest.mark.asyncio
    async def test_invalid_key_returns_none(self, store: InMemoryHarnessStore) -> None:
        identity = await store.validate_key("sk-harness-invalid")
        assert identity is None

    @pytest.mark.asyncio
    async def test_revoked_key_returns_none(self, store: InMemoryHarnessStore) -> None:
        key = HarnessKey(name="test", user_id="alice")
        plaintext = await store.create_key(key)

        await store.revoke_key(key.id)

        identity = await store.validate_key(plaintext)
        assert identity is None

    @pytest.mark.asyncio
    async def test_expired_key_returns_none(self, store: InMemoryHarnessStore) -> None:
        key = HarnessKey(name="test", user_id="alice", expires_at=0.0)
        plaintext = await store.create_key(key)

        identity = await store.validate_key(plaintext)
        assert identity is None

    @pytest.mark.asyncio
    async def test_spend_record_and_query(self, store: InMemoryHarnessStore) -> None:
        """Test recording and querying spend."""
        record = SpendRecord(
            user_id="alice",
            org_id="acme",
            model_used="opus",
            cost_usd=0.05,
            input_tokens=1000,
            output_tokens=500,
        )
        await store.record_spend(record)

        records = await store.get_spend(org_id="acme")
        assert len(records) == 1
        assert records[0].cost_usd == 0.05

    @pytest.mark.asyncio
    async def test_spend_summary(self, store: InMemoryHarnessStore) -> None:
        for i in range(5):
            await store.record_spend(SpendRecord(
                user_id="alice",
                org_id="acme",
                model_used="sonnet",
                cost_usd=0.01,
            ))

        summary = await store.get_spend_summary(org_id="acme")
        assert summary["daily_requests"] == 5
        assert abs(summary["daily_cost_usd"] - 0.05) < 0.001

    @pytest.mark.asyncio
    async def test_spend_by_model(self, store: InMemoryHarnessStore) -> None:
        await store.record_spend(SpendRecord(
            model_used="haiku", cost_usd=0.01, input_tokens=100, output_tokens=50
        ))
        await store.record_spend(SpendRecord(
            model_used="opus", cost_usd=0.10, input_tokens=1000, output_tokens=500
        ))

        by_model = await store.get_spend_by_model()
        assert "haiku" in by_model
        assert "opus" in by_model
        assert by_model["opus"]["cost_usd"] == 0.10

    @pytest.mark.asyncio
    async def test_spend_by_user(self, store: InMemoryHarnessStore) -> None:
        await store.record_spend(SpendRecord(user_id="alice", cost_usd=0.05))
        await store.record_spend(SpendRecord(user_id="bob", cost_usd=0.10))

        by_user = await store.get_spend_by_user()
        assert by_user["alice"]["cost_usd"] == 0.05
        assert by_user["bob"]["cost_usd"] == 0.10

    @pytest.mark.asyncio
    async def test_audit_record_and_query(self, store: InMemoryHarnessStore) -> None:
        from sagewai.harness.models import HarnessAuditEvent

        event = HarnessAuditEvent(
            event_type="request",
            user_id="alice",
            org_id="acme",
            details={"model": "opus"},
        )
        await store.record_audit(event)

        events = await store.get_audit(org_id="acme")
        assert len(events) == 1
        assert events[0].event_type == "request"

    @pytest.mark.asyncio
    async def test_key_list_filtered_by_org(self, store: InMemoryHarnessStore) -> None:
        await store.create_key(HarnessKey(name="k1", org_id="acme", user_id="a"))
        await store.create_key(HarnessKey(name="k2", org_id="other", user_id="b"))

        acme_keys = await store.list_keys(org_id="acme")
        assert len(acme_keys) == 1
        assert acme_keys[0].name == "k1"
