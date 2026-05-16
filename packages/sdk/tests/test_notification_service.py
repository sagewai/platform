# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the notification system.

Covers: service dispatch, project override resolution, trigger routing,
history recording, channel failure handling, and hook integration.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sagewai.notifications.channels.base import NotificationChannel
from sagewai.notifications.channels.email import SMTPChannel
from sagewai.notifications.channels.inapp import InAppChannel
from sagewai.notifications.channels.slack import SlackWebhookChannel
from sagewai.notifications.hooks import (
    create_budget_notification_hook,
    create_workflow_notification_hook,
)
from sagewai.notifications.models import NotificationChannelConfig, NotificationRecord
from sagewai.notifications.service import NotificationService
from sagewai.notifications.stores import InMemoryNotificationStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class MockChannel(NotificationChannel):
    """A test channel that records calls and optionally fails."""

    def __init__(self, *, should_fail: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.should_fail = should_fail

    async def send(
        self,
        title: str,
        body: str,
        severity: str,
        metadata: dict[str, Any],
    ) -> bool:
        self.calls.append({
            "title": title,
            "body": body,
            "severity": severity,
            "metadata": metadata,
        })
        return not self.should_fail


class FakeEvent:
    """Mimics AgentEvent enum member."""

    def __init__(self, value: str) -> None:
        self.value = value


# ---------------------------------------------------------------------------
# NotificationService tests
# ---------------------------------------------------------------------------


class TestNotificationService:
    """Test the core NotificationService dispatch logic."""

    @pytest.mark.asyncio
    async def test_dispatch_to_registered_channels(self) -> None:
        """Service dispatches to all channels mapped to a trigger."""
        store = InMemoryNotificationStore()
        svc = NotificationService(store=store)

        email_ch = MockChannel()
        slack_ch = MockChannel()
        inapp_ch = MockChannel()

        svc.register_channel("email", email_ch)
        svc.register_channel("slack", slack_ch)
        svc.register_channel("in_app", inapp_ch)

        results = await svc.notify(
            trigger="budget_warning",
            title="Budget alert",
            body="Limit approaching",
            severity="warning",
        )

        assert results == [True, True, True]
        assert len(email_ch.calls) == 1
        assert len(slack_ch.calls) == 1
        assert len(inapp_ch.calls) == 1
        assert email_ch.calls[0]["title"] == "Budget alert"

    @pytest.mark.asyncio
    async def test_unknown_trigger_returns_empty(self) -> None:
        """Unrecognized triggers route to no channels."""
        svc = NotificationService()
        results = await svc.notify("unknown_event", "test", "test")
        assert results == []

    @pytest.mark.asyncio
    async def test_missing_channel_is_skipped(self) -> None:
        """If a channel type is in routing but not registered, it's skipped."""
        svc = NotificationService()
        svc.register_channel("in_app", MockChannel())
        # budget_warning routes to email, slack, in_app — but only in_app is registered
        results = await svc.notify("budget_warning", "test", "test")
        assert results == [True]  # only in_app delivered

    @pytest.mark.asyncio
    async def test_history_recording(self) -> None:
        """Notification results are recorded in the store."""
        store = InMemoryNotificationStore()
        svc = NotificationService(store=store)
        svc.register_channel("in_app", MockChannel())

        await svc.notify("budget_warning", "Test", "Body", severity="info")

        history = store.list_history(limit=10)
        assert len(history) == 1
        assert history[0]["title"] == "Test"
        assert history[0]["delivered"] is True

    @pytest.mark.asyncio
    async def test_channel_failure_doesnt_crash(self) -> None:
        """A failing channel returns False but doesn't break the service."""
        store = InMemoryNotificationStore()
        svc = NotificationService(store=store)

        fail_ch = MockChannel(should_fail=True)
        ok_ch = MockChannel()

        svc.register_channel("email", fail_ch)
        svc.register_channel("in_app", ok_ch)

        results = await svc.notify("budget_warning", "Test", "Body")
        # email=False, slack missing (skipped), in_app=True
        assert False in results
        assert True in results

    @pytest.mark.asyncio
    async def test_channel_exception_doesnt_crash(self) -> None:
        """A channel that raises is caught gracefully."""

        class CrashChannel(NotificationChannel):
            async def send(self, title, body, severity, metadata):
                raise RuntimeError("boom")

        svc = NotificationService()
        svc.register_channel("email", CrashChannel())
        svc.register_channel("in_app", MockChannel())

        results = await svc.notify("budget_warning", "Test", "Body")
        assert results[0] is False  # email crashed
        assert results[1] is True   # in_app ok


# ---------------------------------------------------------------------------
# Project override tests
# ---------------------------------------------------------------------------


class TestProjectOverride:
    """Test project-specific channel resolution."""

    @pytest.mark.asyncio
    async def test_project_channel_overrides_system(self) -> None:
        """A project-specific channel takes priority over the system default."""
        svc = NotificationService()

        system_ch = MockChannel()
        project_ch = MockChannel()

        svc.register_channel("in_app", system_ch)
        svc.register_channel("in_app", project_ch, project_id="t1")

        # Routing for approval_requested only goes to in_app
        await svc.notify(
            "approval_requested",
            "Approval needed",
            "Please review",
            project_id="t1",
        )

        assert len(project_ch.calls) == 1
        assert len(system_ch.calls) == 0

    @pytest.mark.asyncio
    async def test_falls_back_to_system_when_no_project_channel(self) -> None:
        """If no project override exists, falls back to system channel."""
        svc = NotificationService()

        system_ch = MockChannel()
        svc.register_channel("in_app", system_ch)

        await svc.notify(
            "approval_requested",
            "Approval needed",
            "Review",
            project_id="t2",
        )

        assert len(system_ch.calls) == 1


# ---------------------------------------------------------------------------
# Trigger routing tests
# ---------------------------------------------------------------------------


class TestTriggerRouting:
    """Test custom trigger routing configuration."""

    @pytest.mark.asyncio
    async def test_custom_routing(self) -> None:
        """Custom trigger routing overrides defaults."""
        svc = NotificationService()
        email_ch = MockChannel()
        slack_ch = MockChannel()

        svc.register_channel("email", email_ch)
        svc.register_channel("slack", slack_ch)

        # Override: budget_warning only goes to email
        svc.set_trigger_routing("budget_warning", ["email"])

        await svc.notify("budget_warning", "Alert", "Limit")

        assert len(email_ch.calls) == 1
        assert len(slack_ch.calls) == 0

    @pytest.mark.asyncio
    async def test_get_trigger_routing(self) -> None:
        """get_trigger_routing returns current config."""
        svc = NotificationService()
        routing = svc.get_trigger_routing()
        assert "budget_warning" in routing
        assert "in_app" in routing["budget_warning"]


# ---------------------------------------------------------------------------
# Channel implementation tests
# ---------------------------------------------------------------------------


class TestSMTPChannel:
    """Test SMTPChannel with mocked aiosmtplib."""

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """SMTPChannel sends email via aiosmtplib (mocked)."""
        ch = SMTPChannel(
            smtp_host="smtp.example.com",
            smtp_port=587,
            smtp_user="user",
            smtp_password="pass",
            from_address="from@example.com",
            to_addresses=["to@example.com"],
        )

        # Create a mock aiosmtplib module and inject it
        mock_aiosmtplib = MagicMock()
        mock_aiosmtplib.send = AsyncMock(return_value=({}, "OK"))

        with patch.dict(
            "sys.modules", {"aiosmtplib": mock_aiosmtplib}
        ), patch(
            "sagewai.notifications.channels.email._HAS_AIOSMTPLIB", True
        ), patch(
            "sagewai.notifications.channels.email.aiosmtplib",
            mock_aiosmtplib,
            create=True,
        ):
            result = await ch.send("Test", "Body", "info", {})

        assert result is True

    @pytest.mark.asyncio
    async def test_send_without_aiosmtplib(self) -> None:
        """SMTPChannel returns False if aiosmtplib is not installed."""
        ch = SMTPChannel(
            smtp_host="smtp.example.com",
            from_address="from@example.com",
            to_addresses=["to@example.com"],
        )

        with patch(
            "sagewai.notifications.channels.email._HAS_AIOSMTPLIB", False
        ):
            result = await ch.send("Test", "Body", "info", {})

        assert result is False

    @pytest.mark.asyncio
    async def test_send_no_recipients(self) -> None:
        """SMTPChannel returns False with no recipients."""
        ch = SMTPChannel(
            smtp_host="smtp.example.com",
            from_address="from@example.com",
            to_addresses=[],
        )
        result = await ch.send("Test", "Body", "info", {})
        assert result is False


class TestSlackWebhookChannel:
    """Test SlackWebhookChannel with mocked httpx."""

    @pytest.mark.asyncio
    async def test_send_success(self) -> None:
        """SlackWebhookChannel posts to webhook URL."""
        ch = SlackWebhookChannel(webhook_url="https://hooks.slack.com/test")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(return_value=mock_response)
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await ch.send("Alert", "Body", "warning", {"agent_name": "test"})

        assert result is True

    @pytest.mark.asyncio
    async def test_send_http_error(self) -> None:
        """SlackWebhookChannel returns False on HTTP error."""
        ch = SlackWebhookChannel(webhook_url="https://hooks.slack.com/test")

        with patch("httpx.AsyncClient") as MockClient:
            client_instance = AsyncMock()
            client_instance.post = AsyncMock(
                side_effect=httpx.HTTPError("fail")
            )
            MockClient.return_value.__aenter__ = AsyncMock(
                return_value=client_instance
            )
            MockClient.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await ch.send("Alert", "Body", "warning", {})

        assert result is False


class TestInAppChannel:
    """Test InAppChannel with mock callback."""

    @pytest.mark.asyncio
    async def test_send_with_callback(self) -> None:
        """InAppChannel invokes the callback with event data."""
        received: list[dict[str, Any]] = []
        ch = InAppChannel(callback=lambda e: received.append(e))

        result = await ch.send("Alert", "Body", "warning", {"trigger": "test"})

        assert result is True
        assert len(received) == 1
        assert received[0]["title"] == "Alert"
        assert received[0]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_send_without_callback(self) -> None:
        """InAppChannel succeeds silently with no callback."""
        ch = InAppChannel(callback=None)
        result = await ch.send("Alert", "Body", "info", {})
        assert result is True

    @pytest.mark.asyncio
    async def test_callback_error_still_returns_true(self) -> None:
        """InAppChannel returns True even if callback raises."""
        def bad_callback(e: dict) -> None:
            raise RuntimeError("oops")

        ch = InAppChannel(callback=bad_callback)
        result = await ch.send("Alert", "Body", "info", {})
        assert result is True


# ---------------------------------------------------------------------------
# Hook integration tests
# ---------------------------------------------------------------------------


class TestBudgetNotificationHook:
    """Test create_budget_notification_hook integration."""

    @pytest.mark.asyncio
    async def test_budget_warning_hook(self) -> None:
        """Hook dispatches budget_warning notification."""
        store = InMemoryNotificationStore()
        svc = NotificationService(store=store)
        ch = MockChannel()
        svc.register_channel("in_app", ch)

        hook = create_budget_notification_hook(svc, project_id="t1")
        await hook(FakeEvent("budget_warning"), {
            "agent_name": "TestAgent",
            "reason": "80% of daily limit",
        })

        assert len(ch.calls) == 1
        assert "TestAgent" in ch.calls[0]["title"]
        assert ch.calls[0]["severity"] == "warning"

    @pytest.mark.asyncio
    async def test_budget_exceeded_hook(self) -> None:
        """Hook dispatches budget_exceeded notification."""
        svc = NotificationService()
        ch = MockChannel()
        svc.register_channel("in_app", ch)

        hook = create_budget_notification_hook(svc)
        await hook(FakeEvent("budget_exceeded"), {
            "agent_name": "TestAgent",
            "reason": "limit exceeded",
        })

        assert len(ch.calls) == 1
        assert ch.calls[0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_irrelevant_event_ignored(self) -> None:
        """Hook ignores events that aren't budget-related."""
        svc = NotificationService()
        ch = MockChannel()
        svc.register_channel("in_app", ch)

        hook = create_budget_notification_hook(svc)
        await hook(FakeEvent("run_started"), {"agent_name": "A"})

        assert len(ch.calls) == 0


class TestWorkflowNotificationHook:
    """Test create_workflow_notification_hook integration."""

    @pytest.mark.asyncio
    async def test_run_error_hook(self) -> None:
        """Hook dispatches workflow_failed notification on run_error."""
        svc = NotificationService()
        ch = MockChannel()
        svc.register_channel("in_app", ch)

        hook = create_workflow_notification_hook(svc)
        await hook(FakeEvent("run_error"), {
            "agent_name": "Orchestrator",
            "error": "timeout",
        })

        assert len(ch.calls) == 1
        assert "Orchestrator" in ch.calls[0]["title"]
        assert ch.calls[0]["severity"] == "critical"

    @pytest.mark.asyncio
    async def test_approval_requested_hook(self) -> None:
        """Hook dispatches approval_requested notification."""
        svc = NotificationService()
        ch = MockChannel()
        svc.register_channel("in_app", ch)

        hook = create_workflow_notification_hook(svc)
        await hook(FakeEvent("approval_requested"), {
            "workflow_name": "deploy",
            "agent_name": "Approver",
        })

        assert len(ch.calls) == 1
        assert ch.calls[0]["severity"] == "info"


# ---------------------------------------------------------------------------
# InMemoryNotificationStore tests
# ---------------------------------------------------------------------------


class TestInMemoryStore:
    """Test the in-memory notification store."""

    def test_record_and_list(self) -> None:
        """Records are stored and listed newest-first."""
        store = InMemoryNotificationStore()

        for i in range(3):
            store.record(NotificationRecord(
                id=f"n{i}",
                trigger="test",
                title=f"Title {i}",
                body="body",
                channel_type="email",
            ))

        history = store.list_history(limit=10)
        assert len(history) == 3
        assert history[0]["title"] == "Title 2"  # newest first

    def test_max_capacity(self) -> None:
        """Store evicts oldest records beyond MAX_HISTORY."""
        store = InMemoryNotificationStore()
        store.MAX_HISTORY = 5

        for i in range(10):
            store.record(NotificationRecord(
                id=f"n{i}",
                trigger="test",
                title=f"Title {i}",
                body="body",
                channel_type="email",
            ))

        history = store.list_history(limit=100)
        assert len(history) == 5

    def test_filter_by_trigger(self) -> None:
        """History can be filtered by trigger type."""
        store = InMemoryNotificationStore()
        store.record(NotificationRecord(
            id="1", trigger="budget_warning", title="A", body="", channel_type="email",
        ))
        store.record(NotificationRecord(
            id="2", trigger="workflow_failed", title="B", body="", channel_type="email",
        ))

        result = store.list_history(trigger="budget_warning")
        assert len(result) == 1
        assert result[0]["trigger"] == "budget_warning"

    def test_channel_config_crud(self) -> None:
        """Channel configs can be saved, listed, and deleted."""
        store = InMemoryNotificationStore()

        config = NotificationChannelConfig(
            channel_type="email",
            smtp_host="smtp.test.com",
            from_address="test@test.com",
        )
        key = store.save_channel_config(config)

        configs = store.list_channel_configs()
        assert len(configs) == 1
        assert configs[0]["smtp_host"] == "smtp.test.com"

        assert store.delete_channel_config(key) is True
        assert store.delete_channel_config(key) is False  # already deleted
        assert len(store.list_channel_configs()) == 0


# We need httpx for the Slack test
import httpx
