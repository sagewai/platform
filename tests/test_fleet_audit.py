"""Tests for sagewai.fleet.audit — Fleet audit events and backends."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sagewai.fleet.audit import (
    FleetAuditEvent,
    FleetAuditEventType,
    InMemoryFleetAuditBackend,
)


# ------------------------------------------------------------------
# FleetAuditEventType
# ------------------------------------------------------------------


class TestFleetAuditEventType:
    """Validate enum members and string values."""

    def test_all_values(self) -> None:
        expected = {
            "worker.registered",
            "worker.approved",
            "worker.rejected",
            "worker.revoked",
            "worker.heartbeat_missed",
            "enrollment_key.created",
            "enrollment_key.revoked",
            "enrollment_key.used",
            "run.claimed",
            "run.reported",
            "run.timeout",
            "token.issued",
            "token.revoked",
        }
        actual = {e.value for e in FleetAuditEventType}
        assert actual == expected

    def test_str_enum(self) -> None:
        """Members should be usable as plain strings."""
        assert FleetAuditEventType.WORKER_REGISTERED == "worker.registered"
        assert FleetAuditEventType.TOKEN_ISSUED.value == "token.issued"


# ------------------------------------------------------------------
# FleetAuditEvent model
# ------------------------------------------------------------------


class TestFleetAuditEvent:
    """Validate Pydantic model behaviour."""

    def test_defaults(self) -> None:
        evt = FleetAuditEvent(
            org_id="acme",
            event_type=FleetAuditEventType.WORKER_REGISTERED,
        )
        assert evt.org_id == "acme"
        assert evt.worker_id is None
        assert evt.details == {}
        assert isinstance(evt.id, str)
        assert isinstance(evt.created_at, datetime)

    def test_serialization(self) -> None:
        evt = FleetAuditEvent(
            id="evt-1",
            org_id="acme",
            event_type=FleetAuditEventType.RUN_CLAIMED,
            worker_id="w-10",
            details={"run_id": "run-abc"},
        )
        data = evt.model_dump()
        assert data["id"] == "evt-1"
        assert data["event_type"] == FleetAuditEventType.RUN_CLAIMED
        assert data["details"]["run_id"] == "run-abc"

    def test_json_roundtrip(self) -> None:
        evt = FleetAuditEvent(
            org_id="acme",
            event_type=FleetAuditEventType.TOKEN_REVOKED,
            worker_id="w-5",
        )
        json_str = evt.model_dump_json()
        rebuilt = FleetAuditEvent.model_validate_json(json_str)
        assert rebuilt.org_id == evt.org_id
        assert rebuilt.event_type == evt.event_type
        assert rebuilt.worker_id == evt.worker_id


# ------------------------------------------------------------------
# InMemoryFleetAuditBackend
# ------------------------------------------------------------------


class TestInMemoryFleetAuditBackend:
    """Tests for the in-memory audit backend."""

    @pytest.fixture()
    def backend(self) -> InMemoryFleetAuditBackend:
        return InMemoryFleetAuditBackend()

    async def test_record_and_query(self, backend: InMemoryFleetAuditBackend) -> None:
        evt = FleetAuditEvent(
            org_id="acme",
            event_type=FleetAuditEventType.WORKER_REGISTERED,
            worker_id="w-1",
        )
        await backend.record(evt)
        results = await backend.query("acme")
        assert len(results) == 1
        assert results[0].id == evt.id

    async def test_query_filters_org(self, backend: InMemoryFleetAuditBackend) -> None:
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="other",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
            )
        )
        results = await backend.query("acme")
        assert len(results) == 1
        assert results[0].org_id == "acme"

    async def test_query_filter_event_type(
        self, backend: InMemoryFleetAuditBackend
    ) -> None:
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.RUN_CLAIMED,
            )
        )
        results = await backend.query(
            "acme", event_type=FleetAuditEventType.RUN_CLAIMED
        )
        assert len(results) == 1
        assert results[0].event_type == FleetAuditEventType.RUN_CLAIMED

    async def test_query_filter_worker_id(
        self, backend: InMemoryFleetAuditBackend
    ) -> None:
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                worker_id="w-1",
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                worker_id="w-2",
            )
        )
        results = await backend.query("acme", worker_id="w-2")
        assert len(results) == 1
        assert results[0].worker_id == "w-2"

    async def test_query_filter_since(
        self, backend: InMemoryFleetAuditBackend
    ) -> None:
        old_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        new_time = datetime(2026, 3, 1, tzinfo=timezone.utc)

        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                created_at=old_time,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_APPROVED,
                created_at=new_time,
            )
        )

        cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
        results = await backend.query("acme", since=cutoff)
        assert len(results) == 1
        assert results[0].event_type == FleetAuditEventType.WORKER_APPROVED

    async def test_query_limit(self, backend: InMemoryFleetAuditBackend) -> None:
        for i in range(10):
            await backend.record(
                FleetAuditEvent(
                    org_id="acme",
                    event_type=FleetAuditEventType.WORKER_REGISTERED,
                    created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)
                    + timedelta(hours=i),
                )
            )
        results = await backend.query("acme", limit=3)
        assert len(results) == 3

    async def test_query_newest_first(
        self, backend: InMemoryFleetAuditBackend
    ) -> None:
        t1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                created_at=t1,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_APPROVED,
                created_at=t2,
            )
        )
        results = await backend.query("acme")
        assert results[0].created_at >= results[1].created_at

    async def test_query_empty(self, backend: InMemoryFleetAuditBackend) -> None:
        results = await backend.query("acme")
        assert results == []

    async def test_combined_filters(
        self, backend: InMemoryFleetAuditBackend
    ) -> None:
        """Multiple filters work together (AND logic)."""
        t = datetime(2026, 3, 15, tzinfo=timezone.utc)
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.RUN_CLAIMED,
                worker_id="w-1",
                created_at=t,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.RUN_CLAIMED,
                worker_id="w-2",
                created_at=t,
            )
        )
        await backend.record(
            FleetAuditEvent(
                org_id="acme",
                event_type=FleetAuditEventType.WORKER_REGISTERED,
                worker_id="w-1",
                created_at=t,
            )
        )

        results = await backend.query(
            "acme",
            event_type=FleetAuditEventType.RUN_CLAIMED,
            worker_id="w-1",
        )
        assert len(results) == 1
        assert results[0].worker_id == "w-1"
        assert results[0].event_type == FleetAuditEventType.RUN_CLAIMED
