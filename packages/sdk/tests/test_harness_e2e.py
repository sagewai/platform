"""End-to-end integration tests for the LLM Harness."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sagewai.harness.app import create_harness_app


@pytest.fixture
def app():
    """Create a harness app with no real backends."""
    return create_harness_app()


@pytest.fixture
def client(app) -> TestClient:
    return TestClient(app)


class TestHarnessE2E:
    """End-to-end tests through the full harness stack."""

    def test_health_endpoint(self, client: TestClient) -> None:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"

    def test_classify_simple_request(self, client: TestClient) -> None:
        """Simple request should classify as SIMPLE tier."""
        resp = client.post("/api/v1/harness/test-classify", json={
            "messages": [{"role": "user", "content": "fix typo"}],
        })
        assert resp.status_code == 200
        assert resp.json()["tier"] == "simple"

    def test_classify_complex_request(self, client: TestClient) -> None:
        """Complex request should classify as COMPLEX tier."""
        resp = client.post("/api/v1/harness/test-classify", json={
            "messages": [
                {"role": "system", "content": "You are an architect. " * 100},
                {"role": "user", "content": (
                    "Design a complete microservices architecture "
                    "with security audit and end-to-end review"
                )},
            ],
            "tools": [{"name": f"tool_{i}"} for i in range(10)],
        })
        assert resp.status_code == 200
        assert resp.json()["tier"] == "complex"

    def test_create_and_list_policy(self, client: TestClient) -> None:
        """Create a policy and verify it appears in the list."""
        # Create
        resp = client.post("/api/v1/harness/policies", json={
            "name": "e2e-test-policy",
            "scope": {"org_id": "test-org"},
            "force_model": "haiku",
        })
        assert resp.status_code == 201
        policy_id = resp.json()["id"]

        # List
        resp = client.get("/api/v1/harness/policies")
        assert resp.status_code == 200
        names = [p["name"] for p in resp.json()]
        assert "e2e-test-policy" in names

        # Get
        resp = client.get(f"/api/v1/harness/policies/{policy_id}")
        assert resp.status_code == 200
        assert resp.json()["force_model"] == "haiku"

        # Delete
        resp = client.delete(f"/api/v1/harness/policies/{policy_id}")
        assert resp.status_code == 204

    def test_key_lifecycle(self, client: TestClient) -> None:
        """Create, list, and revoke a key."""
        # Create
        resp = client.post("/api/v1/harness/keys", json={
            "name": "e2e-key",
            "user_id": "test-user",
            "org_id": "test-org",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["plaintext"].startswith("sk-harness-")
        key_id = data["key_id"]

        # List
        resp = client.get("/api/v1/harness/keys")
        assert resp.status_code == 200
        assert any(k["name"] == "e2e-key" for k in resp.json())

        # Revoke
        resp = client.delete(f"/api/v1/harness/keys/{key_id}")
        assert resp.status_code == 204

    def test_spend_summary_starts_empty(self, client: TestClient) -> None:
        """Spend summary should start at zero."""
        resp = client.get("/api/v1/harness/spend")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_requests"] == 0
        assert data["total_cost_usd"] == 0.0

    def test_spend_breakdown_starts_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/harness/spend/breakdown")
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_config_get_and_update(self, client: TestClient) -> None:
        """Get and update global config."""
        resp = client.get("/api/v1/harness/config")
        assert resp.status_code == 200
        assert resp.json()["enabled"] is True

        # Update
        resp = client.put("/api/v1/harness/config", json={
            "enabled": False,
            "allow_model_override": False,
        })
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_audit_starts_empty(self, client: TestClient) -> None:
        resp = client.get("/api/v1/harness/audit")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_models_endpoint(self, client: TestClient) -> None:
        """Models endpoint should return tier config models."""
        resp = client.get("/v1/models")
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        # Should contain at least the tier config defaults
        model_ids = [m["id"] for m in data["data"]]
        assert len(model_ids) >= 3  # simple, medium, complex
