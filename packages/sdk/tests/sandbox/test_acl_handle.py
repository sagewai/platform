# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""AclFilteringSandboxHandle: per-tool env filtering at exec time."""
from __future__ import annotations

from typing import Any

import pytest

from sagewai.sandbox.acl_handle import AclFilteringSandboxHandle
from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import SandboxMode, SandboxStats, ToolCall, ToolResult


class FakeAuditWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def emit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeHandle:
    sandbox_id = "sgw-fake"
    mode = SandboxMode.PER_RUN
    image = "img"
    image_digest = "sha256:x"

    def __init__(self) -> None:
        self.set_env_calls: list[dict[str, str]] = []
        self.exec_envs_at_call: list[dict[str, str]] = []
        self._exec_raise: Exception | None = None

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        # Capture the env that was last set before this exec
        self.exec_envs_at_call.append(
            dict(self.set_env_calls[-1]) if self.set_env_calls else {}
        )
        if self._exec_raise:
            raise self._exec_raise
        return ToolResult(call_id=tool_call.call_id, ok=True)

    async def set_env(self, env: dict[str, str]) -> None:
        self.set_env_calls.append(dict(env))

    async def copy_in(self, src, dst):
        pass

    async def copy_out(self, src, dst):
        pass

    async def stats(self):
        return SandboxStats()

    async def stop(self, *, timeout=10.0):
        pass


@pytest.mark.asyncio
async def test_acl_filtering_handle_protocol_conformance() -> None:
    inner = FakeHandle()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys=set(),
        acl={},
        audit_writer=FakeAuditWriter(),
        run_id="r",
        profile_id="p",
    )
    assert isinstance(wrapper, SandboxHandle)


@pytest.mark.asyncio
async def test_acl_filtering_handle_filters_per_tool() -> None:
    inner = FakeHandle()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys={"K1", "K2"},
        acl={"claude-code": ["K1"], "codex": ["K2"]},
        audit_writer=FakeAuditWriter(),
        run_id="r-1",
        profile_id="p-1",
    )

    await wrapper.set_env({"K1": "v1", "K2": "v2", "DEBUG": "1"})
    # Initial set_env passes the FULL env to inner
    assert inner.set_env_calls[-1] == {"K1": "v1", "K2": "v2", "DEBUG": "1"}

    await wrapper.exec(ToolCall(tool="claude-code", args={}, call_id="c1"))
    # During exec: inner saw the FILTERED env (K2 removed)
    assert inner.exec_envs_at_call[-1] == {"K1": "v1", "DEBUG": "1"}
    # After exec: inner.set_env was called again to RESTORE full env
    assert inner.set_env_calls[-1] == {"K1": "v1", "K2": "v2", "DEBUG": "1"}


@pytest.mark.asyncio
async def test_acl_enforced_audit_emitted_when_filter_removes_keys() -> None:
    inner = FakeHandle()
    audit = FakeAuditWriter()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys={"K1", "K2"},
        acl={"claude-code": ["K1"]},
        audit_writer=audit,
        run_id="r-1",
        profile_id="p-1",
    )
    await wrapper.set_env({"K1": "v1", "K2": "v2"})
    await wrapper.exec(ToolCall(tool="claude-code", args={}, call_id="c1"))

    enforced = [e for e in audit.events if e.get("event_type") == "acl.enforced"]
    assert len(enforced) == 1
    assert enforced[0]["details"]["tool"] == "claude-code"
    assert enforced[0]["details"]["allowed_keys"] == ["K1"]
    assert enforced[0]["details"]["removed_keys"] == ["K2"]


@pytest.mark.asyncio
async def test_acl_passthrough_when_tool_unrestricted() -> None:
    inner = FakeHandle()
    audit = FakeAuditWriter()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys={"K1"},
        acl={"claude-code": ["K1"]},
        audit_writer=audit,
        run_id="r-1",
        profile_id="p-1",
    )
    await wrapper.set_env({"K1": "v1"})
    await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c1"))

    # No filter happened (tool not in ACL)
    enforced = [e for e in audit.events if e.get("event_type") == "acl.enforced"]
    assert enforced == []


@pytest.mark.asyncio
async def test_acl_restore_runs_even_on_inner_exec_exception() -> None:
    inner = FakeHandle()
    inner._exec_raise = RuntimeError("boom")
    audit = FakeAuditWriter()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys={"K1"},
        acl={"claude-code": []},
        audit_writer=audit,
        run_id="r-1",
        profile_id="p-1",
    )
    await wrapper.set_env({"K1": "v1", "DEBUG": "1"})
    with pytest.raises(RuntimeError, match="boom"):
        await wrapper.exec(ToolCall(tool="claude-code", args={}, call_id="c1"))

    # Restore must have run despite the exception
    assert len(inner.set_env_calls) >= 3
    assert inner.set_env_calls[-1] == {"K1": "v1", "DEBUG": "1"}


@pytest.mark.asyncio
async def test_acl_empty_list_denies_all_secrets() -> None:
    inner = FakeHandle()
    wrapper = AclFilteringSandboxHandle(
        inner,
        secret_keys={"K1", "K2"},
        acl={"shell": []},
        audit_writer=FakeAuditWriter(),
        run_id="r-1",
        profile_id="p-1",
    )
    await wrapper.set_env({"K1": "v1", "K2": "v2", "DEBUG": "1"})
    await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c1"))

    # Filter dropped all secrets, kept the knob
    assert inner.exec_envs_at_call[-1] == {"DEBUG": "1"}
