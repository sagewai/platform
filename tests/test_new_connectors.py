"""Tests for enterprise connectors: GitHub, Jira, Notion, HubSpot (#513)."""

import pytest

from sagewai.connectors.base import AuthType
from sagewai.connectors.registry import ConnectorRegistry


def test_github_connector():
    from sagewai.connectors.builtins.github.connector import GitHubConnector

    c = GitHubConnector()
    assert c.name == "github"
    assert c.display_name == "GitHub"
    assert c.category == "productivity"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) == 1
    assert c.auth_fields[0].key == "token"
    assert c.auth_fields[0].env_var == "GITHUB_TOKEN"
    assert c.supports_webhook is True
    assert c.supports_poller is True
    assert c.docs_url == "https://docs.github.com/en/rest"
    assert c.agent_description != ""
    assert c.example_prompt != ""
    assert c.validate_credentials({}) != []
    assert c.validate_credentials({"token": "ghp_abc123"}) == []


def test_jira_connector():
    from sagewai.connectors.builtins.jira.connector import JiraConnector

    c = JiraConnector()
    assert c.name == "jira"
    assert c.display_name == "Jira"
    assert c.category == "productivity"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) == 3
    # email is not secret
    email_field = next(f for f in c.auth_fields if f.key == "email")
    assert email_field.env_var == "JIRA_EMAIL"
    assert email_field.secret is False
    # api_token is secret
    token_field = next(f for f in c.auth_fields if f.key == "api_token")
    assert token_field.env_var == "JIRA_API_TOKEN"
    assert token_field.secret is True
    # base_url is not secret
    url_field = next(f for f in c.auth_fields if f.key == "base_url")
    assert url_field.env_var == "JIRA_BASE_URL"
    assert url_field.secret is False
    assert c.supports_webhook is True
    assert c.supports_poller is True
    assert c.agent_description != ""
    assert c.example_prompt != ""
    assert c.validate_credentials({}) != []
    assert (
        c.validate_credentials(
            {
                "email": "user@example.com",
                "api_token": "tok",
                "base_url": "https://x.atlassian.net",
            }
        )
        == []
    )


def test_notion_connector():
    from sagewai.connectors.builtins.notion.connector import NotionConnector

    c = NotionConnector()
    assert c.name == "notion"
    assert c.display_name == "Notion"
    assert c.category == "productivity"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) == 1
    assert c.auth_fields[0].key == "api_key"
    assert c.auth_fields[0].env_var == "NOTION_API_KEY"
    assert c.supports_webhook is True
    assert c.supports_poller is True
    assert c.agent_description != ""
    assert c.example_prompt != ""
    assert c.validate_credentials({}) != []
    assert c.validate_credentials({"api_key": "secret_abc"}) == []


def test_hubspot_connector():
    from sagewai.connectors.builtins.hubspot.connector import HubSpotConnector

    c = HubSpotConnector()
    assert c.name == "hubspot"
    assert c.display_name == "HubSpot"
    assert c.category == "crm"
    assert c.auth_type == AuthType.API_KEY
    assert len(c.auth_fields) == 1
    assert c.auth_fields[0].key == "access_token"
    assert c.auth_fields[0].env_var == "HUBSPOT_ACCESS_TOKEN"
    assert c.supports_webhook is True
    assert c.supports_poller is True
    assert c.agent_description != ""
    assert c.example_prompt != ""
    assert c.validate_credentials({}) != []
    assert c.validate_credentials({"access_token": "pat-na1-abc"}) == []


def test_all_four_discoverable_via_registry():
    """All 4 new connectors are auto-discovered by the registry."""
    reg = ConnectorRegistry()
    reg.discover_builtins()
    names = {c.name for c in reg.list()}
    for expected in ["github", "jira", "notion", "hubspot"]:
        assert expected in names, f"Connector '{expected}' not discovered by registry"


def test_total_builtin_count():
    """Registry discovers all 18 builtin connectors (14 existing + 4 new)."""
    reg = ConnectorRegistry()
    reg.discover_builtins()
    total = len(reg.list())
    assert total == 18, f"Expected 18 builtins, got {total}: {sorted(c.name for c in reg.list())}"
