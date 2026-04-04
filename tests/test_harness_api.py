"""Integration tests for the LLM Harness API layer."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from sagewai.admin.budget import BudgetManager
from sagewai.harness.admin_api import create_harness_admin_router
from sagewai.harness.app import create_harness_app
from sagewai.harness.classifier import RequestClassifier
from sagewai.harness.models import (
    HarnessConfig,
    HarnessKey,
    ModelTierConfig,
    PolicyRule,
    PolicyScope,
    SpendRecord,
)
from sagewai.harness.store import InMemoryHarnessStore


@pytest.fixture
def store() -> InMemoryHarnessStore:
    return InMemoryHarnessStore()


@pytest.fixture
def config() -> HarnessConfig:
    return HarnessConfig()


@pytest.fixture
def admin_app(store: InMemoryHarnessStore, config: HarnessConfig) -> FastAPI:
    """Create a test app with admin routes only."""
    app = FastAPI()
    router = create_harness_admin_router(
        store=store,
        classifier=RequestClassifier(),
        config=config,
    )
    app.include_router(router, prefix="/api/v1/harness")
    return app


@pytest.fixture
def admin_client(admin_app: FastAPI) -> TestClient:
    return TestClient(admin_app)


class TestAdminPolicies:
    """Test admin policy CRUD endpoints."""

    def test_list_policies_empty(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v1/harness/policies")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_create_and_get_policy(self, admin_client: TestClient) -> None:
        policy_data = {
            "name": "test-policy",
            "scope": {"org_id": "acme"},
            "force_model": "haiku",
        }
        resp = admin_client.post("/api/v1/harness/policies", json=policy_data)
        assert resp.status_code == 201
        created = resp.json()
        assert created["name"] == "test-policy"
        policy_id = created["id"]

        resp = admin_client.get(f"/api/v1/harness/policies/{policy_id}")
        assert resp.status_code == 200
        assert resp.json()["force_model"] == "haiku"

    def test_update_policy(self, admin_client: TestClient) -> None:
        # Create
        resp = admin_client.post("/api/v1/harness/policies", json={
            "name": "original",
            "scope": {"org_id": "acme"},
        })
        policy_id = resp.json()["id"]

        # Update
        resp = admin_client.put(f"/api/v1/harness/policies/{policy_id}", json={
            "name": "updated",
            "scope": {"org_id": "acme"},
            "force_model": "opus",
        })
        assert resp.status_code == 200
        assert resp.json()["name"] == "updated"
        assert resp.json()["force_model"] == "opus"

    def test_delete_policy(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/v1/harness/policies", json={
            "name": "to-delete",
            "scope": {},
        })
        assert resp.status_code == 201
        policy_id = resp.json()["id"]

        resp = admin_client.delete(f"/api/v1/harness/policies/{policy_id}")
        assert resp.status_code == 204

        resp = admin_client.get(f"/api/v1/harness/policies/{policy_id}")
        assert resp.status_code == 404

    def test_get_nonexistent_policy(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v1/harness/policies/nonexistent")
        assert resp.status_code == 404


class TestAdminKeys:
    """Test admin key management endpoints."""

    def test_create_key(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/v1/harness/keys", json={
            "name": "test-key",
            "user_id": "alice",
            "org_id": "acme",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["plaintext"].startswith("sk-harness-")
        assert "key_id" in data

    def test_list_keys(self, admin_client: TestClient) -> None:
        admin_client.post("/api/v1/harness/keys", json={
            "name": "k1", "user_id": "alice",
        })
        admin_client.post("/api/v1/harness/keys", json={
            "name": "k2", "user_id": "bob",
        })
        resp = admin_client.get("/api/v1/harness/keys")
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_revoke_key(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/v1/harness/keys", json={
            "name": "to-revoke", "user_id": "alice",
        })
        assert resp.status_code == 201
        key_id = resp.json()["key_id"]

        resp = admin_client.delete(f"/api/v1/harness/keys/{key_id}")
        assert resp.status_code == 204


class TestAdminSpend:
    """Test spend query endpoints."""

    @pytest.mark.asyncio
    async def test_spend_summary(
        self, store: InMemoryHarnessStore, admin_client: TestClient
    ) -> None:
        # Add spend records directly to store
        await store.record_spend(SpendRecord(
            user_id="alice", org_id="acme", model_used="opus", cost_usd=0.05
        ))
        resp = admin_client.get("/api/v1/harness/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert "daily_cost_usd" in data

    @pytest.mark.asyncio
    async def test_spend_breakdown(
        self, store: InMemoryHarnessStore, admin_client: TestClient
    ) -> None:
        await store.record_spend(SpendRecord(
            model_used="haiku", cost_usd=0.01, input_tokens=100, output_tokens=50
        ))
        await store.record_spend(SpendRecord(
            model_used="opus", cost_usd=0.10, input_tokens=1000, output_tokens=500
        ))
        resp = admin_client.get("/api/v1/harness/spend/breakdown")
        assert resp.status_code == 200
        data = resp.json()
        assert "haiku" in data
        assert "opus" in data


class TestAdminClassify:
    """Test the dry-run classification endpoint."""

    def test_classify_simple_request(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/v1/harness/test-classify", json={
            "messages": [{"role": "user", "content": "fix typo"}],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "simple"
        assert "score" in data

    def test_classify_complex_request(self, admin_client: TestClient) -> None:
        resp = admin_client.post("/api/v1/harness/test-classify", json={
            "messages": [
                {"role": "system", "content": "You are an architect. " * 100},
                {"role": "user", "content": (
                    "Design and plan a complete microservices migration "
                    "with security audit for the authentication module."
                )},
            ],
            "tools": [{"name": f"tool_{i}"} for i in range(10)],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["tier"] == "complex"


class TestAdminConfig:
    """Test global config endpoints."""

    def test_get_config(self, admin_client: TestClient) -> None:
        resp = admin_client.get("/api/v1/harness/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["enabled"] is True


class TestStandaloneApp:
    """Test the standalone harness app factory."""

    def test_create_app(self) -> None:
        app = create_harness_app()
        assert isinstance(app, FastAPI)

    def test_app_health(self) -> None:
        app = create_harness_app()
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
