# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Decision channels and their configuration (spec sections 15 and 16)."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

import httpx

from sagewai.work.profiles.software.github import GitHubClient, GitHubFactory, _parse_issue_url
from sagewai.work.tasks.decisions import (
    ChannelDeliveryError,
    ConsoleDecisionChannel,
    DecisionChannel,
    DecisionRequest,
    channel_error_detail,
)
from sagewai.work.tasks.events import TaskEvent, TaskEventType
from sagewai.work.tasks.models import (
    AttentionOwner,
    ReportTarget,
    SoftwareTarget,
    Task,
    TaskDefaults,
    TaskRecord,
)
from sagewai.work.tasks.store import StaleTaskError, TaskStore
from sagewai.work.tasks.writer import TaskWriter

logger = logging.getLogger("sagewai.work.tasks")

_WEBHOOK_TIMEOUT_SECONDS = 10.0
_CONFIG_TYPES = {"slack_webhook": "slack", "google_chat_webhook": "google_chat"}
_SEVERITY = {"now": ":rotating_light:", "today": ":warning:", "this_week": ":bell:"}


class ChannelNotConfiguredError(ValueError):
    """A project named a decision channel its configuration does not supply."""


class ChannelConfigStore(Protocol):
    """The notification channel store the encrypted webhook URLs live in.

    ``list_channel_configs`` is awaited by the resolver.
    """

    async def list_channel_configs(self, project_id: str | None = None) -> list[dict[str, Any]]: ...


@runtime_checkable
class TrackingDecisionChannel(Protocol):
    """A channel that establishes one durable per-Task reference worth projecting."""

    @property
    def name(self) -> str: ...

    async def notify(self, decision: DecisionRequest) -> str | None: ...

    async def track(self, task: Task, text: str) -> str | None: ...

    def established(self, task_id: str) -> str | None: ...


def _deep_link(base_url: str | None, decision: DecisionRequest) -> str | None:
    return None if base_url is None else f"{base_url.rstrip('/')}/tasks/{decision.task_id}"


def _due_line(decision: DecisionRequest) -> str:
    due = "no deadline" if decision.due_at is None else decision.due_at.isoformat()
    return f"urgency {decision.urgency}, due {due}"


async def _post(name: str, webhook_url: str, body: dict[str, Any]) -> None:
    """POST one webhook payload, raising an error that never carries the endpoint.

    ``response.raise_for_status()`` would put the full request URL in the message, and for an
    incoming webhook that URL is the credential itself.
    """
    async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT_SECONDS) as client:
        response = await client.post(webhook_url, json=body)
    if response.is_error:
        raise ChannelDeliveryError(f"{name} webhook returned HTTP {response.status_code}")


class SlackWebhookDecisionChannel:
    """Post one Needs-you item to a Slack incoming webhook."""

    name = "slack_webhook"

    def __init__(
        self,
        *,
        webhook_url: str,
        console_base_url: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._console_base_url = console_base_url

    async def notify(self, decision: DecisionRequest) -> str | None:
        link = _deep_link(self._console_base_url, decision)
        context = _due_line(decision)
        if link is not None:
            context = f"{context} — <{link}|decide in the console>"
        blocks: list[dict[str, Any]] = [
            {
                "type": "header",
                "text": {
                    "type": "plain_text",
                    "text": f"{_SEVERITY[decision.urgency]} Sagewai needs you",
                    "emoji": True,
                },
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": decision.summary}},
            {"type": "context", "elements": [{"type": "mrkdwn", "text": context}]},
        ]
        if decision.evidence_refs:
            blocks.append(
                {
                    "type": "context",
                    "elements": [{"type": "mrkdwn", "text": "\n".join(decision.evidence_refs[:8])}],
                }
            )
        await _post(self.name, self._webhook_url, {"blocks": blocks})
        return f"{self.name}:{decision.task_id}:{decision.attention_id}"


class GoogleChatWebhookDecisionChannel:
    """Post one Needs-you item to a Google Chat incoming webhook."""

    name = "google_chat_webhook"

    def __init__(
        self,
        *,
        webhook_url: str,
        console_base_url: str | None = None,
    ) -> None:
        self._webhook_url = webhook_url
        self._console_base_url = console_base_url

    async def notify(self, decision: DecisionRequest) -> str | None:
        lines = ["*Sagewai needs you*", decision.summary, _due_line(decision)]
        link = _deep_link(self._console_base_url, decision)
        if link is not None:
            lines.append(link)
        lines.extend(decision.evidence_refs[:8])
        await _post(self.name, self._webhook_url, {"text": "\n".join(lines)})
        return f"{self.name}:{decision.task_id}:{decision.attention_id}"


class GitHubIssueDecisionChannel:
    """One tracking issue per Task: created on first use, commented on thereafter."""

    name = "github_issue"

    def __init__(self, *, store: TaskStore, github_factory: GitHubFactory) -> None:
        self._store = store
        self._github_factory = github_factory
        self._established: dict[str, str] = {}

    def established(self, task_id: str) -> str | None:
        """The issue URL this channel established during the notify just performed."""
        return self._established.pop(task_id, None)

    async def notify(self, decision: DecisionRequest) -> str | None:
        self._established.pop(decision.task_id, None)
        loaded = await self._store.load(decision.task_id, project_id=decision.project_id)
        if loaded is None:
            return None
        task, record = loaded
        established = await self._establish_issue(task, record)
        if established is None:
            return None
        github, issue_url = established
        comment = await github.comment_issue(issue_url, _tracking_comment(decision))
        return comment.url

    async def track(self, task: Task, text: str) -> str | None:
        self._established.pop(task.id, None)
        loaded = await self._store.load(task.id, project_id=task.project_id)
        if loaded is None:
            return None
        stored_task, record = loaded
        established = await self._establish_issue(stored_task, record)
        if established is None:
            return None
        github, issue_url = established
        comment = await github.comment_issue(issue_url, text)
        return comment.url

    async def _establish_issue(
        self, task: Task, record: TaskRecord
    ) -> tuple[GitHubClient, str] | None:
        issue_url = task.tracking_issue_url or record.tracking_issue_url
        if issue_url is None:
            target = _issue_target(task)
            if target is None:
                return None
            github = self._github_factory(task)
            owner, repo = target
            label = f"sagewai-task:{task.id}"
            existing = await github.list_labeled_issues(owner=owner, repo=repo, label=label)
            issue_url = (
                existing[0].url
                if existing
                else (
                    await github.create_issue(
                        owner=owner,
                        repo=repo,
                        title=f"Sagewai Task: {task.title}",
                        body=(
                            f"Sagewai is coordinating this Task.\n\n{task.brief_summary}\n\n"
                            f"sagewai-task: {task.id}\n"
                        ),
                        labels=(label,),
                    )
                ).url
            )
            self._established[task.id] = issue_url
            return github, issue_url
        return self._github_factory(task), issue_url


def _issue_target(task: Task) -> tuple[str, str] | None:
    """Where the tracking issue lives: the software repository, or the report's sink issue."""
    if isinstance(task.target, SoftwareTarget):
        return task.target.owner, task.target.repo
    if isinstance(task.target, ReportTarget):
        for sink in task.target.sinks:
            if sink.kind == "github_issue" and sink.issue_url:
                owner, repo, _number = _parse_issue_url(sink.issue_url)
                return owner, repo
    return None


def _tracking_comment(decision: DecisionRequest) -> str:
    lines = [f"**{decision.summary}**", "", _due_line(decision)]
    lines.extend(f"- {ref}" for ref in decision.evidence_refs[:16])
    return "\n".join(lines)


async def build_decision_channels(
    *,
    defaults: TaskDefaults,
    config_store: ChannelConfigStore | None = None,
    tracking_channel: DecisionChannel | None = None,
    console_base_url: str | None = None,
) -> tuple[DecisionChannel, ...]:
    """Resolve TaskDefaults.decision_channels into instances, in the order named.

    A system-wide config row is outside the scoped project query and therefore fails closed.
    """
    configs: list[dict[str, Any]] = []
    if config_store is not None:
        configs = await config_store.list_channel_configs(defaults.project_id)
    channels: list[DecisionChannel] = []
    for name in defaults.decision_channels:
        if name == "console":
            channels.append(ConsoleDecisionChannel())
        elif name == "github_issue":
            if tracking_channel is None:
                raise ChannelNotConfiguredError("github_issue needs a GitHub client")
            channels.append(tracking_channel)
        elif name in _CONFIG_TYPES:
            url = _webhook_url(configs, _CONFIG_TYPES[name], name)
            builder = (
                SlackWebhookDecisionChannel
                if name == "slack_webhook"
                else GoogleChatWebhookDecisionChannel
            )
            channels.append(builder(webhook_url=url, console_base_url=console_base_url))
        else:
            raise ChannelNotConfiguredError(f"unknown decision channel: {name}")
    return tuple(channels)


def _webhook_url(configs: Sequence[dict[str, Any]], channel_type: str, name: str) -> str:
    for config in configs:
        if config.get("channel_type") != channel_type or not config.get("enabled", True):
            continue
        url = config.get("webhook_url")
        if url:
            return str(url)
    raise ChannelNotConfiguredError(f"{name} has no enabled webhook_url for this project")


@dataclass(frozen=True)
class _OpenItem:
    """The one Needs-you item a Task is still waiting on, rebuilt from its own events."""

    attention_id: str
    summary: str
    urgency: str
    due_at: datetime
    evidence_refs: tuple[str, ...]
    presented: tuple[tuple[str, datetime], ...]


class DecisionEscalation:
    """Re-notify the next channel after half the remaining time (spec section 15)."""

    def __init__(
        self,
        *,
        store: TaskStore,
        channels: Callable[[str], Awaitable[Sequence[DecisionChannel]]],
    ) -> None:
        self._store = store
        self._channels = channels

    async def run(self, *, project_id: str, now: datetime) -> int:
        escalated = 0
        channels: Sequence[DecisionChannel] | None = None
        for record in await self._store.list_records(project_id=project_id):
            if record.attention_owner is not AttentionOwner.USER:
                continue
            item = _open_item(await self._store.read_events(record.task_id, project_id=project_id))
            if item is None or item.urgency == "now":
                continue
            if now - item.presented[-1][1] < (item.due_at - item.presented[-1][1]) / 2:
                continue
            if channels is None:
                channels = await self._channels(project_id)
            carried = {name for name, _at in item.presented}
            nxt = next((channel for channel in channels if channel.name not in carried), None)
            if nxt is None:
                continue
            escalated += await self._present(record, item, nxt, now)
        return escalated

    async def _present(
        self, record: TaskRecord, item: _OpenItem, channel: DecisionChannel, now: datetime
    ) -> int:
        decision = DecisionRequest(
            project_id=record.project_id,
            task_id=record.task_id,
            attention_id=item.attention_id,
            summary=item.summary,
            urgency=item.urgency,
            due_at=item.due_at,
            evidence_refs=item.evidence_refs,
        )
        command_id = f"notify:{channel.name}:{item.attention_id}"
        if not await self._store.record_command(
            task_id=record.task_id,
            project_id=record.project_id,
            command_id=command_id,
            payload={"decision": decision.model_dump(mode="json"), "escalated": True},
        ):
            return 0
        try:
            reference = await channel.notify(decision)
        except Exception as exc:
            logger.warning(
                "escalation channel failed",
                extra={
                    "event": "task.escalate.failed",
                    "task": record.task_id,
                    "channel": channel.name,
                    "attention_id": item.attention_id,
                    "error": channel_error_detail(exc),
                },
            )
            await self._store.delete_command(
                task_id=record.task_id, project_id=record.project_id, command_id=command_id
            )
            return 0
        if reference is None:
            await self._store.delete_command(
                task_id=record.task_id, project_id=record.project_id, command_id=command_id
            )
            return 0
        try:
            await TaskWriter(self._store).append(
                record,
                [
                    (
                        TaskEventType.NOTIFICATION_PRESENTED,
                        {
                            "channel": channel.name,
                            "ref": reference,
                            "attention_id": item.attention_id,
                            "urgency": item.urgency,
                            "due_at": item.due_at.isoformat(),
                            "summary": item.summary,
                            "evidence_refs": list(item.evidence_refs),
                        },
                    )
                ],
                now=now,
            )
        except StaleTaskError:
            await self._store.delete_command(
                task_id=record.task_id, project_id=record.project_id, command_id=command_id
            )
            return 0
        return 1


def _open_item(events: Sequence[TaskEvent]) -> _OpenItem | None:
    """The item the Task still owes an answer on, or None.

    A Task has at most one open Needs-you item at a time: ``decide`` returns ``None`` while
    ``pending_gate`` is set or the status is in ``_WAITING`` (``decide.py:266-267``), so the
    latest presentation is the open one and everything before it was answered. Escalating the
    whole history would push long-settled decisions onto a second channel.
    """
    ordered = sorted(events, key=lambda event: event.sequence)
    latest = next(
        (
            event
            for event in reversed(ordered)
            if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
        ),
        None,
    )
    if latest is None or latest.payload_json.get("due_at") is None:
        return None
    attention_id = str(latest.payload_json["attention_id"])
    if any(
        event.event_type is TaskEventType.GATE_DECIDED
        and str(event.payload_json["gate_id"]) == attention_id
        and event.sequence > latest.sequence
        for event in ordered
    ):
        return None
    presented = tuple(
        (str(event.payload_json["channel"]), event.created_at)
        for event in ordered
        if event.event_type is TaskEventType.NOTIFICATION_PRESENTED
        and str(event.payload_json["attention_id"]) == attention_id
    )
    return _OpenItem(
        attention_id=attention_id,
        summary=str(latest.payload_json["summary"]),
        urgency=str(latest.payload_json["urgency"]),
        due_at=datetime.fromisoformat(str(latest.payload_json["due_at"])),
        evidence_refs=tuple(latest.payload_json.get("evidence_refs") or ()),
        presented=presented,
    )


__all__ = [
    "ChannelConfigStore",
    "ChannelNotConfiguredError",
    "DecisionEscalation",
    "GitHubIssueDecisionChannel",
    "GoogleChatWebhookDecisionChannel",
    "SlackWebhookDecisionChannel",
    "TrackingDecisionChannel",
    "build_decision_channels",
]
