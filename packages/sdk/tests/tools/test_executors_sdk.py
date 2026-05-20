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
from sagewai.tools import registry
from sagewai.tools.executors import sdk as sdk_exec


def _noop_creds(*, project_id, kind, id):
    return {}


@pytest.mark.asyncio
async def test_sdk_executor_calls_entrypoint(monkeypatch):
    called = {}

    async def fake_fetch(payload):
        called["payload"] = payload
        return {"ok": True}

    import sagewai.tools.builtins.http_parsing as dt
    monkeypatch.setattr(dt, "fetch_url", fake_fetch, raising=False)

    registry._reset()
    registry.load()
    entry = registry.lookup("fetch_url")
    out = await sdk_exec.run(entry, operation=None, inputs={"url": "https://x"}, project_id="p1", get_credentials=_noop_creds)
    assert out == {"ok": True}
    assert called["payload"] == {"url": "https://x"}


@pytest.mark.asyncio
async def test_sdk_executor_raises_on_missing_entrypoint(monkeypatch):
    registry._reset()
    registry.load()
    entry = registry.lookup("fetch_url")
    # Cannot mutate a frozen dataclass; build a synthetic entry with broken entrypoint
    import sagewai.tools.registry as reg
    broken = reg.CatalogEntry(
        id="broken",
        version="0.1.0",
        title="Broken",
        description="x",
        category="test",
        kind="sdk",
        sandbox_tier="SANDBOXED",
        exec_={"sdk": {"entrypoint": "pkg.nope:nope"}},
        scopes=frozenset(),
        setup={"auth_complexity": "none", "body": "x"},
    )
    with pytest.raises(sdk_exec.EntrypointResolutionError):
        await sdk_exec.run(broken, operation=None, inputs={}, project_id="p1", get_credentials=_noop_creds)
