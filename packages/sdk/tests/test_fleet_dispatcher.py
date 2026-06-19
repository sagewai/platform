# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the fleet dispatcher — claim, report, encryption, audit."""

from __future__ import annotations

import asyncio

import pytest

from sagewai.fleet.dispatcher import FleetDispatcher, InMemoryTaskStore, TaskStore


# ---------------------------------------------------------------------------
# InMemoryTaskStore tests
# ---------------------------------------------------------------------------


class TestInMemoryTaskStore:
    """Unit tests for the InMemoryTaskStore."""

    @pytest.fixture()
    def store(self) -> InMemoryTaskStore:
        return InMemoryTaskStore()

    @pytest.mark.asyncio
    async def test_enqueue_and_claim(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default", "payload": "hello"})

        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels=None,
        )
        assert task is not None
        assert task["run_id"] == "r1"
        assert task["worker_id"] == "w1"
        assert "claimed_at" in task

    @pytest.mark.asyncio
    async def test_claim_empty_queue(self, store: InMemoryTaskStore) -> None:
        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels=None,
        )
        assert task is None

    @pytest.mark.asyncio
    async def test_claim_model_filter(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({"run_id": "r1", "model": "claude-sonnet-4-6", "pool": "default"})

        # Worker only supports gpt-4o — should not match
        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels=None,
        )
        assert task is None

        # Worker supports claude-sonnet-4-6 — should match
        task = await store.claim_task(
            worker_id="w2",
            org_id="org1",
            models_canonical=["claude-sonnet-4-6"],
            pool="default",
            labels=None,
        )
        assert task is not None
        assert task["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_claim_pool_filter(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "gpu-cluster"})

        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels=None,
        )
        assert task is None

        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="gpu-cluster",
            labels=None,
        )
        assert task is not None

    @pytest.mark.asyncio
    async def test_claim_label_filter(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({
            "run_id": "r1",
            "model": "gpt-4o",
            "pool": "default",
            "labels": {"region": "us-east"},
        })

        # Worker without matching labels — should not match
        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels={"region": "eu-west"},
        )
        assert task is None

        # Worker with matching labels
        task = await store.claim_task(
            worker_id="w2",
            org_id="org1",
            models_canonical=["gpt-4o"],
            pool="default",
            labels={"region": "us-east"},
        )
        assert task is not None

    @pytest.mark.asyncio
    async def test_claim_no_model_matches_any(self, store: InMemoryTaskStore) -> None:
        """A task without a model field matches any worker."""
        await store.enqueue({"run_id": "r1", "pool": "default"})

        task = await store.claim_task(
            worker_id="w1",
            org_id="org1",
            models_canonical=["anything"],
            pool="default",
            labels=None,
        )
        assert task is not None

    @pytest.mark.asyncio
    async def test_claim_enforces_org_isolation(self, store: InMemoryTaskStore) -> None:
        """An org-stamped task can only be claimed by a worker from that org."""
        await store.enqueue({"run_id": "r1", "org_id": "orgA", "pool": "default"})

        # A worker from a different org must NOT get it.
        assert await store.claim_task("w", "orgB", [], "default", None) is None
        # A same-org worker can.
        task = await store.claim_task("w", "orgA", [], "default", None)
        assert task is not None and task["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_report_task(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({"run_id": "r1", "pool": "default"})
        await store.claim_task("w1", "org1", [], "default", None)

        await store.report_task("r1", "completed", "output data", None, worker_id="w1")

        assert "r1" in store._completed
        assert store._completed["r1"]["status"] == "completed"
        assert store._completed["r1"]["output"] == "output data"
        assert "r1" not in store._claimed

    @pytest.mark.asyncio
    async def test_report_failed(self, store: InMemoryTaskStore) -> None:
        await store.enqueue({"run_id": "r1", "pool": "default"})
        await store.claim_task("w1", "org1", [], "default", None)

        await store.report_task("r1", "failed", None, "something broke", worker_id="w1")

        assert store._completed["r1"]["status"] == "failed"
        assert store._completed["r1"]["error"] == "something broke"

    def test_implements_protocol(self) -> None:
        """InMemoryTaskStore satisfies the TaskStore protocol."""
        assert isinstance(InMemoryTaskStore(), TaskStore)


# ---------------------------------------------------------------------------
# FleetDispatcher tests
# ---------------------------------------------------------------------------


class TestFleetDispatcher:
    """Unit tests for the FleetDispatcher."""

    @pytest.fixture()
    def store(self) -> InMemoryTaskStore:
        return InMemoryTaskStore()

    @pytest.fixture()
    def dispatcher(self, store: InMemoryTaskStore) -> FleetDispatcher:
        return FleetDispatcher(
            store=store,
            poll_interval=0.05,  # fast polling for tests
            poll_timeout=0.3,
        )

    @pytest.mark.asyncio
    async def test_claim_with_available_task(
        self, store: InMemoryTaskStore, dispatcher: FleetDispatcher
    ) -> None:
        await store.enqueue({"run_id": "r1", "model": "gpt-4o", "pool": "default", "payload": "data"})

        task = await dispatcher.claim(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
        )
        assert task is not None
        assert task["run_id"] == "r1"

    @pytest.mark.asyncio
    async def test_claim_timeout_empty_queue(self, dispatcher: FleetDispatcher) -> None:
        task = await dispatcher.claim(
            worker_id="w1",
            org_id="org1",
            models_canonical=["gpt-4o"],
        )
        assert task is None

    @pytest.mark.asyncio
    async def test_claim_waits_for_task(
        self, store: InMemoryTaskStore, dispatcher: FleetDispatcher
    ) -> None:
        """Task enqueued after claim starts should still be found."""

        async def _enqueue_later() -> None:
            await asyncio.sleep(0.1)
            await store.enqueue({"run_id": "r-late", "pool": "default"})

        task_coro = dispatcher.claim(
            worker_id="w1", org_id="org1", models_canonical=["gpt-4o"],
        )
        enqueue_coro = _enqueue_later()

        task, _ = await asyncio.gather(task_coro, enqueue_coro)
        assert task is not None
        assert task["run_id"] == "r-late"

    @pytest.mark.asyncio
    async def test_report_success(
        self, store: InMemoryTaskStore, dispatcher: FleetDispatcher
    ) -> None:
        await store.enqueue({"run_id": "r1", "pool": "default"})
        await dispatcher.claim(worker_id="w1", org_id="org1", models_canonical=[])

        await dispatcher.report(
            worker_id="w1",
            org_id="org1",
            run_id="r1",
            status="completed",
            output="result data",
        )
        assert store._completed["r1"]["status"] == "completed"

    @pytest.mark.asyncio
    async def test_report_failure(
        self, store: InMemoryTaskStore, dispatcher: FleetDispatcher
    ) -> None:
        await store.enqueue({"run_id": "r1", "pool": "default"})
        await dispatcher.claim(worker_id="w1", org_id="org1", models_canonical=[])

        await dispatcher.report(
            worker_id="w1",
            org_id="org1",
            run_id="r1",
            status="failed",
            error="timeout exceeded",
        )
        assert store._completed["r1"]["status"] == "failed"
        assert store._completed["r1"]["error"] == "timeout exceeded"

    @pytest.mark.asyncio
    async def test_heartbeat(self, dispatcher: FleetDispatcher) -> None:
        """Heartbeat should not raise even without an audit backend."""
        await dispatcher.heartbeat("w1")

    @pytest.mark.asyncio
    async def test_encryption_roundtrip(self, store: InMemoryTaskStore) -> None:
        """Dispatcher decrypts payload on claim and encrypts output on report."""

        class FakeEncryption:
            def decrypt(self, org_id: str, data: str) -> str:
                return data.replace("ENC:", "")

            def encrypt(self, org_id: str, data: str) -> str:
                return f"ENC:{data}"

        enc = FakeEncryption()
        dispatcher = FleetDispatcher(
            store=store,
            encryption=enc,
            poll_interval=0.05,
            poll_timeout=0.3,
        )

        await store.enqueue({
            "run_id": "r1",
            "pool": "default",
            "payload": "ENC:secret-data",
        })

        task = await dispatcher.claim(
            worker_id="w1", org_id="org1", models_canonical=[],
        )
        assert task is not None
        assert task["payload"] == "secret-data"  # decrypted

        await dispatcher.report(
            worker_id="w1", org_id="org1", run_id="r1",
            status="completed", output="result",
        )
        assert store._completed["r1"]["output"] == "ENC:result"  # encrypted

    @pytest.mark.asyncio
    async def test_audit_event_recording(self, store: InMemoryTaskStore) -> None:
        """Audit events are recorded on claim and report."""
        events: list[dict] = []

        class FakeAudit:
            async def record(self, **kwargs: object) -> None:
                events.append(dict(kwargs))

        dispatcher = FleetDispatcher(
            store=store,
            audit=FakeAudit(),
            poll_interval=0.05,
            poll_timeout=0.3,
        )

        await store.enqueue({"run_id": "r1", "pool": "default"})

        await dispatcher.claim(worker_id="w1", org_id="org1", models_canonical=[])
        assert len(events) == 1
        assert events[0]["event_type"] == "RUN_CLAIMED"
        assert events[0]["worker_id"] == "w1"
        assert events[0]["run_id"] == "r1"

        await dispatcher.report(
            worker_id="w1", org_id="org1", run_id="r1",
            status="completed", output="done",
        )
        assert len(events) == 2
        assert events[1]["event_type"] == "RUN_REPORTED"
