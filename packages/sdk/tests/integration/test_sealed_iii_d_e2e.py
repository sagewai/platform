# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sealed-iii.D end-to-end: two CLIs, two keys, ACL splits access."""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="requires SAGEWAI_DATABASE_URL",
)


@pytest.mark.asyncio
async def test_two_clis_two_keys_acl_splits_access() -> None:
    from sagewai.sandbox.acl_handle import AclFilteringSandboxHandle
    from sagewai.sandbox.models import SandboxMode, ToolCall, ToolResult

    captured_envs: list[dict[str, str]] = []

    class _H:
        sandbox_id = "sgw-it"
        mode = SandboxMode.PER_RUN
        image = "img"
        image_digest = "sha256:x"

        def __init__(self):
            self._env: dict[str, str] = {}

        async def exec(self, c: ToolCall) -> ToolResult:
            captured_envs.append(dict(self._env))
            return ToolResult(call_id=c.call_id, ok=True)

        async def set_env(self, env):
            self._env = dict(env)

        async def copy_in(self, s, d): ...
        async def copy_out(self, s, d): ...

        async def stats(self):
            from sagewai.sandbox.models import SandboxStats
            return SandboxStats()

        async def stop(self, *, timeout=10.0): ...

    class _A:
        async def emit(self, **k): ...

    wrapper = AclFilteringSandboxHandle(
        _H(),
        secret_keys={"ANTHROPIC_API_KEY", "OPENAI_API_KEY"},
        acl={
            "claude-code": ["ANTHROPIC_API_KEY"],
            "codex":       ["OPENAI_API_KEY"],
        },
        audit_writer=_A(),
        run_id="r-itd-1",
        profile_id="p-it",
    )
    await wrapper.set_env({
        "ANTHROPIC_API_KEY": "sk-ant-xxxxxxxxxxxxxxxxxxxx",
        "OPENAI_API_KEY":    "sk-openai-yyyyyyyyyyyyyyyyy",
        "DEBUG":             "1",
    })

    await wrapper.exec(ToolCall(tool="claude-code", args={}, call_id="c1"))
    await wrapper.exec(ToolCall(tool="codex",       args={}, call_id="c2"))

    # During claude-code: only ANTHROPIC + DEBUG (OPENAI removed)
    assert "ANTHROPIC_API_KEY" in captured_envs[0]
    assert "OPENAI_API_KEY" not in captured_envs[0]
    assert "DEBUG" in captured_envs[0]

    # During codex: only OPENAI + DEBUG (ANTHROPIC removed)
    assert "OPENAI_API_KEY" in captured_envs[1]
    assert "ANTHROPIC_API_KEY" not in captured_envs[1]
    assert "DEBUG" in captured_envs[1]
