"""Tests for sagewai.fleet.registry — FleetRegistry and InMemoryFleetRegistry."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.fleet.models import (
    WorkerApprovalStatus,
    WorkerCapabilities,
)
from sagewai.fleet.registry import InMemoryFleetRegistry


@pytest.fixture
def registry() -> InMemoryFleetRegistry:
    return InMemoryFleetRegistry()


@pytest.fixture
def caps() -> WorkerCapabilities:
    return WorkerCapabilities(
        models_supported=["openai/gpt-4o", "ollama/llama3:70b"],
        max_concurrent=2,
        pool="gpu",
        labels={"region": "us-east"},
    )


# ---------------------------------------------------------------------------
# Worker Registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_worker_without_key_is_pending(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="worker-1", org_id="org-1", capabilities=caps
    )
    assert worker.approval_status == WorkerApprovalStatus.PENDING
    assert worker.org_id == "org-1"
    assert worker.name == "worker-1"
    assert worker.approved_at is None
    assert worker.approved_by is None
    # Canonical models should be filled
    assert "gpt-4o" in worker.capabilities.models_canonical
    assert "llama3-70b" in worker.capabilities.models_canonical


@pytest.mark.asyncio
async def test_register_worker_with_valid_key_is_approved(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    worker = await registry.register_worker(
        name="worker-1",
        org_id="org-1",
        capabilities=caps,
        enrollment_key=raw_key,
    )
    assert worker.approval_status == WorkerApprovalStatus.APPROVED
    assert worker.approved_by == "enrollment-key"
    assert worker.approved_at is not None


@pytest.mark.asyncio
async def test_register_worker_with_invalid_key_is_pending(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="worker-1",
        org_id="org-1",
        capabilities=caps,
        enrollment_key="bogus-key",
    )
    assert worker.approval_status == WorkerApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_register_worker_with_wrong_org_key_is_pending(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-OTHER", name="key-1", created_by="admin-1"
    )
    worker = await registry.register_worker(
        name="worker-1",
        org_id="org-1",
        capabilities=caps,
        enrollment_key=raw_key,
    )
    assert worker.approval_status == WorkerApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# Get / List Workers
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_worker(registry: InMemoryFleetRegistry, caps: WorkerCapabilities):
    worker = await registry.register_worker(
        name="worker-1", org_id="org-1", capabilities=caps
    )
    fetched = await registry.get_worker(worker.id)
    assert fetched is not None
    assert fetched.id == worker.id


@pytest.mark.asyncio
async def test_get_worker_not_found(registry: InMemoryFleetRegistry):
    assert await registry.get_worker("nonexistent") is None


@pytest.mark.asyncio
async def test_list_workers_filters(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    w2 = await registry.register_worker(
        name="w2", org_id="org-1", capabilities=caps
    )
    await registry.approve_worker(w2.id, approved_by="admin")

    # All for org-1
    all_workers = await registry.list_workers(org_id="org-1")
    assert len(all_workers) == 2

    # Filter by status
    pending = await registry.list_workers(
        org_id="org-1", status=WorkerApprovalStatus.PENDING
    )
    assert len(pending) == 1

    approved = await registry.list_workers(
        org_id="org-1", status=WorkerApprovalStatus.APPROVED
    )
    assert len(approved) == 1

    # Filter by pool
    gpu = await registry.list_workers(org_id="org-1", pool="gpu")
    assert len(gpu) == 2

    cpu = await registry.list_workers(org_id="org-1", pool="cpu")
    assert len(cpu) == 0

    # Different org
    other = await registry.list_workers(org_id="org-other")
    assert len(other) == 0


@pytest.mark.asyncio
async def test_list_workers_limit(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    for i in range(5):
        await registry.register_worker(
            name=f"w{i}", org_id="org-1", capabilities=caps
        )
    limited = await registry.list_workers(org_id="org-1", limit=3)
    assert len(limited) == 3


# ---------------------------------------------------------------------------
# State Transitions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_worker(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    approved = await registry.approve_worker(worker.id, approved_by="admin-1")
    assert approved.approval_status == WorkerApprovalStatus.APPROVED
    assert approved.approved_by == "admin-1"
    assert approved.approved_at is not None


@pytest.mark.asyncio
async def test_approve_non_pending_raises(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    await registry.approve_worker(worker.id, approved_by="admin")
    with pytest.raises(ValueError, match="Cannot approve"):
        await registry.approve_worker(worker.id, approved_by="admin")


@pytest.mark.asyncio
async def test_reject_worker(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    rejected = await registry.reject_worker(worker.id)
    assert rejected.approval_status == WorkerApprovalStatus.REJECTED


@pytest.mark.asyncio
async def test_reject_non_pending_raises(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    await registry.approve_worker(worker.id, approved_by="admin")
    with pytest.raises(ValueError, match="Cannot reject"):
        await registry.reject_worker(worker.id)


@pytest.mark.asyncio
async def test_revoke_worker(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    await registry.approve_worker(worker.id, approved_by="admin")
    revoked = await registry.revoke_worker(worker.id)
    assert revoked.approval_status == WorkerApprovalStatus.REVOKED


@pytest.mark.asyncio
async def test_revoke_non_approved_raises(
    registry: InMemoryFleetRegistry, caps: WorkerCapabilities
):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    with pytest.raises(ValueError, match="Cannot revoke"):
        await registry.revoke_worker(worker.id)


@pytest.mark.asyncio
async def test_approve_nonexistent_raises(registry: InMemoryFleetRegistry):
    with pytest.raises(ValueError, match="not found"):
        await registry.approve_worker("no-such-id", approved_by="admin")


@pytest.mark.asyncio
async def test_reject_nonexistent_raises(registry: InMemoryFleetRegistry):
    with pytest.raises(ValueError, match="not found"):
        await registry.reject_worker("no-such-id")


@pytest.mark.asyncio
async def test_revoke_nonexistent_raises(registry: InMemoryFleetRegistry):
    with pytest.raises(ValueError, match="not found"):
        await registry.revoke_worker("no-such-id")


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_heartbeat(registry: InMemoryFleetRegistry, caps: WorkerCapabilities):
    worker = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps
    )
    assert worker.last_heartbeat is None

    await registry.heartbeat(worker.id)
    updated = await registry.get_worker(worker.id)
    assert updated is not None
    assert updated.last_heartbeat is not None


@pytest.mark.asyncio
async def test_heartbeat_nonexistent_is_noop(registry: InMemoryFleetRegistry):
    # Plan 1.5: heartbeat for an unknown worker silently returns (no-op).
    # The server-side pool_stats cache update must not raise on missing workers.
    await registry.heartbeat("no-such-id")  # must not raise


# ---------------------------------------------------------------------------
# Enrollment Key CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_enrollment_key(registry: InMemoryFleetRegistry):
    record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="test-key",
        created_by="admin-1",
        max_uses=10,
    )
    assert record.org_id == "org-1"
    assert record.name == "test-key"
    assert record.max_uses == 10
    assert record.current_uses == 0
    assert not record.revoked
    assert len(raw_key) > 20  # secrets.token_urlsafe(32) produces ~43 chars


@pytest.mark.asyncio
async def test_list_enrollment_keys(registry: InMemoryFleetRegistry):
    await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    await registry.create_enrollment_key(
        org_id="org-1", name="key-2", created_by="admin-1"
    )
    await registry.create_enrollment_key(
        org_id="org-other", name="key-3", created_by="admin-2"
    )

    keys = await registry.list_enrollment_keys("org-1")
    assert len(keys) == 2

    keys_other = await registry.list_enrollment_keys("org-other")
    assert len(keys_other) == 1


@pytest.mark.asyncio
async def test_revoke_enrollment_key(registry: InMemoryFleetRegistry):
    record, _raw = await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    await registry.revoke_enrollment_key(record.id)

    keys = await registry.list_enrollment_keys("org-1")
    assert keys[0].revoked is True


@pytest.mark.asyncio
async def test_revoke_enrollment_key_not_found(registry: InMemoryFleetRegistry):
    with pytest.raises(ValueError, match="not found"):
        await registry.revoke_enrollment_key("no-such-id")


# ---------------------------------------------------------------------------
# Enrollment Key Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_validate_enrollment_key_valid(registry: InMemoryFleetRegistry):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    result = await registry.validate_enrollment_key("org-1", raw_key)
    assert result is not None
    assert result.org_id == "org-1"


@pytest.mark.asyncio
async def test_validate_enrollment_key_wrong_org(registry: InMemoryFleetRegistry):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    result = await registry.validate_enrollment_key("org-other", raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_validate_enrollment_key_revoked(registry: InMemoryFleetRegistry):
    record, raw_key = await registry.create_enrollment_key(
        org_id="org-1", name="key-1", created_by="admin-1"
    )
    await registry.revoke_enrollment_key(record.id)
    result = await registry.validate_enrollment_key("org-1", raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_validate_enrollment_key_expired(registry: InMemoryFleetRegistry):
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="key-1",
        created_by="admin-1",
        expires_at=past,
    )
    result = await registry.validate_enrollment_key("org-1", raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_validate_enrollment_key_exhausted(registry: InMemoryFleetRegistry):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="key-1",
        created_by="admin-1",
        max_uses=1,
    )
    # Use it once
    caps = WorkerCapabilities(pool="default")
    await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps, enrollment_key=raw_key
    )
    # Now it's exhausted
    result = await registry.validate_enrollment_key("org-1", raw_key)
    assert result is None


@pytest.mark.asyncio
async def test_validate_enrollment_key_bogus(registry: InMemoryFleetRegistry):
    result = await registry.validate_enrollment_key("org-1", "totally-bogus")
    assert result is None


# ---------------------------------------------------------------------------
# Enrollment Key Constraints
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrollment_key_pool_constraint(registry: InMemoryFleetRegistry):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="gpu-only",
        created_by="admin-1",
        allowed_pools=["gpu"],
    )
    # Matching pool -> auto-approve
    caps_gpu = WorkerCapabilities(pool="gpu")
    w1 = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps_gpu, enrollment_key=raw_key
    )
    assert w1.approval_status == WorkerApprovalStatus.APPROVED

    # Non-matching pool -> pending (key valid but constraints don't match)
    caps_cpu = WorkerCapabilities(pool="cpu")
    w2 = await registry.register_worker(
        name="w2", org_id="org-1", capabilities=caps_cpu, enrollment_key=raw_key
    )
    assert w2.approval_status == WorkerApprovalStatus.PENDING


@pytest.mark.asyncio
async def test_enrollment_key_model_constraint(registry: InMemoryFleetRegistry):
    _record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="gpt-only",
        created_by="admin-1",
        allowed_models=["openai/gpt-4o"],
    )
    # Matching model -> auto-approve
    caps_match = WorkerCapabilities(
        models_supported=["openai/gpt-4o"], pool="default"
    )
    w1 = await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps_match, enrollment_key=raw_key
    )
    assert w1.approval_status == WorkerApprovalStatus.APPROVED

    # No matching model -> pending
    caps_nomatch = WorkerCapabilities(
        models_supported=["anthropic/claude-sonnet-4-6"], pool="default"
    )
    w2 = await registry.register_worker(
        name="w2", org_id="org-1", capabilities=caps_nomatch, enrollment_key=raw_key
    )
    assert w2.approval_status == WorkerApprovalStatus.PENDING


# ---------------------------------------------------------------------------
# Enrollment Key Usage Counter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_enrollment_key_usage_increments(registry: InMemoryFleetRegistry):
    record, raw_key = await registry.create_enrollment_key(
        org_id="org-1",
        name="key-1",
        created_by="admin-1",
        max_uses=5,
    )
    caps = WorkerCapabilities(pool="default")
    await registry.register_worker(
        name="w1", org_id="org-1", capabilities=caps, enrollment_key=raw_key
    )

    keys = await registry.list_enrollment_keys("org-1")
    assert keys[0].current_uses == 1
