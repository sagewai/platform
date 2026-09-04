# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

from __future__ import annotations

import pytest

from sagewai.artifacts.object_store import LocalArtifactStore
from sagewai.work.tasks.actions import deliver_action
from sagewai.work.tasks.events import TaskEventType
from sagewai.work.tasks.models import Authority, GateMode, TaskOrigin, TaskStatus
from sagewai.work.tasks.service import TaskService
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_coordinator import (
    PROJECT,
    _drive_to_rest,
    _fixed_task,
    _lose_the_batch,
    _seed,
    stores,  # noqa: F401
)


def _deliver_action_payload(work_id: str, *, rollback: str | None) -> dict:
    return deliver_action(
        PROJECT,
        work_id=work_id,
        scope="https://github.com/o/r/issues/9",
        evidence_refs=(),
        rollback=rollback,
    ).model_dump(mode="json")


@pytest.mark.asyncio
async def test_an_approved_work_gate_is_mirrored_back_onto_the_task(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_MERGE"
    runner.gates["s1"] = "merge:w-s1-1:7"
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.pending_gate == "merge:w-s1-1:7"

    await runner.approve("w-s1-1", gate_id="merge:w-s1-1:7", decision="allow")
    record = await _drive_to_rest(coordinator, record, epoch)

    assert record.pending_gate is None
    decided = [
        event.payload_json
        for event in await task_store.read_events(task.id, project_id=PROJECT)
        if event.event_type is TaskEventType.GATE_DECIDED
    ]
    assert decided == [{"gate_id": "merge:w-s1-1:7", "decision": "allow"}]


@pytest.mark.asyncio
async def test_a_denied_work_gate_is_mirrored_back_and_blocks_the_task(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_MERGE"
    runner.gates["s1"] = "merge:w-s1-1:7"
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)
    assert record.pending_gate == "merge:w-s1-1:7"

    await runner.approve("w-s1-1", gate_id="merge:w-s1-1:7", decision="deny")
    record = await _drive_to_rest(coordinator, record, epoch)

    assert record.pending_gate is None
    assert record.status is TaskStatus.BLOCKED


@pytest.mark.asyncio
async def test_a_compensatable_delivery_resolves_its_gate_and_delivers(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    action = _deliver_action_payload("w-s1-1", rollback="delete_comment")
    runner.deliver_action = action

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    decided = next(
        event.payload_json
        for event in await task_store.read_events(task.id, project_id=PROJECT)
        if event.event_type is TaskEventType.GATE_DECIDED
        and event.payload_json["gate_id"] == "deliver:w-s1-1:1"
    )
    assert decided == {
        "gate_id": "deliver:w-s1-1:1",
        "decision": "allow",
        "action": action,
    }
    assert runner.delivered == [("w-s1-1", 1)]
    assert record.pending_gate is None
    events = await task_store.read_events(task.id, project_id=PROJECT)
    kinds = [event.event_type for event in events]
    assert (
        kinds.index(TaskEventType.ACTION_INTENT_RECORDED)
        < kinds.index(TaskEventType.ACTION_RESULT_RECORDED)
        < kinds.index(TaskEventType.OBSERVATION_RECORDED)
    )
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    assert result.payload_json["action_id"] == "deliver:w-s1-1:1"
    assert result.payload_json["work_id"] == "w-s1-1"


@pytest.mark.asyncio
async def test_a_delivery_receipt_with_a_foreign_action_id_still_terminates(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")
    runner.delivery_action_id = "foreign:delivery:receipt"

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert runner.delivered == [("w-s1-1", 1)]
    assert record.status is TaskStatus.COMPLETE
    events = await task_store.read_events(task.id, project_id=PROJECT)
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    assert result.payload_json["action_id"] == "deliver:w-s1-1:1"
    assert result.payload_json["status"] == "succeeded"
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert observation.payload_json["action_id"] == "deliver:w-s1-1:1"


@pytest.mark.asyncio
async def test_a_two_sink_report_work_delivers_both_sinks_before_completion(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback=None)
    runner.deliver_next_sink_versions[("w-s1-1", 1)] = 2
    runner.deliver_actions[("w-s1-1", 2)] = _deliver_action_payload(
        "w-s1-1", rollback="delete_comment"
    )

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert runner.delivered == [("w-s1-1", 1), ("w-s1-1", 2)]
    assert record.status is TaskStatus.COMPLETE
    events = await task_store.read_events(task.id, project_id=PROJECT)
    assert [
        event.payload_json["action_id"]
        for event in events
        if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    ] == ["deliver:w-s1-1:1", "deliver:w-s1-1:2"]


@pytest.mark.asyncio
async def test_a_lost_delivery_batch_uses_the_task_action_when_work_context_was_cleared(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")
    runner.clear_report_on_deliver = True

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    _lose_the_batch(monkeypatch, kind="deliver_report")
    with pytest.raises(RuntimeError):
        await _drive_to_rest(coordinator, record, epoch)
    record = (await task_store.load(task.id, project_id=PROJECT))[1]
    record = await _drive_to_rest(coordinator, record, epoch)

    assert runner.delivered == [("w-s1-1", 1)]
    assert record.status is TaskStatus.BLOCKED
    events = await task_store.read_events(task.id, project_id=PROJECT)
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert result.payload_json["action_id"] == "deliver:w-s1-1:1"
    assert result.payload_json["status"] == "blocked"
    assert observation.payload_json["check"] == "delivery_receipt"
    assert observation.payload_json["passed"] is None


@pytest.mark.asyncio
async def test_a_delivery_whose_batch_is_lost_asks_instead_of_delivering_twice(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    _lose_the_batch(monkeypatch, kind="deliver_report")
    with pytest.raises(RuntimeError):
        await _drive_to_rest(coordinator, record, epoch)
    record = (await task_store.load(task.id, project_id=PROJECT))[1]
    record = await _drive_to_rest(coordinator, record, epoch)

    assert runner.delivered == [("w-s1-1", 1)]
    assert record.status is TaskStatus.BLOCKED
    events = await task_store.read_events(task.id, project_id=PROJECT)
    kinds = [event.event_type for event in events]
    assert (
        kinds.index(TaskEventType.ACTION_INTENT_RECORDED)
        < kinds.index(TaskEventType.ACTION_RESULT_RECORDED)
        < kinds.index(TaskEventType.OBSERVATION_RECORDED)
    )
    result = next(
        event for event in events if event.event_type is TaskEventType.ACTION_RESULT_RECORDED
    )
    observation = next(
        event for event in events if event.event_type is TaskEventType.OBSERVATION_RECORDED
    )
    assert result.payload_json["action_id"] == "deliver:w-s1-1:1"
    assert result.payload_json["work_id"] == "w-s1-1"
    assert result.payload_json["status"] == "blocked"
    assert observation.payload_json["check"] == "delivery_receipt"
    assert observation.payload_json["passed"] is None
    assert (
        observation.payload_json["detail"]
        == "the delivery may have run before a crash; confirm the sink"
    )


@pytest.mark.asyncio
async def test_a_failed_delivery_post_check_opens_a_delete_comment_rollback_gate(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    comment_url = "https://github.com/o/r/issues/9#issuecomment-123"
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")
    runner.delivery_external_ref = comment_url
    runner.delivery_passed = False

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert record.pending_gate == "rollback:w-s1-1"
    events = await task_store.read_events(task.id, project_id=PROJECT)
    requested = next(
        event
        for event in events
        if event.event_type is TaskEventType.GATE_REQUESTED
        and event.payload_json["gate_id"] == "rollback:w-s1-1"
    )
    assert requested.payload_json["action"]["scope"] == comment_url
    assert requested.payload_json["action"]["rollback"] == "delete_comment"


@pytest.mark.asyncio
async def test_a_required_delivery_waits_for_a_project_admin(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path)
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")
    monkeypatch.setattr(
        coordinator,
        "_load",
        _fixed_task(
            task_store,
            task.model_copy(
                update={"authority": Authority(plan=GateMode.AUTO, deliver=GateMode.REQUIRE)}
            ),
        ),
    )

    record = await _drive_to_rest(coordinator, record, record.lease_epoch)
    assert record.pending_gate == "deliver:w-s1-1:1"
    assert runner.delivered == []
    events = await task_store.read_events(task.id, project_id=PROJECT)
    requested = next(
        event.payload_json
        for event in events
        if event.event_type is TaskEventType.GATE_REQUESTED
        and event.payload_json["gate_id"] == "deliver:w-s1-1:1"
    )
    assert requested["action"] == runner.deliver_action

    service = TaskService(store=task_store, artifact_store=LocalArtifactStore(root=tmp_path))
    record = await service.decide_gate(
        task.id,
        project_id=PROJECT,
        gate_id="deliver:w-s1-1:1",
        decision="allow",
        actor_ref="arda",
    )
    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert runner.delivered == [("w-s1-1", 1)]


@pytest.mark.asyncio
async def test_a_trigger_origin_task_can_never_auto_deliver(
    stores,  # noqa: F811
    tmp_path,
    monkeypatch,
) -> None:
    """Section 19: a non-human origin holds no automatic authority to deliver."""
    task_store, _work_store = stores
    task, record, runner, coordinator = await _seed(stores, tmp_path, origin=TaskOrigin.TRIGGER)
    monkeypatch.setattr(coordinator, "_load", _fixed_task(task_store, task))
    runner.statuses["s1"] = "READY_TO_DELIVER"
    runner.deliver_sink_versions["w-s1-1"] = 1
    runner.deliver_action = _deliver_action_payload("w-s1-1", rollback="delete_comment")

    epoch = await task_store.claim(task.id, project_id=PROJECT, owner="r", ttl_seconds=90)
    record = await _drive_to_rest(coordinator, record, epoch)

    assert task.authority.deliver is GateMode.REQUIRE
    assert record.pending_gate == "deliver:w-s1-1:1"
    assert runner.delivered == []
