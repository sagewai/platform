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
        acceptance_criteria=("fake executable passes",),
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


def _fake_runtime_executable(tmp_path: Path) -> Path:
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
            }}))
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
            else:
                print(json.dumps({{
                    "structured_output": result,
                    "usage": {{"output_tokens": 123}},
                }}))
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
    assert result.status == "passed"
    assert result.summary == "fake runtime completed"
    assert result.output_tokens == (123 if runtime_type is ClaudeRuntime else None)
    assert observation == {
        "ambient": None,
        "scoped": "worker-local-token",
        "capabilities": ["filesystem.write"],
        "has_session": False,
    }
    assert provider.declared_scopes == ["credential://workspace"]
    assert not hasattr(runtime, "intercept_tool_call")
    if runtime_type is ClaudeRuntime:
        tools = argv[argv.index("--tools") + 1].split(",")
        allowed_tools = argv[argv.index("--allowedTools") + 1].split(",")
        assert tools == ["Edit", "Glob", "Grep", "Read", "Write"]
        assert allowed_tools == [
            "Edit(/packages/sdk/sagewai/work/**)",
            "Read(/packages/sdk/sagewai/work/**)",
        ]
        assert "Bash" not in tools


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
