# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""HarnessRuntime bounded structured-result loop tests."""

from __future__ import annotations

import asyncio
import inspect
import json
import shlex
from types import SimpleNamespace

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.core.events import AgentEvent
from sagewai.harness.models import ComplexityTier
from sagewai.models import ChatMessage, ToolSpec
from sagewai.safety.permissions import PermissionLevel
from sagewai.work import CapabilityGrant, CapabilitySet, ListActivitySink, WorkRequest
from sagewai.work.runtime_harness import HarnessBudget, HarnessRuntime
from sagewai.work.tasks.models import HarnessTier

from .test_runtime import _capsule, _request, _workspace


class FakeAgent:
    """Stands in for UniversalAgent: scripted replies, hook callbacks, tool-call simulation."""

    def __init__(
        self,
        replies: list[str],
        *,
        tokens: tuple[int, int] = (10, 5),
        cost: float = 0.0,
        tool_calls: int = 0,
        delay: float = 0.0,
        raises: Exception | None = None,
        **kwargs,
    ) -> None:
        self.kwargs = kwargs
        self.prompts: list[str] = []
        self.histories: list[list[ChatMessage]] = []
        self._replies = list(replies)
        self._callbacks = []
        self._tokens = tokens
        self._cost = cost
        self._tool_calls = tool_calls
        self._delay = delay
        self._raises = raises

    def on_event(self, callback) -> None:
        self._callbacks.append(callback)

    async def _fire(self, event, payload) -> None:
        for callback in self._callbacks:
            await callback(event, payload) if inspect.iscoroutinefunction(callback) else callback(event, payload)

    async def chat(self, message: str) -> str:
        raise AssertionError("HarnessRuntime must use chat_with_history")

    async def chat_with_history(self, messages: list[ChatMessage]) -> ChatMessage:
        self.histories.append(list(messages))
        self.prompts.append(messages[-1].content or "")
        await asyncio.sleep(self._delay)
        for index in range(self._tool_calls):
            decision = await self.kwargs["permission_policy"].check_and_approve("fs_read", {"path": f"f{index}"})
            assert decision.allowed is True and decision.level == PermissionLevel.AUTO_APPROVE
            await self._fire(
                AgentEvent.TOOL_CALL_START,
                {"tool_call_id": f"tc{index}", "tool_name": "fs_read", "arguments": {"path": f"f{index}"}},
            )
            await self._fire(
                AgentEvent.TOOL_CALL_RESULT,
                {"tool_call_id": f"tc{index}", "tool_name": "fs_read", "content": "...", "error": None},
            )
        await self._fire(
            AgentEvent.LLM_CALL_FINISHED,
            {
                "model": self.kwargs["model"],
                "input_tokens": self._tokens[0],
                "output_tokens": self._tokens[1],
                "cost_usd": self._cost,
                "duration_ms": 3,
            },
        )
        if self._raises is not None:
            raise self._raises
        reply = self._replies.pop(0)
        await self._fire(AgentEvent.TEXT_MESSAGE_CONTENT, {"message_id": "msg_0", "delta": reply})
        return ChatMessage.assistant(content=reply)


def _factory(replies, **fake_kwargs):
    made = []

    def make(**kwargs):
        agent = FakeAgent(replies, **fake_kwargs, **kwargs)
        made.append(agent)
        return agent

    make.made = made
    return make


class FakeTools:
    specs = (ToolSpec(name="fs_read", description="read"),)

    def __init__(self) -> None:
        self.close_calls = 0

    async def close(self) -> None:
        self.close_calls += 1


def _fake_tools(monkeypatch: pytest.MonkeyPatch, *, fail: Exception | None = None, close_on_fail: bool = False):
    made: list[FakeTools] = []
    calls: list[dict] = []

    async def fake_build_harness_tools(**kwargs):
        tools = FakeTools()
        made.append(tools)
        calls.append(kwargs)
        if fail is not None:
            if close_on_fail:
                await tools.close()
            raise fail
        return tools

    monkeypatch.setattr("sagewai.work.runtime_harness.build_harness_tools", fake_build_harness_tools)
    return made, calls


TIERS = {
    "simple": HarnessTier(backend="ollama", model="qwen3:8b"),
    "complex": HarnessTier(backend="ollama", model="qwen3:32b"),
}
BACKENDS = {"ollama": "http://127.0.0.1:11434/v1"}


def _capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem",
                kind="filesystem",
                scope={"roots": ["."]},
                permissions=("workspace.write",),
            ),
        ),
    )


def _valid(request: WorkRequest) -> str:
    return json.dumps(
        {
            "project_id": request.project_id,
            "work_id": request.work_id,
            "run_id": request.run_id,
            "status": "passed",
            "summary": "done",
            "evidence_refs": [],
            "artifact_refs": [],
            "changes": [],
            "verification": [],
            "risks": [],
            "action_results": [],
            "profile_context": {},
        }
    )


@pytest.mark.asyncio
async def test_valid_result_on_first_turn_passes_with_selection_note_and_usage(tmp_path) -> None:
    factory = _factory([_valid(_request())], tokens=(100, 20), cost=9.99)
    sink = ListActivitySink()
    runtime = HarnessRuntime(
        tier="complex",
        tiers=TIERS,
        backends=BACKENDS,
        activity_sink=sink,
        agent_factory=factory,
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed"
    assert result.input_tokens == 100 and result.output_tokens == 20 and result.cost_usd == 0.0
    assert any("harness tier=complex backend=ollama model=qwen3:32b" in note for note in result.verification)
    assert factory.made[0].kwargs["model"] == "qwen3:32b" and factory.made[0].kwargs["api_base"] == BACKENDS["ollama"]
    assert factory.made[0].kwargs["api_key"] == "local"
    assert factory.made[0].kwargs["temperature"] == 0.1
    assert (
        factory.made[0].kwargs["strategy"].max_tool_calls_per_name
        == HarnessBudget().max_tool_calls
    )
    assert [item.kind for item in sink.items] == ["usage", "message"] or [item.kind for item in sink.items] == [
        "message",
        "usage",
    ]
    assert all(item.source == "harness" for item in sink.items)
    usage = next(item for item in sink.items if item.kind == "usage")
    assert usage.input_tokens == 100 and usage.output_tokens == 20 and usage.cost_usd == 0.0


@pytest.mark.asyncio
async def test_harness_selection_note_replaces_last_verification_entry_at_bound(tmp_path) -> None:
    payload = json.loads(_valid(_request()))
    payload["verification"] = [f"v{index}" for index in range(100)]
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([json.dumps(payload)]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert len(result.verification) == 100
    assert result.verification[-1] == "harness tier=simple backend=ollama model=qwen3:8b"


@pytest.mark.asyncio
async def test_invalid_answer_gets_feedback_turns_then_fails(tmp_path) -> None:
    factory = _factory(["not json", "{\"status\": \"passed\"}", "still wrong"])
    runtime = HarnessRuntime(tier="simple", tiers=TIERS, backends=BACKENDS, agent_factory=factory)
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "failed"
    assert "invalid structured result after 3 turns" in result.summary
    assert len(factory.made[0].prompts) == 3
    assert "not a valid OperatorResult" in factory.made[0].prompts[1]
    assert _request().work_id in factory.made[0].histories[1][1].content
    assert "not json" in factory.made[0].histories[1][2].content


@pytest.mark.asyncio
async def test_invalid_then_valid_passes_on_the_feedback_turn(tmp_path) -> None:
    factory = _factory(["oops", _valid(_request())])
    runtime = HarnessRuntime(tier="simple", tiers=TIERS, backends=BACKENDS, agent_factory=factory)
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed" and len(factory.made[0].prompts) == 2
    assert _request().work_id in factory.made[0].histories[1][1].content
    assert "oops" in factory.made[0].histories[1][2].content


@pytest.mark.asyncio
async def test_identity_mismatch_counts_as_invalid(tmp_path) -> None:
    wrong = json.loads(_valid(_request()))
    wrong["run_id"] = "other"
    factory = _factory([json.dumps(wrong), _valid(_request())])
    runtime = HarnessRuntime(tier="simple", tiers=TIERS, backends=BACKENDS, agent_factory=factory)
    assert (await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))).status == "passed"


@pytest.mark.asyncio
async def test_token_ceiling_fails_the_attempt(tmp_path) -> None:
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        budget=HarnessBudget(max_tokens=50),
        agent_factory=_factory([_valid(_request())], tokens=(60, 1)),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert "token ceiling" in result.summary


@pytest.mark.asyncio
async def test_tool_call_ceiling_fails_the_attempt(tmp_path) -> None:
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        budget=HarnessBudget(max_tool_calls=1),
        agent_factory=_factory([_valid(_request())], tool_calls=2),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert "tool-call ceiling" in result.summary


@pytest.mark.asyncio
async def test_wall_clock_ceiling_fails_the_attempt(tmp_path) -> None:
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        budget=HarnessBudget(max_wall_seconds=1),
        agent_factory=_factory([_valid(_request())], delay=2),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert "wall-clock ceiling" in result.summary


@pytest.mark.asyncio
async def test_router_decision_overrides_the_tier_model(tmp_path) -> None:
    class Router:
        async def route(self, *, identity, messages, tools=None, requested_model="", force_model_header=None):
            assert identity.project_id == "project-a"
            assert identity.key_id == "work:work-1"
            assert identity.org_id == "default"
            assert identity.user_id == "coordinator"
            assert identity.team_id is None
            assert identity.name == "run-1"
            return SimpleNamespace(target_model="qwen3:8b", tier=ComplexityTier.SIMPLE, reason="policy")

    factory = _factory([_valid(_request())])
    runtime = HarnessRuntime(
        tier="complex",
        tiers=TIERS,
        backends=BACKENDS,
        router=Router(),
        agent_factory=factory,
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert factory.made[0].kwargs["model"] == "qwen3:8b"
    assert "harness tier=simple backend=ollama model=qwen3:8b" in result.verification


@pytest.mark.asyncio
async def test_router_tier_outside_configuration_does_not_fail_the_attempt(tmp_path) -> None:
    class Router:
        async def route(self, *, identity, messages, tools=None, requested_model="", force_model_header=None):
            return SimpleNamespace(target_model="qwen3:32b", tier=ComplexityTier.MEDIUM, reason="classified")

    factory = _factory([_valid(_request())])
    runtime = HarnessRuntime(tier="complex", tiers=TIERS, backends=BACKENDS, router=Router(), agent_factory=factory)
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed"
    assert any("harness tier=complex backend=ollama model=qwen3:32b" in note for note in result.verification)


@pytest.mark.asyncio
async def test_router_model_selects_the_backend_of_its_own_tier(tmp_path) -> None:
    class Router:
        async def route(self, *, identity, messages, tools=None, requested_model="", force_model_header=None):
            return SimpleNamespace(target_model="qwen3:8b", tier=ComplexityTier.COMPLEX, reason="policy")

    tiers = {"simple": HarnessTier(backend="ollama", model="qwen3:8b"), "complex": HarnessTier(backend="vllm", model="qwen3:32b")}
    backends = {"ollama": "http://127.0.0.1:11434/v1", "vllm": "http://127.0.0.1:8000/v1"}
    factory = _factory([_valid(_request())])
    runtime = HarnessRuntime(tier="complex", tiers=tiers, backends=backends, router=Router(), agent_factory=factory)
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed"
    assert factory.made[0].kwargs["api_base"] == backends["ollama"]
    assert any("harness tier=simple backend=ollama model=qwen3:8b" in note for note in result.verification)


@pytest.mark.asyncio
async def test_router_decision_rejects_unconfigured_models(tmp_path) -> None:
    class Router:
        async def route(self, **_kwargs):
            return SimpleNamespace(target_model="not-configured", tier=ComplexityTier.SIMPLE, reason="policy")

    runtime = HarnessRuntime(
        tier="complex",
        tiers=TIERS,
        backends=BACKENDS,
        router=Router(),
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "configuration failure: router selected an unconfigured model"


def test_unknown_tier_or_backend_is_rejected_at_construction() -> None:
    with pytest.raises(ValueError):
        HarnessRuntime(tier="medium", tiers=TIERS, backends=BACKENDS)
    with pytest.raises(ValueError):
        HarnessRuntime(tier="simple", tiers=TIERS, backends={})


@pytest.mark.asyncio
async def test_harness_runtime_requires_a_workspace() -> None:
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([_valid(_request())]),
    )

    with pytest.raises(ValueError, match="HarnessRuntime requires a workspace"):
        await runtime.run(_request(), _capsule(), _capabilities(), None)


@pytest.mark.asyncio
async def test_tools_close_on_success(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _calls = _fake_tools(monkeypatch)
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "passed"
    assert tools[0].close_calls == 1


@pytest.mark.asyncio
async def test_tools_close_on_ceiling(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _calls = _fake_tools(monkeypatch)
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        budget=HarnessBudget(max_tool_calls=1),
        agent_factory=_factory([_valid(_request())], tool_calls=2),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert "tool-call ceiling" in result.summary
    assert tools[0].close_calls == 1


@pytest.mark.asyncio
async def test_tools_close_on_build_failure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _calls = _fake_tools(
        monkeypatch,
        fail=ValueError("filesystem: bad grant"),
        close_on_fail=True,
    )
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "configuration failure: filesystem: bad grant"
    assert tools[0].close_calls == 1


@pytest.mark.asyncio
async def test_tools_close_on_mid_run_exception(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools, _calls = _fake_tools(monkeypatch)
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([], raises=RuntimeError("backend crashed")),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "provider failure: backend crashed"
    assert tools[0].close_calls == 1


@pytest.mark.asyncio
async def test_provider_value_error_is_configuration_failure(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _tools, _calls = _fake_tools(monkeypatch)
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([], raises=ValueError("bad provider payload")),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "configuration failure: bad provider payload"


@pytest.mark.asyncio
async def test_grant_config_value_error_fails_and_names_the_grant(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _tools, _calls = _fake_tools(monkeypatch, fail=ValueError("filesystem: roots missing"))
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "configuration failure: filesystem: roots missing"


def _cli_capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="cli:echo",
                kind="cli",
                scope={"arg_pattern": ".*", "max_args": 2, "executable": "/bin/echo"},
                permissions=("workspace.execute",),
            ),
        ),
    )


@pytest.mark.asyncio
async def test_cli_grant_without_sandbox_fails(tmp_path) -> None:
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _cli_capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.summary == "configuration failure: cli:echo: cli grants require a sandbox backend"


@pytest.mark.asyncio
async def test_cli_sandbox_is_prefixed_into_the_workspace(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[str] = []

    class Sandbox:
        async def exec(self, tool_call):
            commands.append(tool_call.args["command"])
            return SimpleNamespace(ok=True)

    class SandboxToolCall:
        tool = "bash"

        def __init__(self, args):
            self.args = args

        def model_copy(self, *, update):
            return SandboxToolCall(update["args"])

    async def fake_build_harness_tools(**kwargs):
        await kwargs["sandbox"].exec(SandboxToolCall({"command": "echo ok"}))
        return FakeTools()

    monkeypatch.setattr("sagewai.work.runtime_harness.build_harness_tools", fake_build_harness_tools)
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        sandbox=Sandbox(),
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _cli_capabilities(), _workspace(tmp_path))

    assert result.status == "passed"
    assert commands == [f"cd {shlex.quote(str(tmp_path))} && echo ok"]


@pytest.mark.asyncio
async def test_archive_is_written_when_final_answer_is_invalid(tmp_path) -> None:
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        artifact_store=artifacts,
        agent_factory=_factory(["not json", "still not json", "nope"]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "failed"
    assert result.artifact_refs[-1].startswith("artifact://sha256:")
    assert artifacts.read(result.artifact_refs[-1], project_id=_request().project_id)


@pytest.mark.asyncio
async def test_text_message_content_emits_message_activity(tmp_path) -> None:
    sink = ListActivitySink()
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        activity_sink=sink,
        agent_factory=_factory([_valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "passed"
    assert [item.summary for item in sink.items if item.kind == "message"] == [_valid(_request())]


@pytest.mark.asyncio
async def test_harness_activity_redacts_credential_values(tmp_path) -> None:
    secret = "worker-local-token"
    sink = ListActivitySink()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    runtime = HarnessRuntime(
        tier="simple",
        tiers=TIERS,
        backends=BACKENDS,
        credential_values={"SCOPED_TOKEN": secret},
        activity_sink=sink,
        artifact_store=artifacts,
        agent_factory=_factory([f"token={secret}", _valid(_request())]),
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "passed"
    assert all(secret not in item.summary for item in sink.items)
    assert any("[REDACTED:SCOPED_TOKEN]" in item.summary for item in sink.items)
    stored = artifacts.read(result.artifact_refs[-1], project_id=_request().project_id)
    assert secret not in stored.decode()
    assert "[REDACTED:SCOPED_TOKEN]" in stored.decode()
