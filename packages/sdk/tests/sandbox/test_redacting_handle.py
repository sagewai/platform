# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Unit tests for RedactingSandboxHandle wrapper."""
from __future__ import annotations

from typing import Any

import pytest

from sagewai.sandbox.backend import SandboxHandle
from sagewai.sandbox.models import SandboxMode, ToolCall, ToolResult
from sagewai.sandbox.redacting_handle import RedactingSandboxHandle
from sagewai.sealed.redaction import Redactor


class FakeAuditWriter:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, **kwargs: Any) -> None:
        self.events.append(kwargs)


class FakeHandle:
    sandbox_id = "sgw-fake"
    mode = SandboxMode.PER_RUN
    image = "ghcr.io/test/image:dev"
    image_digest = "sha256:abc"

    def __init__(self, *, exec_result: ToolResult, raise_exc: Exception | None = None) -> None:
        self._exec_result = exec_result
        self._raise = raise_exc
        self.set_env_calls: list[dict[str, str]] = []
        self.exec_calls: list[ToolCall] = []
        self.copy_in_calls: list[Any] = []
        self.stop_calls: int = 0

    async def exec(self, tool_call: ToolCall) -> ToolResult:
        if self._raise is not None:
            raise self._raise
        self.exec_calls.append(tool_call)
        return self._exec_result

    async def set_env(self, env: dict[str, str]) -> None:
        self.set_env_calls.append(dict(env))

    async def copy_in(self, src, dst) -> None:
        self.copy_in_calls.append((src, dst))

    async def copy_out(self, src, dst) -> None:
        ...

    async def stats(self):
        from sagewai.sandbox.models import SandboxStats
        return SandboxStats()

    async def stop(self, *, timeout: float = 10.0) -> None:
        self.stop_calls += 1


@pytest.mark.asyncio
async def test_redacting_handle_conforms_to_protocol() -> None:
    inner = FakeHandle(exec_result=ToolResult(call_id="c", ok=True))
    redactor = Redactor({"K": "sk-aaaaaaaaaaaaaaaa"})
    audit = FakeAuditWriter()
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=audit, run_id="r-1", profile_id="p-1",
    )
    # Protocol-conformance smoke
    assert isinstance(wrapper, SandboxHandle)
    assert wrapper.sandbox_id == "sgw-fake"
    assert wrapper.mode == SandboxMode.PER_RUN


@pytest.mark.asyncio
async def test_redacting_handle_redacts_stdout() -> None:
    inner = FakeHandle(
        exec_result=ToolResult(
            call_id="c", ok=True,
            stdout="leaked sk-aaaaaaaaaaaaaaaa here", stderr="",
        )
    )
    redactor = Redactor({"K": "sk-aaaaaaaaaaaaaaaa"})
    audit = FakeAuditWriter()
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=audit, run_id="r-1", profile_id="p-1",
    )

    out = await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))
    assert out.stdout == "leaked <redacted:K> here"
    assert out.stderr == ""
    # One audit event per (key, surface) tuple
    match_events = [e for e in audit.events if e.get("event_type") == "redaction.match"]
    assert len(match_events) == 1
    assert match_events[0]["secret_key"] == "K"
    assert match_events[0]["details"]["surface"] == "stdout"


@pytest.mark.asyncio
async def test_redacting_handle_redacts_stderr_and_error() -> None:
    inner = FakeHandle(
        exec_result=ToolResult(
            call_id="c", ok=False,
            stdout="",
            stderr="boom sk-aaaaaaaaaaaaaaaa boom",
            error="exception: sk-aaaaaaaaaaaaaaaa",
        )
    )
    redactor = Redactor({"K": "sk-aaaaaaaaaaaaaaaa"})
    audit = FakeAuditWriter()
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=audit, run_id="r-1", profile_id="p-1",
    )

    out = await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))
    assert "<redacted:K>" in out.stderr
    assert "<redacted:K>" in (out.error or "")
    surfaces = sorted(
        e["details"]["surface"] for e in audit.events
        if e.get("event_type") == "redaction.match"
    )
    assert surfaces == ["error", "stderr"]


@pytest.mark.asyncio
async def test_redacting_handle_no_match_no_audit() -> None:
    inner = FakeHandle(exec_result=ToolResult(call_id="c", ok=True, stdout="clean output"))
    redactor = Redactor({"K": "sk-not-present-aaaaaaaaaaa"})
    audit = FakeAuditWriter()
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=audit, run_id="r-1", profile_id="p-1",
    )

    await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))
    match_events = [e for e in audit.events if e.get("event_type") == "redaction.match"]
    assert match_events == []


@pytest.mark.asyncio
async def test_redacting_handle_propagates_inner_exception() -> None:
    inner = FakeHandle(
        exec_result=ToolResult(call_id="c", ok=True),
        raise_exc=RuntimeError("inner exec failed"),
    )
    redactor = Redactor({"K": "sk-aaaaaaaaaaaaaaaa"})
    audit = FakeAuditWriter()
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=audit, run_id="r-1", profile_id="p-1",
    )

    with pytest.raises(RuntimeError, match="inner exec failed"):
        await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))


@pytest.mark.asyncio
async def test_redacting_handle_pure_delegate_set_env_and_stop() -> None:
    inner = FakeHandle(exec_result=ToolResult(call_id="c", ok=True))
    redactor = Redactor({})
    wrapper = RedactingSandboxHandle(
        inner, redactor=redactor, audit_writer=FakeAuditWriter(),
        run_id="r-1", profile_id="p-1",
    )

    await wrapper.set_env({"X": "y"})
    assert inner.set_env_calls == [{"X": "y"}]
    await wrapper.stop()
    assert inner.stop_calls == 1
