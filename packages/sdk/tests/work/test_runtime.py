# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Native operator runtime and scoped-capability tests."""

from __future__ import annotations

import json
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from sagewai.work import (
    AcceptanceCriterion,
    ActionIntent,
    ActionScope,
    CapabilityGrant,
    CapabilitySet,
    ClaudeRuntime,
    CodexRuntime,
    OperatorResult,
    Reversibility,
    TaskCapsule,
    WorkContract,
    WorkItem,
    WorkRequest,
)
from sagewai.work.profiles.software import SoftwareWorkspace

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


def _intent() -> ActionIntent:
    return ActionIntent(
        project_id="project-a",
        action_id="action-1",
        capability="filesystem.write",
        target="packages/sdk/sagewai/work/runtime.py",
        expected_effect="Runtime implementation exists",
        scope={"allowed_targets": ["packages/sdk/sagewai/work"]},
        risk="low",
        reversibility=Reversibility.SNAPSHOT_REVERSIBLE,
        required_permission="workspace.write",
        evidence_refs=("contract://1",),
    )


def _request() -> WorkRequest:
    return WorkRequest(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        stage="implement",
        action_scope=ActionScope(
            project_id="project-a",
            objective="Implement runtime",
            allowed_targets=("packages/sdk/sagewai/work",),
            allowed_capabilities=("filesystem.write",),
        ),
        action_intents=(_intent(),),
        control_preconditions=(),
    )


def _capsule() -> TaskCapsule:
    item = WorkItem(
        id="work-1",
        project_id="project-a",
        profile="software",
        source="local",
        source_ref="source://task",
        title="Runtime",
        description="Implement the runtime",
        created_at=NOW,
    )
    contract = WorkContract(
        id="contract-1",
        project_id="project-a",
        work_id="work-1",
        version=1,
        goal="Implement runtime",
        allowed_scope=("packages/sdk/sagewai/work",),
        acceptance_criteria=(
            AcceptanceCriterion(
                id="criterion-runtime-executable",
                project_id="project-a",
                statement="fake executable passes",
                verification_kind="deterministic",
            ),
        ),
        constraints=(),
        non_goals=(),
        evidence_refs=(),
        assumption_ids=(),
        risk="low",
        design_required=False,
    )
    return TaskCapsule(
        project_id="project-a",
        work_id="work-1",
        stage="implement",
        work_item=item,
        contract=contract,
        knowledge_refs=(),
        knowledge_items=(),
        knowledge_items_considered=0,
        artifact_bytes_referenced=0,
        open_assumption_ids=(),
        prior_result_refs=(),
    )


def _workspace(path: Path) -> SoftwareWorkspace:
    return SoftwareWorkspace(
        ref="workspace://attempt-1",
        project_id="project-a",
        work_id="work-1",
        attempt_id="attempt-1",
        repository=path,
        path=path,
        base_sha="a" * 40,
        initial_sha="a" * 40,
    )


def _fake_runtime_executable(
    tmp_path: Path,
    *,
    envelope_padding: int = 0,
    failure_padding: int = 0,
    include_usage: bool = True,
    include_cost: bool = True,
) -> Path:
    executable = tmp_path / "fake-operator"
    executable.write_text(
        textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import pathlib
            import sys

            prompt = json.load(sys.stdin)
            pathlib.Path("runtime-observation.json").write_text(json.dumps({{
                "ambient": os.environ.get("AMBIENT_SECRET"),
                "scoped": os.environ.get("SCOPED_TOKEN"),
                "capabilities": [g["name"] for g in prompt["capabilities"]["grants"]],
                "has_session": "session" in prompt,
                "argv": sys.argv[1:],
                "result_contract": prompt.get("result_contract"),
                "output_schema": (
                    json.loads(pathlib.Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
                    if "--output-schema" in sys.argv
                    else None
                ),
            }}))
            if {failure_padding}:
                print("x" * {failure_padding} + "tail-error", file=sys.stderr)
                raise SystemExit(1)
            result = {{
                "project_id": prompt["request"]["project_id"],
                "work_id": prompt["request"]["work_id"],
                "run_id": prompt["request"]["run_id"],
                "status": "passed",
                "summary": "fake runtime completed",
                "evidence_refs": ["command://fake"],
                "artifact_refs": [],
                "changes": ["runtime-observation.json"],
                "verification": ["fake executable"],
                "risks": [],
                "output_tokens": 999,
                "action_results": [{{
                    "project_id": prompt["request"]["project_id"],
                    "action_id": "action-1",
                    "status": "succeeded",
                    "external_ref": None,
                    "evidence_refs": ["command://fake"],
                    "started_at": "2026-08-26T12:00:00Z",
                    "completed_at": "2026-08-26T12:00:00Z"
                }}],
                "profile_context": {{}}
            }}
            if "--output-last-message" in sys.argv:
                output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
                output.write_text(json.dumps(result))
            elif "--output-format" in sys.argv and sys.argv[sys.argv.index("--output-format") + 1] == "stream-json":
                print(json.dumps({{
                    "type": "assistant",
                    "message": {{"content": [{{"type": "text", "text": "fake runtime completed"}}]}},
                }}))
                print(json.dumps({{
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "structured_output": result,
                    "result": json.dumps(result),
                    "usage": {{"input_tokens": 12, "output_tokens": 7}},
                    "total_cost_usd": 0.01,
                }}))
            else:
                envelope = {{
                    "structured_output": result,
                    "result": "x" * {envelope_padding},
                }}
                if {include_usage!r}:
                    envelope["usage"] = {{"input_tokens": 12, "output_tokens": 7}}
                if {include_cost!r}:
                    envelope["total_cost_usd"] = 0.01
                print(json.dumps(envelope))
            """
        )
    )
    executable.chmod(0o755)
    return executable


class RecordingSecretProvider:
    def __init__(self) -> None:
        self.declared_scopes: list[str] | None = None

    async def env_for(self, *, declared_scopes: list[str], **_kwargs) -> dict[str, str]:
        self.declared_scopes = declared_scopes
        return {"SCOPED_TOKEN": "worker-local-token"}


def _capabilities() -> CapabilitySet:
    return CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="filesystem.write",
                kind="filesystem",
                scope={"roots": ["packages/sdk/sagewai/work"]},
                permissions=("workspace.write",),
                credential_ref="credential://workspace",
            ),
        ),
    )


def test_capability_set_scopes_grants_without_secret_values() -> None:
    capabilities = CapabilitySet(
        project_id="project-a",
        grants=(
            *_capabilities().grants,
            CapabilityGrant(
                project_id="project-a",
                name="production.deploy",
                kind="api",
                scope={"environment": "production"},
                permissions=("deploy",),
                credential_ref="credential://production",
            ),
        ),
    )

    scoped = capabilities.for_names(("filesystem.write",))

    assert [grant.name for grant in scoped.grants] == ["filesystem.write"]
    assert scoped.credential_refs() == ("credential://workspace",)
    assert "worker-local-token" not in scoped.model_dump_json()


@pytest.mark.asyncio
@pytest.mark.parametrize("runtime_type", [CodexRuntime, ClaudeRuntime])
async def test_native_runtime_uses_fake_executable_without_session_or_api_key(
    runtime_type,
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AMBIENT_SECRET", "must-not-leak")
    provider = RecordingSecretProvider()
    runtime = runtime_type(
        executable=str(_fake_runtime_executable(tmp_path)),
        secret_provider=provider,
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    result = await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    argv = observation.pop("argv")
    result_contract = observation.pop("result_contract")
    output_schema = observation.pop("output_schema")
    assert result.status == "passed"
    assert result.summary == "fake runtime completed"
    assert result.output_tokens == (7 if runtime_type is ClaudeRuntime else None)
    assert observation == {
        "ambient": None,
        "scoped": "worker-local-token",
        "capabilities": ["filesystem.write"],
        "has_session": False,
    }
    assert result_contract["identity"] == {
        "project_id": "project-a",
        "work_id": "work-1",
        "run_id": "run-1",
    }
    assert result_contract["required_action_results"] == [
        {"project_id": "project-a", "action_id": "action-1"}
    ]
    assert result_contract["required_profile_context"] == {}
    assert provider.declared_scopes == ["credential://workspace"]
    assert not hasattr(runtime, "intercept_tool_call")
    if runtime_type is CodexRuntime:
        assert result.input_tokens is None
        assert result.cost_usd is None
        assert argv[:7] == [
            "exec",
            "--ephemeral",
            "--sandbox",
            "workspace-write",
            "--cd",
            str(workspace_path),
            "--output-schema",
        ]
        assert Path(argv[7]).name == "schema.json"
        assert argv[8] == "--output-last-message"
        assert Path(argv[9]).name == "result.json"
        assert argv[10:] == ["--json", "-"]
        properties = output_schema["properties"]
        assert properties["profile_context"] == {
            "additionalProperties": False,
            "properties": {},
            "title": "Profile Context",
            "type": "object",
        }
        assert set(output_schema["required"]) == set(properties)
        assert "default" not in properties["output_tokens"]
    else:
        assert output_schema is None
    if runtime_type is ClaudeRuntime:
        assert result.input_tokens == 12
        assert result.cost_usd == 0.01
        assert argv == [
            "--print",
            "--no-session-persistence",
            "--safe-mode",
            "--strict-mcp-config",
            "--permission-mode",
            "dontAsk",
            "--tools",
            "Edit,Glob,Grep,Read,Write",
            "--allowedTools",
            "Edit(/packages/sdk/sagewai/work/**),Read(/packages/sdk/sagewai/work/**)",
            "--output-format",
            "stream-json",
            "--verbose",
            "--json-schema",
            json.dumps(OperatorResult.model_json_schema(), sort_keys=True),
        ]
        tools = argv[argv.index("--tools") + 1].split(",")
        allowed_tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert tools == ["Edit", "Glob", "Grep", "Read", "Write"]
        assert allowed_tools == [
            "Edit(/packages/sdk/sagewai/work/**)",
            "Read(/packages/sdk/sagewai/work/**)",
        ]
        assert "Bash" not in tools


@pytest.mark.asyncio
async def test_claude_runtime_omitted_usage_fields_are_none(tmp_path: Path) -> None:
    runtime = ClaudeRuntime(
        executable=str(
            _fake_runtime_executable(
                tmp_path,
                include_usage=False,
                include_cost=False,
            )
        ),
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    result = await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    assert result.output_tokens is None
    assert result.input_tokens is None
    assert result.cost_usd is None


@pytest.mark.asyncio
async def test_claude_runtime_emits_configured_native_options(
    tmp_path: Path,
) -> None:
    runtime = ClaudeRuntime(
        executable=str(_fake_runtime_executable(tmp_path)),
        model="claude-sonnet-analysis",
        effort="xhigh",
        max_budget_usd="1.25",
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    assert observation["argv"] == [
        "--model",
        "claude-sonnet-analysis",
        "--effort",
        "xhigh",
        "--max-budget-usd",
        "1.25",
        "--print",
        "--no-session-persistence",
        "--safe-mode",
        "--strict-mcp-config",
        "--permission-mode",
        "dontAsk",
        "--tools",
        "Edit,Glob,Grep,Read,Write",
        "--allowedTools",
        "Edit(/packages/sdk/sagewai/work/**),Read(/packages/sdk/sagewai/work/**)",
        "--output-format",
        "stream-json",
        "--verbose",
        "--json-schema",
        json.dumps(OperatorResult.model_json_schema(), sort_keys=True),
    ]


@pytest.mark.asyncio
async def test_claude_runtime_omits_unset_native_options(tmp_path: Path) -> None:
    runtime = ClaudeRuntime(
        executable=str(_fake_runtime_executable(tmp_path)),
        model="claude-sonnet-analysis",
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    argv = observation["argv"]
    assert argv[:2] == ["--model", "claude-sonnet-analysis"]
    assert "--effort" not in argv
    assert "--max-budget-usd" not in argv


@pytest.mark.parametrize(
    ("model", "reasoning_effort"),
    [
        ("gpt-5.5", "xhigh"),
        ("gpt-5.6-sol", "ultra"),
        ("gpt-5.6-terra", "ultra"),
        ("gpt-5.6-luna", "max"),
    ],
)
@pytest.mark.asyncio
async def test_codex_runtime_emits_configured_native_options(
    tmp_path: Path,
    model: str,
    reasoning_effort: str,
) -> None:
    runtime = CodexRuntime(
        executable=str(_fake_runtime_executable(tmp_path)),
        model=model,
        reasoning_effort=reasoning_effort,
        selection_note=f"runtime.codex: model={model}, effort={reasoning_effort}",
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    result = await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    argv = observation["argv"]
    assert argv == [
        "exec",
        "--model",
        model,
        "-c",
        f"model_reasoning_effort={reasoning_effort}",
        "--ephemeral",
        "--sandbox",
        "workspace-write",
        "--cd",
        str(workspace_path),
        "--output-schema",
        argv[11],
        "--output-last-message",
        argv[13],
        "--json",
        "-",
    ]
    assert Path(argv[11]).name == "schema.json"
    assert Path(argv[13]).name == "result.json"
    assert result.verification[-1] == (
        f"runtime.codex: model={model}, effort={reasoning_effort}"
    )


@pytest.mark.asyncio
async def test_codex_runtime_omits_unset_native_options(tmp_path: Path) -> None:
    runtime = CodexRuntime(
        executable=str(_fake_runtime_executable(tmp_path)),
        model="gpt-5-codex",
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    argv = observation["argv"]
    assert argv[:3] == ["exec", "--model", "gpt-5-codex"]
    assert "-c" not in argv


@pytest.mark.parametrize(
    ("runtime_factory", "match"),
    [
        (lambda: ClaudeRuntime(effort=""), "Claude effort"),
        (lambda: ClaudeRuntime(max_budget_usd="0"), "positive number"),
        (lambda: CodexRuntime(reasoning_effort=" xhigh"), "Codex reasoning effort"),
    ],
)
def test_native_runtime_rejects_invalid_configuration(
    runtime_factory,
    match: str,
) -> None:
    with pytest.raises(ValueError, match=match):
        runtime_factory()


@pytest.mark.asyncio
async def test_claude_scopes_cli_and_mcp_tools_from_grants(tmp_path: Path) -> None:
    runtime = ClaudeRuntime(
        executable=str(_fake_runtime_executable(tmp_path)),
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()
    capabilities = CapabilitySet(
        project_id="project-a",
        grants=(
            CapabilityGrant(
                project_id="project-a",
                name="cli:git",
                kind="cli",
                scope={},
                permissions=("workspace.execute",),
            ),
            CapabilityGrant(
                project_id="project-a",
                name="mcp:github",
                kind="mcp",
                scope={},
                permissions=("read",),
            ),
        ),
    )

    result = await runtime.run(
        _request(),
        _capsule(),
        capabilities,
        _workspace(workspace_path),
    )

    observation = json.loads((workspace_path / "runtime-observation.json").read_text())
    argv = observation["argv"]
    tools = argv[argv.index("--tools") + 1].split(",")
    allowed_tools = argv[argv.index("--allowedTools") + 1].split(",")
    assert result.status == "passed"
    assert tools == ["Bash"]
    assert allowed_tools == ["Bash(git *)", "mcp__github__*"]
    assert all("curl" not in selector for selector in allowed_tools)
    assert all("kubectl" not in selector for selector in allowed_tools)


@pytest.mark.asyncio
async def test_claude_accepts_schema_bounded_output_larger_than_preview_limit(
    tmp_path: Path,
) -> None:
    runtime = ClaudeRuntime(
        executable=str(_fake_runtime_executable(tmp_path, envelope_padding=5000)),
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    result = await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    assert result.summary == "fake runtime completed"


@pytest.mark.asyncio
async def test_codex_failure_preserves_bounded_stderr_tail(tmp_path: Path) -> None:
    runtime = CodexRuntime(
        executable=str(_fake_runtime_executable(tmp_path, failure_padding=5000)),
        model="gpt-5.5",
        reasoning_effort="xhigh",
        selection_note="runtime.codex: model=gpt-5.5, effort=xhigh",
        timeout=5,
    )
    workspace_path = tmp_path / "workspace"
    workspace_path.mkdir()

    result = await runtime.run(
        _request(),
        _capsule(),
        _capabilities(),
        _workspace(workspace_path),
    )

    assert result.status == "failed"
    assert len(result.summary) == 4000
    assert result.summary.endswith("tail-error\n")
    assert result.verification == (
        "runtime.codex: model=gpt-5.5, effort=xhigh",
    )


def test_native_runtime_prompt_maps_profile_result_schemas() -> None:
    capsule = _capsule().model_copy(
        update={
            "profile_context": {
                "software": {"base_sha": "a" * 40},
                "analysis_result_schema": {
                    "type": "object",
                    "properties": {
                        "project_id": {"type": ["string", "null"]},
                        "attempt_id": {"type": "string"},
                    },
                    "required": ["project_id", "attempt_id"],
                },
            }
        }
    )

    payload = json.loads(
        ClaudeRuntime._prompt(_request(), capsule, _capabilities())
    )

    assert payload["result_contract"]["required_profile_context"] == {
        "analysis_result": {
            "schema_ref": "capsule.profile_context.analysis_result_schema",
            "identity": {
                "project_id": "project-a",
                "attempt_id": "run-1",
            },
        }
    }
    assert any(
        "exact profile_context key" in rule
        for rule in payload["result_contract"]["rules"]
    )
    assert any(
        "profile result identity" in rule
        for rule in payload["result_contract"]["rules"]
    )


def test_operator_result_schema_is_structured_and_bounded() -> None:
    values = {
        "project_id": "project-a",
        "work_id": "work-1",
        "run_id": "run-1",
        "status": "passed",
        "summary": "x" * 4001,
        "evidence_refs": (),
        "artifact_refs": (),
        "changes": (),
        "verification": (),
        "risks": (),
        "action_results": (),
        "profile_context": {},
    }

    with pytest.raises(ValidationError):
        OperatorResult.model_validate(values)


def test_operator_result_accepts_token_and_cost_fields() -> None:
    from sagewai.work.runtime import OperatorResult

    result = OperatorResult(
        project_id="p",
        work_id="w",
        run_id="r",
        status="passed",
        summary="ok",
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=(),
        risks=(),
        action_results=(),
        input_tokens=120,
        output_tokens=30,
        cost_usd=0.0042,
    )
    assert result.input_tokens == 120 and result.cost_usd == 0.0042
    assert OperatorResult.model_validate(result.model_dump(mode="json")) == result


def test_codex_schema_requires_the_new_fields_without_defaults() -> None:
    from sagewai.work.runtime import _codex_result_schema

    schema = _codex_result_schema()
    for name in ("output_tokens", "input_tokens", "cost_usd"):
        assert name in schema["required"]
        assert "default" not in schema["properties"][name]


def test_work_request_rejects_action_scope_from_different_project() -> None:
    values = _request().model_dump()
    values["action_scope"]["project_id"] = "project-b"

    with pytest.raises(ValidationError, match="action scope belongs to a different project"):
        WorkRequest.model_validate(values)


@pytest.mark.parametrize(
    ("result_project", "action_project"),
    [("project-a", None), (None, "global")],
)
def test_operator_result_rejects_action_result_from_different_project(
    result_project: str | None,
    action_project: str | None,
) -> None:
    values = {
        "project_id": result_project,
        "work_id": "work-1",
        "run_id": "run-1",
        "status": "passed",
        "summary": "operator completed",
        "evidence_refs": (),
        "artifact_refs": (),
        "changes": (),
        "verification": (),
        "risks": (),
        "action_results": (
            {
                "project_id": action_project,
                "action_id": "action-1",
                "status": "succeeded",
                "external_ref": None,
                "evidence_refs": (),
                "started_at": NOW,
                "completed_at": NOW,
            },
        ),
        "profile_context": {},
    }

    with pytest.raises(ValidationError, match="action result belongs to a different project"):
        OperatorResult.model_validate(values)
