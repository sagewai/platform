# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
import respx
from sagewai.tools.executors import webhook as wh_exec
from sagewai.tools.registry import CatalogEntry


def _noop_creds(*, project_id, kind, id):
    return {}


def _entry(url: str) -> CatalogEntry:
    return CatalogEntry(
        id="wh_demo",
        version="0.1.0",
        title="Webhook demo",
        description="x",
        category="test",
        kind="webhook",
        sandbox_tier="SANDBOXED",
        exec_={"webhook": {"url": url}},
        scopes=frozenset(),
        setup={"auth_complexity": "none", "body": "x"},
    )


@pytest.mark.asyncio
@respx.mock
async def test_webhook_posts_payload():
    route = respx.post("https://example.test/hook").respond(202, json={"queued": True})
    out = await wh_exec.run(
        _entry("https://example.test/hook"),
        operation=None, inputs={"event": "x"},
        project_id="p1", get_credentials=_noop_creds,
    )
    assert out == {"status": 202, "body": {"queued": True}}
    import json
    assert json.loads(route.calls.last.request.content) == {"event": "x"}
