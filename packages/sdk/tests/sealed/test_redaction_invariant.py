"""The central iii.B promise: known-secret values never appear in the
redacted ToolResult emitted from a wrapped handle.

This test exists to guard regressions if anyone touches the wrapper or
the Redactor in a way that breaks the invariant. If this test fails,
do NOT skip it; fix the regression."""
from __future__ import annotations

import pytest

from sagewai.sandbox.models import SandboxMode, ToolCall, ToolResult
from sagewai.sandbox.redacting_handle import RedactingSandboxHandle
from sagewai.sealed.redaction import Redactor


class _Handle:
    sandbox_id = "sgw-x"
    mode = SandboxMode.PER_RUN
    image = "img"
    image_digest = "sha256:x"

    def __init__(self, result: ToolResult) -> None:
        self._result = result

    async def exec(self, _: ToolCall) -> ToolResult:
        return self._result

    async def set_env(self, env): ...
    async def copy_in(self, src, dst): ...
    async def copy_out(self, src, dst): ...
    async def stats(self):
        from sagewai.sandbox.models import SandboxStats
        return SandboxStats()
    async def stop(self, *, timeout: float = 10.0): ...


class _Audit:
    async def emit(self, **kwargs): ...


@pytest.mark.asyncio
async def test_central_invariant_no_value_substring_in_redacted_result() -> None:
    secrets = {
        "ANTHROPIC_API_KEY": "sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "OPENAI_API_KEY":    "sk-openai-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        "AWS_ACCESS_KEY_ID": "AKIAaaaaaaaaaaaaaaaaa",
        "AWS_SECRET_ACCESS_KEY": "secrettokenccccccccccccccccccccccccccc",
        "GITHUB_TOKEN":      "ghp_dddddddddddddddddddddddddddddddddd",
    }
    leaky = ToolResult(
        call_id="c", ok=False,
        stdout=(
            "leak1: sk-ant-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, "
            "leak2: sk-openai-bbbbbbbbbbbbbbbbbbbbbbbbbbbbbb, "
            "leak3: AKIAaaaaaaaaaaaaaaaaa"
        ),
        stderr=(
            "leak4: secrettokenccccccccccccccccccccccccccc, "
            "leak5: ghp_dddddddddddddddddddddddddddddddddd"
        ),
        error="all together: " + " ".join(secrets.values()),
    )
    wrapper = RedactingSandboxHandle(
        _Handle(leaky),
        redactor=Redactor(secrets),
        audit_writer=_Audit(),
        run_id="r-1",
        profile_id="p-1",
    )

    out = await wrapper.exec(ToolCall(tool="shell", args={}, call_id="c"))

    # Central invariant: no value substring anywhere in any string field.
    haystack = "\n".join([out.stdout or "", out.stderr or "", out.error or ""])
    for name, value in secrets.items():
        assert value not in haystack, (
            f"INVARIANT BROKEN: secret value for {name} appears unredacted "
            f"in ToolResult after redaction wrapper. This is a security regression."
        )
