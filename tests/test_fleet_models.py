"""Tests for sagewai.fleet.models — Fleet data models."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.fleet.models import (
    EnrollmentKey,
    WorkerApprovalStatus,
    WorkerCapabilities,
    WorkerRecord,
)
from sagewai.fleet.normalizer import ModelNormalizer


class TestWorkerApprovalStatus:
    """WorkerApprovalStatus enum tests."""

    def test_values(self) -> None:
        assert WorkerApprovalStatus.PENDING == "pending"
        assert WorkerApprovalStatus.APPROVED == "approved"
        assert WorkerApprovalStatus.REJECTED == "rejected"
        assert WorkerApprovalStatus.REVOKED == "revoked"

    def test_is_str_enum(self) -> None:
        assert isinstance(WorkerApprovalStatus.PENDING, str)

    def test_all_values(self) -> None:
        values = {s.value for s in WorkerApprovalStatus}
        assert values == {"pending", "approved", "rejected", "revoked"}


class TestWorkerCapabilities:
    """WorkerCapabilities model tests."""

    def test_defaults(self) -> None:
        caps = WorkerCapabilities()
        assert caps.models_supported == []
        assert caps.models_canonical == []
        assert caps.max_concurrent == 1
        assert caps.labels == {}
        assert caps.pool == "default"
        assert caps.sdk_version == ""

    def test_serialization_roundtrip(self) -> None:
        caps = WorkerCapabilities(
            models_supported=["openai/gpt-4o", "ollama/llama3:70b"],
            models_canonical=["gpt-4o", "llama3-70b"],
            max_concurrent=8,
            labels={"zone": "us-east", "gpu": "a100"},
            pool="gpu-cluster",
            sdk_version="0.1.0",
        )
        data = caps.model_dump()
        restored = WorkerCapabilities.model_validate(data)
        assert restored == caps

    def test_canonical_list_auto_fill(self) -> None:
        """Verify canonical_list can be computed from models_supported."""
        caps = WorkerCapabilities(
            models_supported=["openai/gpt-4o", "ollama/llama3:70b"],
        )
        caps.models_canonical = ModelNormalizer.canonical_list(
            caps.models_supported
        )
        assert caps.models_canonical == ["gpt-4o", "llama3-70b"]

    def test_max_concurrent_must_be_positive(self) -> None:
        with pytest.raises(Exception):  # Pydantic validation error
            WorkerCapabilities(max_concurrent=0)

    def test_json_roundtrip(self) -> None:
        caps = WorkerCapabilities(
            models_supported=["gpt-4o"],
            pool="test",
        )
        json_str = caps.model_dump_json()
        restored = WorkerCapabilities.model_validate_json(json_str)
        assert restored == caps


class TestWorkerRecord:
    """WorkerRecord model tests."""

    def test_minimal_creation(self) -> None:
        now = datetime.now(timezone.utc)
        record = WorkerRecord(
            id="worker-001",
            name="test-worker",
            org_id="org-123",
            registered_at=now,
        )
        assert record.id == "worker-001"
        assert record.approval_status == WorkerApprovalStatus.PENDING
        assert record.last_heartbeat is None
        assert record.approved_at is None

    def test_full_creation(self) -> None:
        now = datetime.now(timezone.utc)
        record = WorkerRecord(
            id="worker-002",
            name="gpu-worker",
            org_id="org-456",
            capabilities=WorkerCapabilities(
                models_supported=["gpt-4o"],
                max_concurrent=4,
                pool="gpu",
            ),
            approval_status=WorkerApprovalStatus.APPROVED,
            last_heartbeat=now,
            last_probe_at=now,
            probe_status="healthy",
            registered_at=now,
            approved_at=now,
            approved_by="admin-user-1",
        )
        assert record.approval_status == WorkerApprovalStatus.APPROVED
        assert record.probe_status == "healthy"
        assert record.approved_by == "admin-user-1"

    def test_serialization_roundtrip(self) -> None:
        now = datetime.now(timezone.utc)
        record = WorkerRecord(
            id="w-1",
            name="test",
            org_id="org-1",
            registered_at=now,
        )
        data = record.model_dump()
        restored = WorkerRecord.model_validate(data)
        assert restored == record


class TestEnrollmentKey:
    """EnrollmentKey model tests."""

    def _make_key(self, **overrides: object) -> EnrollmentKey:
        now = datetime.now(timezone.utc)
        defaults = dict(
            id="key-001",
            org_id="org-123",
            name="test-key",
            key_hash="$2b$12$fakehash",
            created_at=now,
            created_by="admin-1",
        )
        defaults.update(overrides)
        return EnrollmentKey(**defaults)  # type: ignore[arg-type]

    def test_defaults(self) -> None:
        key = self._make_key()
        assert key.max_uses is None
        assert key.current_uses == 0
        assert key.expires_at is None
        assert key.allowed_pools == []
        assert key.allowed_models == []
        assert key.revoked is False

    def test_is_expired_not_expired(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        key = self._make_key(expires_at=future)
        assert not key.is_expired()

    def test_is_expired_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        key = self._make_key(expires_at=past)
        assert key.is_expired()

    def test_is_expired_no_expiration(self) -> None:
        key = self._make_key(expires_at=None)
        assert not key.is_expired()

    def test_is_exhausted_under_limit(self) -> None:
        key = self._make_key(max_uses=10, current_uses=5)
        assert not key.is_exhausted()

    def test_is_exhausted_at_limit(self) -> None:
        key = self._make_key(max_uses=10, current_uses=10)
        assert key.is_exhausted()

    def test_is_exhausted_over_limit(self) -> None:
        key = self._make_key(max_uses=10, current_uses=15)
        assert key.is_exhausted()

    def test_is_exhausted_unlimited(self) -> None:
        key = self._make_key(max_uses=None, current_uses=1000)
        assert not key.is_exhausted()

    def test_is_usable_valid_key(self) -> None:
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        key = self._make_key(
            max_uses=10,
            current_uses=5,
            expires_at=future,
        )
        assert key.is_usable()

    def test_is_usable_revoked(self) -> None:
        key = self._make_key(revoked=True)
        assert not key.is_usable()

    def test_is_usable_expired(self) -> None:
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        key = self._make_key(expires_at=past)
        assert not key.is_usable()

    def test_is_usable_exhausted(self) -> None:
        key = self._make_key(max_uses=5, current_uses=5)
        assert not key.is_usable()

    def test_allowed_pools_and_models(self) -> None:
        key = self._make_key(
            allowed_pools=["gpu-cluster", "cpu-pool"],
            allowed_models=["gpt-4o", "claude-sonnet-4-6"],
        )
        assert "gpu-cluster" in key.allowed_pools
        assert "claude-sonnet-4-6" in key.allowed_models

    def test_serialization_roundtrip(self) -> None:
        key = self._make_key(
            max_uses=100,
            allowed_pools=["default"],
            expires_at=datetime.now(timezone.utc) + timedelta(days=30),
        )
        data = key.model_dump()
        restored = EnrollmentKey.model_validate(data)
        assert restored == key

    def test_current_uses_cannot_be_negative(self) -> None:
        with pytest.raises(Exception):
            self._make_key(current_uses=-1)


class TestFleetExports:
    """Verify fleet models are exported from the main package."""

    def test_fleet_imports_from_package(self) -> None:
        from sagewai import (  # noqa: F401
            EnrollmentKey,
            ModelNormalizer,
            WorkerApprovalStatus,
            WorkerCapabilities,
            WorkerRecord,
        )

    def test_fleet_in_all(self) -> None:
        import sagewai

        for name in [
            "EnrollmentKey",
            "ModelNormalizer",
            "WorkerApprovalStatus",
            "WorkerCapabilities",
            "WorkerRecord",
        ]:
            assert name in sagewai.__all__
