# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Chat webhooks, the configured-channel resolver, and urgency escalation (section 15)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from sagewai.work.tasks.channels import (
    ChannelNotConfiguredError,
    DecisionEscalation,
    GoogleChatWebhookDecisionChannel,
    SlackWebhookDecisionChannel,
    _open_item,
    build_decision_channels,
)
from sagewai.work.tasks.decisions import (
    ChannelDeliveryError,
    DecisionRequest,
)
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import Task, TaskDefaults, TaskRecord, TaskStatus
from sagewai.work.tasks.store import TaskStore
from sagewai.work.tasks.writer import TaskWriter, status_entry
from tests.db.conftest import dialect_engine  # noqa: F401
from tests.work.tasks.test_store import _record, _task

NOW = datetime(2026, 9, 3, 9, 0, tzinfo=timezone.utc)


def _decision(**overrides) -> DecisionRequest:
    values = {
        "project_id": "project-a",
        "task_id": "t1",
        "attention_id": "gate:merge:w1:7",
        "summary": "Approve merge of PR #7",
        "urgency": "today",
        "due_at": NOW + timedelta(hours=24),
        "evidence_refs": ("https://github.com/o/r/pull/7",),
    }
    values.update(overrides)
    return DecisionRequest(**values)


def _event(sequence: int, event_type: TaskEventType, payload: dict) -> TaskEvent:
    return TaskEvent(
        id=f"t1:{sequence}",
        project_id="project-a",
        task_id="t1",
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="coordinator",
        payload_json=payload,
        created_at=NOW,
    )


def _store_event(
    task: Task,
    sequence: int,
    event_type: TaskEventType,
    payload: dict,
    *,
    created_at: datetime = NOW,
) -> TaskEvent:
    return TaskEvent(
        id=f"{task.id}:{sequence}",
        project_id=task.project_id,
        task_id=task.id,
        sequence=sequence,
        event_type=event_type,
        actor_type="system",
        actor_ref="test",
        payload_json=payload,
        created_at=created_at,
    )


class _RecordingChannel:
    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[DecisionRequest] = []

    async def notify(self, decision: DecisionRequest) -> str | None:
        self.calls.append(decision)
        return f"{self.name}:{decision.task_id}:{decision.attention_id}:{len(self.calls)}"


class _FlakyChannel(_RecordingChannel):
    def __init__(self, name: str, *, failures: int) -> None:
        super().__init__(name)
        self._failures = failures

    async def notify(self, decision: DecisionRequest) -> str | None:
        self.calls.append(decision)
        if len(self.calls) <= self._failures:
            raise RuntimeError("webhook unavailable")
        return f"{self.name}:{decision.task_id}:{decision.attention_id}:{len(self.calls)}"


class _StalingChannel(_RecordingChannel):
    def __init__(self, name: str, *, store: TaskStore, task_id: str, project_id: str) -> None:
        super().__init__(name)
        self._store = store
        self._task_id = task_id
        self._project_id = project_id

    async def notify(self, decision: DecisionRequest) -> str | None:
        self.calls.append(decision)
        if len(self.calls) == 1:
            loaded = await self._store.load(self._task_id, project_id=self._project_id)
            assert loaded is not None
            await TaskWriter(self._store).append(
                loaded[1],
                [
                    (
                        TaskEventType.TASK_MESSAGE,
                        {"author": "system", "text": "advanced stream", "refs": []},
                    )
                ],
                now=NOW + timedelta(minutes=1),
            )
        return f"{self.name}:{decision.task_id}:{decision.attention_id}:{len(self.calls)}"


class _NullChannel(_RecordingChannel):
    async def notify(self, decision: DecisionRequest) -> str | None:
        self.calls.append(decision)
        return None


@pytest.fixture
async def store(dialect_engine) -> TaskStore:  # noqa: F811
    result = TaskStore(engine=dialect_engine)
    await result.init()
    return result


def _channels(*channels: _RecordingChannel):
    async def resolve(_project_id: str):
        return channels

    return resolve


async def _seed_open_item(
    store: TaskStore,
    *,
    task_id: str,
    project_id: str,
    attention_id: str = "gate:1",
    channel: str = "console",
    urgency: str = "today",
    summary: str = "Approve merge",
    evidence_refs: tuple[str, ...] = ("pr://7", "work://w1"),
    presented_at: datetime = NOW,
    due_at: datetime | None = None,
) -> TaskRecord:
    task = _task(task_id, project_id=project_id)
    record = await store.create(
        task,
        events=(_store_event(task, 1, TaskEventType.TASK_CREATED, {"title": task.title}),),
        record=_record(task),
    )
    due = due_at or presented_at + timedelta(hours=24)
    return await TaskWriter(store).append(
        record,
        [
            (
                TaskEventType.NOTIFICATION_PRESENTED,
                {
                    "channel": channel,
                    "ref": f"{channel}:{attention_id}",
                    "attention_id": attention_id,
                    "urgency": urgency,
                    "due_at": due.isoformat(),
                    "summary": summary,
                    "evidence_refs": list(evidence_refs),
                },
            ),
            status_entry(record, TaskStatus.BLOCKED),
        ],
        now=presented_at,
    )


@pytest.mark.asyncio
async def test_slack_posts_block_kit_and_returns_a_reference(respx_mock) -> None:
    route = respx_mock.post("https://hooks.slack.com/services/T/B/X").mock(
        return_value=httpx.Response(200, text="ok")
    )
    channel = SlackWebhookDecisionChannel(
        webhook_url="https://hooks.slack.com/services/T/B/X",
        console_base_url="https://sagewai.example/app",
    )
    refs = tuple(f"evidence-{index}" for index in range(10))

    reference = await channel.notify(_decision(evidence_refs=refs))

    body = json.loads(route.calls[0].request.read().decode())
    assert [block["type"] for block in body["blocks"]] == [
        "header",
        "section",
        "context",
        "context",
    ]
    assert body["blocks"][0]["text"]["text"] == ":warning: Sagewai needs you"
    assert body["blocks"][1]["text"]["text"] == "Approve merge of PR #7"
    assert body["blocks"][2]["elements"][0]["text"].endswith(
        "<https://sagewai.example/app/tasks/t1|decide in the console>"
    )
    assert body["blocks"][3]["elements"][0]["text"].splitlines() == list(refs[:8])
    assert reference == "slack_webhook:t1:gate:merge:w1:7"


@pytest.mark.asyncio
async def test_google_chat_posts_text_with_due_line_and_console_link(respx_mock) -> None:
    webhook = "https://chat.googleapis.com/v1/spaces/S/messages"
    route = respx_mock.post(webhook).mock(return_value=httpx.Response(200, text="ok"))
    channel = GoogleChatWebhookDecisionChannel(
        webhook_url=webhook, console_base_url="https://sagewai.example/app"
    )

    reference = await channel.notify(_decision())

    assert json.loads(route.calls[0].request.read().decode()) == {
        "text": "\n".join(
            [
                "*Sagewai needs you*",
                "Approve merge of PR #7",
                "urgency today, due 2026-09-04T09:00:00+00:00",
                "https://sagewai.example/app/tasks/t1",
                "https://github.com/o/r/pull/7",
            ]
        )
    }
    assert reference == "google_chat_webhook:t1:gate:merge:w1:7"


@pytest.mark.asyncio
async def test_google_chat_omits_the_console_link_when_no_base_url(respx_mock) -> None:
    webhook = "https://chat.googleapis.com/v1/spaces/S/messages"
    route = respx_mock.post(webhook).mock(return_value=httpx.Response(200, text="ok"))
    channel = GoogleChatWebhookDecisionChannel(webhook_url=webhook)

    await channel.notify(_decision(evidence_refs=()))

    assert json.loads(route.calls[0].request.read().decode()) == {
        "text": "\n".join(
            [
                "*Sagewai needs you*",
                "Approve merge of PR #7",
                "urgency today, due 2026-09-04T09:00:00+00:00",
            ]
        )
    }


@pytest.mark.asyncio
async def test_google_chat_raises_an_error_that_never_carries_the_webhook(respx_mock) -> None:
    webhook = "https://chat.googleapis.com/v1/spaces/S/SECRET-TOKEN"
    respx_mock.post(webhook).mock(return_value=httpx.Response(500, text="nope"))
    channel = GoogleChatWebhookDecisionChannel(webhook_url=webhook)

    with pytest.raises(ChannelDeliveryError) as raised:
        await channel.notify(_decision())

    assert str(raised.value) == "google_chat_webhook webhook returned HTTP 500"
    assert "SECRET-TOKEN" not in str(raised.value)


def test_the_open_item_is_the_latest_unanswered_one() -> None:
    def presented(sequence: int, attention_id: str, channel: str) -> TaskEvent:
        return _event(
            sequence,
            TaskEventType.NOTIFICATION_PRESENTED,
            {
                "channel": channel,
                "ref": f"{channel}:{attention_id}",
                "attention_id": attention_id,
                "urgency": "today",
                "due_at": (NOW + timedelta(hours=24)).isoformat(),
                "summary": f"Approve {attention_id}",
                "evidence_refs": [],
            },
        )

    answered = [
        presented(1, "plan:t1:1", "console"),
        _event(2, TaskEventType.GATE_DECIDED, {"gate_id": "plan:t1:1", "decision": "allow"}),
    ]
    assert _open_item(answered) is None

    open_now = [*answered, presented(3, "deliver:w1:1", "console")]
    item = _open_item(open_now)
    assert item.attention_id == "deliver:w1:1"
    assert item.summary == "Approve deliver:w1:1"
    assert [name for name, _at in item.presented] == ["console"]


def test_the_open_item_preserves_presented_channels() -> None:
    events = [
        _event(
            index,
            TaskEventType.NOTIFICATION_PRESENTED,
            {
                "channel": channel,
                "ref": f"{channel}:gate:1",
                "attention_id": "gate:1",
                "urgency": "today",
                "due_at": (NOW + timedelta(hours=24)).isoformat(),
                "summary": "Approve merge",
                "evidence_refs": [],
            },
        )
        for index, channel in enumerate(("console", "slack_webhook"), start=1)
    ]

    item = _open_item(events)
    assert {name for name, _at in item.presented} == {"console", "slack_webhook"}


@pytest.mark.asyncio
async def test_escalation_run_notifies_next_channel_after_half_the_remaining_time(
    store: TaskStore,
) -> None:
    due_at = NOW + timedelta(hours=24)
    await _seed_open_item(
        store,
        task_id="task-escalate",
        project_id="project-escalate",
        due_at=due_at,
    )
    first = _RecordingChannel("console")
    second = _RecordingChannel("slack_webhook")
    escalation = DecisionEscalation(store=store, channels=_channels(first, second))

    assert (
        await escalation.run(
            project_id="project-escalate", now=NOW + timedelta(hours=11, minutes=59)
        )
        == 0
    )
    assert second.calls == []

    escalated_at = NOW + timedelta(hours=12, seconds=1)
    assert await escalation.run(project_id="project-escalate", now=escalated_at) == 1
    assert len(second.calls) == 1
    decision = second.calls[0]
    assert decision.attention_id == "gate:1"
    assert decision.summary == "Approve merge"
    assert decision.evidence_refs == ("pr://7", "work://w1")
    assert decision.due_at == due_at
    events = [
        event
        for event in await store.read_events("task-escalate", project_id="project-escalate")
        if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    ]
    assert len(events) == 2
    appended = events[-1]
    assert appended.created_at == escalated_at
    assert appended.payload_json == {
        "channel": "slack_webhook",
        "ref": "slack_webhook:task-escalate:gate:1:1",
        "attention_id": "gate:1",
        "urgency": "today",
        "due_at": due_at.isoformat(),
        "summary": "Approve merge",
        "evidence_refs": ["pr://7", "work://w1"],
    }
    assert await escalation.run(project_id="project-escalate", now=NOW + timedelta(hours=18)) == 0
    assert len(second.calls) == 1


@pytest.mark.asyncio
async def test_escalation_run_honors_the_present_once_receipt(store: TaskStore) -> None:
    await _seed_open_item(store, task_id="task-receipt", project_id="project-receipt")
    channel = _RecordingChannel("slack_webhook")
    await store.record_command(
        task_id="task-receipt",
        project_id="project-receipt",
        command_id="notify:slack_webhook:gate:1",
        payload={"decision": _decision(task_id="task-receipt").model_dump(mode="json")},
    )
    escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), channel)
    )

    assert await escalation.run(project_id="project-receipt", now=NOW + timedelta(hours=18)) == 0
    assert channel.calls == []


@pytest.mark.asyncio
async def test_escalation_run_skips_now_urgency_items(store: TaskStore) -> None:
    await _seed_open_item(
        store,
        task_id="task-now",
        project_id="project-now",
        attention_id="budget:1",
        urgency="now",
        due_at=NOW,
    )
    now_channel = _RecordingChannel("slack_webhook")
    now_escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), now_channel)
    )
    assert await now_escalation.run(project_id="project-now", now=NOW + timedelta(hours=1)) == 0
    assert now_channel.calls == []


@pytest.mark.asyncio
async def test_escalation_run_recovers_a_failed_channel_receipt(store: TaskStore) -> None:
    escalated_at = NOW + timedelta(hours=12, seconds=1)
    await _seed_open_item(store, task_id="task-raise", project_id="project-raise")
    flaky = _FlakyChannel("slack_webhook", failures=1)
    flaky_escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), flaky)
    )
    assert await flaky_escalation.run(project_id="project-raise", now=escalated_at) == 0
    events = await store.read_events("task-raise", project_id="project-raise")
    assert not any(
        event.event_type is TaskEventType.NOTIFICATION_PRESENTED
        and event.payload_json["channel"] == "slack_webhook"
        for event in events
    )
    assert await flaky_escalation.run(project_id="project-raise", now=escalated_at) == 1
    assert len(flaky.calls) == 2


@pytest.mark.asyncio
async def test_escalation_run_continues_after_a_broken_channel(store: TaskStore) -> None:
    escalated_at = NOW + timedelta(hours=12, seconds=1)
    await _seed_open_item(store, task_id="task-next", project_id="project-next")
    broken = _FlakyChannel("slack_webhook", failures=99)
    healthy = _RecordingChannel("google_chat_webhook")
    escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), broken, healthy)
    )

    assert await escalation.run(project_id="project-next", now=escalated_at) == 1
    assert len(broken.calls) == 1
    assert len(healthy.calls) == 1
    events = await store.read_events("task-next", project_id="project-next")
    assert [
        event.payload_json["channel"]
        for event in events
        if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
    ] == ["console", "google_chat_webhook"]


@pytest.mark.asyncio
async def test_escalation_run_recovers_a_none_reference_receipt(store: TaskStore) -> None:
    await _seed_open_item(store, task_id="task-none", project_id="project-none")
    missing = _NullChannel("github_issue")
    escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), missing)
    )

    assert await escalation.run(project_id="project-none", now=NOW + timedelta(hours=18)) == 0
    assert len(missing.calls) == 1
    assert await store.record_command(
        task_id="task-none",
        project_id="project-none",
        command_id="notify:github_issue:gate:1",
        payload={"probe": True},
    )


@pytest.mark.asyncio
async def test_escalation_run_recovers_a_stale_append_receipt(store: TaskStore) -> None:
    escalated_at = NOW + timedelta(hours=12, seconds=1)
    await _seed_open_item(store, task_id="task-stale", project_id="project-stale")
    staling = _StalingChannel(
        "slack_webhook", store=store, task_id="task-stale", project_id="project-stale"
    )
    stale_escalation = DecisionEscalation(
        store=store, channels=_channels(_RecordingChannel("console"), staling)
    )
    assert await stale_escalation.run(project_id="project-stale", now=escalated_at) == 0
    events = await store.read_events("task-stale", project_id="project-stale")
    assert not any(
        event.event_type is TaskEventType.NOTIFICATION_PRESENTED
        and event.payload_json["channel"] == "slack_webhook"
        for event in events
    )
    assert await stale_escalation.run(project_id="project-stale", now=escalated_at) == 1
    assert len(staling.calls) == 2


@pytest.mark.asyncio
async def test_the_resolver_builds_the_named_channels_in_order() -> None:
    class _Configs:
        async def list_channel_configs(self, project_id=None):
            return [
                {
                    "channel_type": "slack",
                    "enabled": False,
                    "project_id": project_id,
                    "webhook_url": "https://hooks.slack.com/services/disabled",
                },
                {
                    "channel_type": "slack",
                    "enabled": True,
                    "project_id": project_id,
                    "webhook_url": "https://hooks.slack.com/services/T/B/X",
                },
                {
                    "channel_type": "google_chat",
                    "enabled": True,
                    "project_id": project_id,
                    "webhook_url": "https://chat.googleapis.com/v1/spaces/S/messages",
                },
            ]

    defaults = TaskDefaults(
        project_id="project-a",
        decision_channels=("console", "google_chat_webhook", "slack_webhook"),
    )
    channels = await build_decision_channels(defaults=defaults, config_store=_Configs())

    assert [channel.name for channel in channels] == [
        "console",
        "google_chat_webhook",
        "slack_webhook",
    ]
    assert (
        getattr(channels[1], "_webhook_url") == "https://chat.googleapis.com/v1/spaces/S/messages"
    )
    assert getattr(channels[2], "_webhook_url") == "https://hooks.slack.com/services/T/B/X"


@pytest.mark.asyncio
async def test_an_unconfigured_channel_fails_closed_to_console(caplog) -> None:
    class _Empty:
        async def list_channel_configs(self, project_id=None):
            return []

    defaults = TaskDefaults(project_id="project-a", decision_channels=("console", "slack_webhook"))
    with caplog.at_level(logging.WARNING, logger="sagewai.work.tasks"):
        channels = await build_decision_channels(defaults=defaults, config_store=_Empty())

    assert [channel.name for channel in channels] == ["console"]
    assert [
        getattr(record, "channel", None)
        for record in caplog.records
        if getattr(record, "event", None) == "task.channel.unconfigured"
    ] == ["slack_webhook"]
    assert "slack_webhook" in caplog.text
    assert "hooks.slack" not in caplog.text


@pytest.mark.asyncio
async def test_a_non_https_webhook_value_fails_closed_to_console(caplog) -> None:
    class _Configs:
        async def list_channel_configs(self, project_id=None):
            return [
                {
                    "channel_type": "slack",
                    "enabled": True,
                    "project_id": project_id,
                    "webhook_url": "gAAAAABciphertext",
                }
            ]

    defaults = TaskDefaults(project_id="project-a", decision_channels=("slack_webhook",))
    with caplog.at_level(logging.WARNING, logger="sagewai.work.tasks"):
        channels = await build_decision_channels(defaults=defaults, config_store=_Configs())

    assert [channel.name for channel in channels] == ["console"]
    assert [
        getattr(record, "channel", None)
        for record in caplog.records
        if getattr(record, "event", None) == "task.channel.unconfigured"
    ] == ["slack_webhook"]
    assert "gAAAAABciphertext" not in caplog.text


@pytest.mark.asyncio
async def test_an_unknown_channel_name_fails_closed_to_console(caplog) -> None:
    defaults = TaskDefaults(project_id="project-a", decision_channels=("console", "teams_webhook"))
    with caplog.at_level(logging.WARNING, logger="sagewai.work.tasks"):
        channels = await build_decision_channels(defaults=defaults)

    assert [channel.name for channel in channels] == ["console"]
    assert [
        getattr(record, "channel", None)
        for record in caplog.records
        if getattr(record, "event", None) == "task.channel.unconfigured"
    ] == ["teams_webhook"]


@pytest.mark.asyncio
async def test_tracking_channel_still_requires_the_tracking_channel() -> None:
    defaults = TaskDefaults(project_id="project-a", decision_channels=("console", "github_issue"))
    with pytest.raises(ChannelNotConfiguredError, match="github_issue needs a GitHub client"):
        await build_decision_channels(defaults=defaults)
