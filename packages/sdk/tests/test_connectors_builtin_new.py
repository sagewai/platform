"""Tests for new builtin connectors: Composio, Toolhouse, WhatsApp, X, Instagram, Shopify."""

import pytest

from sagewai.connectors.base import AuthType


def test_composio_connector():
    from sagewai.connectors.builtins.composio import ComposioConnector

    c = ComposioConnector()
    assert c.name == "composio"
    assert c.category == "aggregator"
    assert c.auth_type == AuthType.API_KEY
    assert c.auth_fields[0].env_var == "COMPOSIO_API_KEY"


def test_toolhouse_connector():
    from sagewai.connectors.builtins.toolhouse import ToolhouseConnector

    c = ToolhouseConnector()
    assert c.name == "toolhouse"
    assert c.category == "aggregator"
    assert c.auth_fields[0].env_var == "TOOLHOUSE_API_KEY"


def test_whatsapp_connector():
    from sagewai.connectors.builtins.whatsapp.connector import WhatsAppConnector

    c = WhatsAppConnector()
    assert c.name == "whatsapp"
    assert c.category == "communication"
    assert len(c.auth_fields) == 2
    assert c.supports_webhook is True


def test_x_connector():
    from sagewai.connectors.builtins.x.connector import XConnector

    c = XConnector()
    assert c.name == "x"
    assert c.auth_type == AuthType.OAUTH2
    assert c.oauth_authorize_url is not None


def test_instagram_connector():
    from sagewai.connectors.builtins.instagram.connector import InstagramConnector

    c = InstagramConnector()
    assert c.name == "instagram"
    assert c.auth_type == AuthType.OAUTH2


def test_shopify_connector():
    from sagewai.connectors.builtins.shopify.connector import ShopifyConnector

    c = ShopifyConnector()
    assert c.name == "shopify"
    assert c.category == "commerce"
    assert len(c.auth_fields) == 2


def test_registry_discovers_new_connectors():
    from sagewai.connectors.registry import ConnectorRegistry

    reg = ConnectorRegistry()
    reg.discover_builtins()
    names = {c.name for c in reg.list()}
    for expected in ["composio", "toolhouse", "whatsapp", "x", "instagram", "shopify"]:
        assert expected in names, f"Missing: {expected}"
