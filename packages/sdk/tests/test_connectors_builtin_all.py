# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for all builtin connectors and registry discovery."""

import pytest

from sagewai.connectors.base import AuthType


def test_slack_connector():
    from sagewai.connectors.builtins.slack.connector import SlackConnector

    c = SlackConnector()
    assert c.name == "slack"
    assert c.category == "communication"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) >= 1
    assert c.supports_webhook is True
    assert c.supports_listener is True
    assert c.supports_poller is True
    assert c.validate_credentials({}) != []


def test_email_connector():
    from sagewai.connectors.builtins.email.connector import EmailConnector

    c = EmailConnector()
    assert c.name == "email"
    assert c.category == "communication"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) >= 1
    assert c.supports_webhook is True
    assert c.supports_poller is True


def test_calendar_connector():
    from sagewai.connectors.builtins.calendar.connector import CalendarConnector

    c = CalendarConnector()
    assert c.name == "calendar"
    assert c.category == "productivity"
    assert c.auth_type == AuthType.API_KEY
    assert c.supports_poller is True


def test_documents_connector():
    from sagewai.connectors.builtins.documents.connector import DocumentsConnector

    c = DocumentsConnector()
    assert c.name == "documents"
    assert c.category == "productivity"
    assert c.auth_type == AuthType.API_KEY


def test_payments_connector():
    from sagewai.connectors.builtins.payments.connector import PaymentsConnector

    c = PaymentsConnector()
    assert c.name == "payments"
    assert c.category == "finance"
    assert c.supports_webhook is True


def test_commerce_connector():
    from sagewai.connectors.builtins.commerce.connector import CommerceConnector

    c = CommerceConnector()
    assert c.name == "commerce"
    assert c.category == "commerce"
    assert len(c.auth_fields) == 2


def test_travel_connector():
    from sagewai.connectors.builtins.travel.connector import TravelConnector

    c = TravelConnector()
    assert c.name == "travel"
    assert c.category == "travel"
    assert len(c.auth_fields) == 2


def test_knowledge_graph_connector():
    from sagewai.connectors.builtins.knowledge_graph.connector import (
        KnowledgeGraphConnector,
    )

    c = KnowledgeGraphConnector()
    assert c.name == "knowledge_graph"
    assert c.category == "data"
    assert len(c.auth_fields) == 4


def test_registry_discovers_all_builtins():
    from sagewai.connectors.registry import ConnectorRegistry

    reg = ConnectorRegistry()
    reg.discover_builtins()
    names = {c.name for c in reg.list()}
    expected = {
        "slack",
        "email",
        "calendar",
        "documents",
        "payments",
        "commerce",
        "travel",
        "knowledge_graph",
    }
    assert expected.issubset(names)
