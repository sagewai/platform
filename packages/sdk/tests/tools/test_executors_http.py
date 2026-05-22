# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import httpx
import pytest
import respx
from sagewai.tools import registry
from sagewai.tools.executors import http as http_exec


def _creds(value):
    def _get(*, project_id, kind, id):
        return {"GITHUB_TOKEN": value}
    return _get


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_sends_request_with_auth_header():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")

    route = respx.get("https://api.github.com/repos/octocat/hello-world").respond(
        200, json={"id": 1, "name": "hello-world", "full_name": "octocat/hello-world"}
    )
    out = await http_exec.run(
        entry,
        operation="get_repo",
        inputs={"owner": "octocat", "repo": "hello-world"},
        project_id="p1",
        get_credentials=_creds("ghp_abc"),
    )
    assert out["name"] == "hello-world"
    assert route.calls.last.request.headers["Authorization"] == "Bearer ghp_abc"


@pytest.mark.asyncio
async def test_http_executor_rejects_bad_input_schema():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    with pytest.raises(http_exec.InputValidationError):
        await http_exec.run(
            entry,
            operation="get_repo",
            inputs={"owner": "octocat"},  # missing repo
            project_id="p1",
            get_credentials=_creds("ghp_abc"),
        )


@pytest.mark.asyncio
async def test_http_executor_raises_on_unknown_operation():
    registry._reset()
    registry.load()
    entry = registry.lookup("github")
    with pytest.raises(http_exec.UnknownOperationError):
        await http_exec.run(
            entry,
            operation="nope",
            inputs={},
            project_id="p1",
            get_credentials=_creds("ghp_abc"),
        )


def test_basic_auth_empty_password_encodes_correctly():
    """Greenhouse-style Basic auth: API key as username, empty password.

    Confirms _build_auth_headers produces ``Authorization: Basic <b64(key:)>``
    when password is empty (matches HTTP Basic-auth spec).
    """
    import base64
    from sagewai.tools.executors.http import _build_auth_headers

    auth_cfg = {"kind": "basic"}
    creds = {"USERNAME": "test-api-key", "PASSWORD": ""}
    headers = _build_auth_headers(auth_cfg, creds)
    expected_b64 = base64.b64encode(b"test-api-key:").decode()
    assert headers["Authorization"] == f"Basic {expected_b64}"


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_form_encoded_body():
    """body_format=form posts application/x-www-form-urlencoded body."""
    from sagewai.tools.registry import CatalogEntry

    entry = CatalogEntry(
        id="form_demo",
        version="0.1.0",
        title="Form demo",
        description="x",
        category="test",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://api.example.test",
                "auth": {"kind": "bearer"},
                "operations": {
                    "do_thing": {
                        "method": "POST",
                        "path": "/things",
                        "body_format": "form",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                },
            }
        },
        scopes=frozenset(),
        setup={"auth_complexity": "api_key", "body": "x"},
    )

    route = respx.post("https://api.example.test/things").respond(200, json={"ok": True})

    def _get_creds(*, project_id, kind, id):
        return {"TOKEN": "bearer-tok"}

    await http_exec.run(
        entry,
        operation="do_thing",
        inputs={"amount": 100, "currency": "usd"},
        project_id="p1",
        get_credentials=_get_creds,
    )

    sent_request = route.calls.last.request
    assert sent_request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded")
    body = sent_request.content.decode()
    assert "amount=100" in body
    assert "currency=usd" in body


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_default_body_format_is_json():
    """Existing batch-1/2a/2b ops without body_format still send JSON."""
    from sagewai.tools.registry import CatalogEntry

    entry = CatalogEntry(
        id="json_demo",
        version="0.1.0",
        title="JSON demo",
        description="x",
        category="test",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://api.example.test",
                "auth": {"kind": "bearer"},
                "operations": {
                    "do_thing": {
                        "method": "POST",
                        "path": "/things",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                },
            }
        },
        scopes=frozenset(),
        setup={"auth_complexity": "api_key", "body": "x"},
    )

    route = respx.post("https://api.example.test/things").respond(200, json={"ok": True})

    def _get_creds(*, project_id, kind, id):
        return {"TOKEN": "bearer-tok"}

    await http_exec.run(
        entry,
        operation="do_thing",
        inputs={"key": "value"},
        project_id="p1",
        get_credentials=_get_creds,
    )

    sent_request = route.calls.last.request
    assert sent_request.headers.get("content-type", "").startswith("application/json")
    body = sent_request.content.decode()
    assert '"key"' in body and '"value"' in body


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_runtime_base_url_override():
    """When runtime_base_url_field is set + credential present, override exec.http.base_url."""
    from sagewai.tools.registry import CatalogEntry

    entry = CatalogEntry(
        id="runtime_demo",
        version="0.1.0",
        title="Runtime URL demo",
        description="x",
        category="test",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://default.example.test",
                "runtime_base_url_field": "MY_SITE",
                "auth": {"kind": "bearer"},
                "operations": {
                    "ping": {
                        "method": "GET",
                        "path": "/ping",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                },
            }
        },
        scopes=frozenset(),
        setup={"auth_complexity": "api_key", "body": "x"},
    )

    route = respx.get("https://acme.atlassian.net/ping").respond(200, json={"ok": True})

    def _creds(*, project_id, kind, id):
        return {"TOKEN": "tok", "MY_SITE": "https://acme.atlassian.net"}

    await http_exec.run(
        entry, operation="ping", inputs={},
        project_id="p1", get_credentials=_creds,
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_runtime_base_url_strips_trailing_slash():
    from sagewai.tools.registry import CatalogEntry

    entry = CatalogEntry(
        id="rt2",
        version="0.1.0",
        title="x",
        description="x",
        category="test",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://default.example.test",
                "runtime_base_url_field": "MY_SITE",
                "auth": {"kind": "bearer"},
                "operations": {
                    "ping": {
                        "method": "GET",
                        "path": "/ping",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                },
            }
        },
        scopes=frozenset(),
        setup={"auth_complexity": "api_key", "body": "x"},
    )

    route = respx.get("https://acme.atlassian.net/ping").respond(200, json={})

    def _creds(*, project_id, kind, id):
        return {"TOKEN": "tok", "MY_SITE": "https://acme.atlassian.net/"}

    await http_exec.run(
        entry, operation="ping", inputs={},
        project_id="p1", get_credentials=_creds,
    )
    assert route.called


@pytest.mark.asyncio
@respx.mock
async def test_http_executor_runtime_base_url_falls_back_when_credential_empty():
    """If credential is missing or empty, use static exec.http.base_url."""
    from sagewai.tools.registry import CatalogEntry

    entry = CatalogEntry(
        id="rt3",
        version="0.1.0",
        title="x",
        description="x",
        category="test",
        kind="http",
        sandbox_tier="SANDBOXED",
        exec_={
            "http": {
                "base_url": "https://default.example.test",
                "runtime_base_url_field": "MY_SITE",
                "auth": {"kind": "bearer"},
                "operations": {
                    "ping": {
                        "method": "GET",
                        "path": "/ping",
                        "input_schema": {"type": "object"},
                        "output_schema": {"type": "object"},
                    }
                },
            }
        },
        scopes=frozenset(),
        setup={"auth_complexity": "api_key", "body": "x"},
    )

    route = respx.get("https://default.example.test/ping").respond(200, json={})

    def _creds(*, project_id, kind, id):
        return {"TOKEN": "tok"}  # MY_SITE missing

    await http_exec.run(
        entry, operation="ping", inputs={},
        project_id="p1", get_credentials=_creds,
    )
    assert route.called
