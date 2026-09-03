# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Native runtimes stream operator activity and archive per-run logs."""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore
from sagewai.work import (
    ClaudeRuntime,
    CodexRuntime,
    ListActivitySink,
    OperatorActivity,
    OperatorResult,
)
from sagewai.work.runtime import _NativeRuntime

from .test_runtime import _capabilities, _capsule, _request, _workspace

FIXTURES = Path(__file__).parent / "fixtures"
FAKE_ARTIFACT_REFS = ("artifact://sha256:" + "1" * 64,)


class FakeExecutable(str):
    def __new__(cls, path: Path, *, calls_path: Path | None = None):
        value = str.__new__(cls, str(path))
        value._calls_path = calls_path
        return value

    @property
    def calls(self) -> int:
        calls_path = self._calls_path
        if calls_path is None or not calls_path.exists():
            return 0
        return len(calls_path.read_text().splitlines())


class StubSecretProvider:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    async def env_for(self, **_kwargs) -> dict[str, str]:
        return dict(self._values)


def _write_fake(tmp_path: Path, name: str, body: str, *, calls_path: Path | None = None) -> FakeExecutable:
    executable = tmp_path / name
    executable.write_text(textwrap.dedent(body))
    executable.chmod(0o755)
    return FakeExecutable(executable, calls_path=calls_path)


def _operator_result_with_verification(count: int) -> OperatorResult:
    return OperatorResult(
        project_id="project-a",
        work_id="work-1",
        run_id="run-1",
        status="passed",
        summary="done",
        evidence_refs=(),
        artifact_refs=(),
        changes=(),
        verification=tuple(f"v{index}" for index in range(count)),
        risks=(),
        action_results=(),
        profile_context={},
    )


def _global_request():
    request = _request()
    return request.model_copy(
        update={
            "project_id": None,
            "action_scope": request.action_scope.model_copy(update={"project_id": None}),
            "action_intents": tuple(
                intent.model_copy(update={"project_id": None}) for intent in request.action_intents
            ),
        }
    )


def _global_capsule():
    capsule = _capsule()
    return capsule.model_copy(
        update={
            "project_id": None,
            "work_item": capsule.work_item.model_copy(update={"project_id": None}),
            "contract": capsule.contract.model_copy(
                update={
                    "project_id": None,
                    "acceptance_criteria": tuple(
                        criterion.model_copy(update={"project_id": None})
                        for criterion in capsule.contract.acceptance_criteria
                    ),
                }
            ),
        }
    )


def _global_capabilities():
    capabilities = _capabilities()
    return capabilities.model_copy(
        update={
            "project_id": None,
            "grants": tuple(
                grant.model_copy(update={"project_id": None, "credential_ref": None})
                for grant in capabilities.grants
            ),
        }
    )


class _GlobalWorkspace:
    ref = "workspace://attempt-1"
    project_id = None
    work_id = "work-1"

    def __init__(self, path: Path) -> None:
        self.path = path


@pytest.fixture
def fake_codex_streaming(tmp_path: Path) -> FakeExecutable:
    return _write_fake(
        tmp_path,
        "fake-codex-streaming",
        f"""\
        #!{sys.executable}
        import json
        import pathlib
        import sys

        prompt = json.load(sys.stdin)
        result = {{
            "project_id": prompt["request"]["project_id"],
            "work_id": prompt["request"]["work_id"],
            "run_id": prompt["request"]["run_id"],
            "status": "passed",
            "summary": "fake runtime completed",
            "evidence_refs": ["command://fake"],
            "artifact_refs": {list(FAKE_ARTIFACT_REFS)!r},
            "changes": ["runtime-observation.json"],
            "verification": ["fake executable"],
            "risks": [],
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
        output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
        output.write_text(json.dumps(result))
        for line in pathlib.Path({str(FIXTURES / "codex-exec.jsonl")!r}).read_text().splitlines():
            print(line, flush=True)
        """,
    )


@pytest.fixture
def fake_codex_echoing_env(tmp_path: Path) -> FakeExecutable:
    return _write_fake(
        tmp_path,
        "fake-codex-echoing-env",
        f"""\
        #!{sys.executable}
        import json
        import os
        import pathlib
        import sys

        prompt = json.load(sys.stdin)
        secret = os.environ.get("SECRET_TOKEN", "")
        result = {{
            "project_id": prompt["request"]["project_id"],
            "work_id": prompt["request"]["work_id"],
            "run_id": prompt["request"]["run_id"],
            "status": "passed",
            "summary": "fake runtime completed",
            "evidence_refs": ["command://fake"],
            "artifact_refs": [],
            "changes": [],
            "verification": ["fake executable"],
            "risks": [],
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
        output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
        output.write_text(json.dumps(result))
        print(json.dumps({{
            "type": "item.completed",
            "item": {{"id": "item_secret", "type": "agent_message", "text": f"token={{secret}}"}}
        }}), flush=True)
        print(f"stderr-token={{secret}}", file=sys.stderr, flush=True)
        print(json.dumps({{
            "type": "turn.completed",
            "usage": {{"input_tokens": 1, "output_tokens": 1}}
        }}), flush=True)
        """,
    )


@pytest.fixture
def fake_codex_long_activity_log(tmp_path: Path) -> FakeExecutable:
    return _write_fake(
        tmp_path,
        "fake-codex-long-activity-log",
        f"""\
        #!{sys.executable}
        import json
        import pathlib
        import sys

        prompt = json.load(sys.stdin)
        result = {{
            "project_id": prompt["request"]["project_id"],
            "work_id": prompt["request"]["work_id"],
            "run_id": prompt["request"]["run_id"],
            "status": "passed",
            "summary": "fake runtime completed",
            "evidence_refs": ["command://fake"],
            "artifact_refs": [],
            "changes": [],
            "verification": ["fake executable"],
            "risks": [],
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
        output = pathlib.Path(sys.argv[sys.argv.index("--output-last-message") + 1])
        output.write_text(json.dumps(result))
        for index in range(20):
            print(f"raw-{{index}}-{{'x' * 600}}", flush=True)
        """,
    )


@pytest.fixture
def fake_claude_stream_without_structured_output(tmp_path: Path) -> FakeExecutable:
    calls_path = tmp_path.parent / f"{tmp_path.name}-claude-calls.txt"
    return _write_fake(
        tmp_path,
        "fake-claude-stream-without-structured-output",
        f"""\
        #!{sys.executable}
        import json
        import pathlib
        import sys

        calls_path = pathlib.Path({str(calls_path)!r})
        with calls_path.open("a") as handle:
            handle.write("call\\n")
        prompt = json.load(sys.stdin)
        result = {{
            "project_id": prompt["request"]["project_id"],
            "work_id": prompt["request"]["work_id"],
            "run_id": prompt["request"]["run_id"],
            "status": "passed",
            "summary": "fake runtime completed",
            "evidence_refs": ["command://fake"],
            "artifact_refs": [],
            "changes": [],
            "verification": ["fake executable"],
            "risks": [],
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
        output_format = sys.argv[sys.argv.index("--output-format") + 1]
        if output_format == "stream-json":
            print(json.dumps({{
                "type": "assistant",
                "message": {{"content": [{{"type": "text", "text": "streaming"}}]}}
            }}), flush=True)
            print(json.dumps({{
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": json.dumps(result),
                "usage": {{"input_tokens": 12, "output_tokens": 7}},
                "total_cost_usd": 0.01
            }}), flush=True)
        else:
            print(json.dumps({{
                "structured_output": result,
                "usage": {{"input_tokens": 12, "output_tokens": 7}},
                "total_cost_usd": 0.01
            }}), flush=True)
        """,
        calls_path=calls_path,
    )


@pytest.fixture
def fake_claude_stream_with_structured_output(tmp_path: Path) -> FakeExecutable:
    calls_path = tmp_path.parent / f"{tmp_path.name}-claude-structured-calls.txt"
    return _write_fake(
        tmp_path,
        "fake-claude-stream-with-structured-output",
        f"""\
        #!{sys.executable}
        import json
        import pathlib
        import sys

        calls_path = pathlib.Path({str(calls_path)!r})
        output_format = sys.argv[sys.argv.index("--output-format") + 1]
        with calls_path.open("a") as handle:
            handle.write(output_format + "\\n")
        prompt = json.load(sys.stdin)
        result = {{
            "project_id": prompt["request"]["project_id"],
            "work_id": prompt["request"]["work_id"],
            "run_id": prompt["request"]["run_id"],
            "status": "passed",
            "summary": "fake runtime completed",
            "evidence_refs": ["command://fake"],
            "artifact_refs": [],
            "changes": [],
            "verification": ["fake executable"],
            "risks": [],
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
        print(json.dumps({{
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "structured_output": result,
            "usage": {{"input_tokens": 12, "output_tokens": 7}},
            "total_cost_usd": 0.01
        }}), flush=True)
        """,
        calls_path=calls_path,
    )


def test_native_runtime_selection_note_replaces_last_verification_entry_at_bound() -> None:
    runtime = _NativeRuntime(
        executable="/bin/true",
        selection_note="runtime selection: codex gpt-5",
    )

    result = runtime._with_selection_evidence(_operator_result_with_verification(100))

    assert len(result.verification) == 100
    assert result.verification[-1] == "runtime selection: codex gpt-5"


@pytest.mark.asyncio
async def test_codex_runtime_streams_activity_and_archives_the_log(
    tmp_path: Path,
    fake_codex_streaming: FakeExecutable,
) -> None:
    sink = ListActivitySink()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    runtime = CodexRuntime(
        executable=fake_codex_streaming,
        activity_sink=sink,
        artifact_store=artifacts,
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed"
    assert sink.items
    assert all(item.run_id == _request().run_id for item in sink.items)
    assert any(item.kind == "usage" for item in sink.items)
    archive_ref = result.artifact_refs[-1]
    assert result.artifact_refs == (*FAKE_ARTIFACT_REFS, archive_ref)
    assert archive_ref.startswith("artifact://sha256:")
    stored = artifacts.read(archive_ref, project_id=_request().project_id)
    archived = [OperatorActivity.model_validate_json(line) for line in stored.decode().splitlines()]
    assert archived == sink.items


@pytest.mark.asyncio
async def test_codex_runtime_streams_global_activity_and_archives_the_log(
    tmp_path: Path,
    fake_codex_streaming: FakeExecutable,
) -> None:
    sink = ListActivitySink()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    runtime = CodexRuntime(
        executable=fake_codex_streaming,
        activity_sink=sink,
        artifact_store=artifacts,
    )
    request = _global_request()
    result = await runtime.run(request, _global_capsule(), _global_capabilities(), _GlobalWorkspace(tmp_path))
    assert result.status == "passed"
    assert sink.items
    assert all(item.project_id is None for item in sink.items)
    archive_ref = result.artifact_refs[-1]
    stored = artifacts.read(archive_ref, project_id=None)
    archived = [OperatorActivity.model_validate_json(line) for line in stored.decode().splitlines()]
    assert archived == sink.items


@pytest.mark.asyncio
async def test_codex_runtime_redacts_scoped_credentials_in_activity(
    tmp_path: Path,
    fake_codex_echoing_env: FakeExecutable,
) -> None:
    sink = ListActivitySink()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    runtime = CodexRuntime(
        executable=fake_codex_echoing_env,
        secret_provider=StubSecretProvider({"SECRET_TOKEN": "s3cr3t"}),
        activity_sink=sink,
        artifact_store=artifacts,
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert all("s3cr3t" not in item.summary for item in sink.items)
    assert any("[REDACTED:SECRET_TOKEN]" in item.summary for item in sink.items)
    assert any(
        item.kind == "raw" and item.summary == "stderr-token=[REDACTED:SECRET_TOKEN]"
        for item in sink.items
    )
    stored = artifacts.read(result.artifact_refs[-1], project_id=_request().project_id)
    assert "s3cr3t" not in stored.decode()
    assert "[REDACTED:SECRET_TOKEN]" in stored.decode()


@pytest.mark.asyncio
async def test_codex_runtime_truncates_archived_activity_log_after_the_byte_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_codex_long_activity_log: FakeExecutable,
) -> None:
    sink = ListActivitySink()
    artifacts = LocalArtifactStore(root=tmp_path / "objects")
    monkeypatch.setattr("sagewai.work.activity.ACTIVITY_LOG_MAX_BYTES", 2100)
    runtime = CodexRuntime(
        executable=fake_codex_long_activity_log,
        activity_sink=sink,
        artifact_store=artifacts,
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    stored = artifacts.read(result.artifact_refs[-1], project_id=_request().project_id)
    archived = [OperatorActivity.model_validate_json(line) for line in stored.decode().splitlines()]
    assert len(stored) <= 2100
    assert len(sink.items) == 20
    assert len(archived) < len(sink.items)
    assert archived[-1].kind == "raw"
    assert archived[-1].summary == "truncated"
    assert sum(item.kind == "raw" and item.summary == "truncated" for item in archived) == 1


@pytest.mark.asyncio
async def test_claude_runtime_uses_stream_json_and_falls_back_to_json(
    tmp_path: Path,
    fake_claude_stream_without_structured_output: FakeExecutable,
) -> None:
    sink = ListActivitySink()
    runtime = ClaudeRuntime(
        executable=fake_claude_stream_without_structured_output,
        activity_sink=sink,
        selection_note="claude: note",
    )
    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))
    assert result.status == "passed"
    assert any(item.kind == "usage" for item in sink.items)
    assert result.verification[-2] == (
        "claude: stream-json result lacked structured_output; fallback to --output-format json"
    )
    assert result.verification[-1] == "claude: note"
    assert fake_claude_stream_without_structured_output.calls == 2


@pytest.mark.asyncio
async def test_claude_runtime_keeps_structured_result_when_activity_parser_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_claude_stream_with_structured_output: FakeExecutable,
) -> None:
    def fail_parse_claude_stream_line(_line, _counter):
        raise RuntimeError("parser exploded")

    monkeypatch.setattr(
        "sagewai.work.runtime.parse_claude_stream_line",
        fail_parse_claude_stream_line,
    )
    runtime = ClaudeRuntime(
        executable=fake_claude_stream_with_structured_output,
        timeout=5,
    )

    result = await runtime.run(_request(), _capsule(), _capabilities(), _workspace(tmp_path))

    assert result.status == "passed"
    assert result.summary == "fake runtime completed"
    assert result.input_tokens == 12
    assert result.output_tokens == 7
    assert fake_claude_stream_with_structured_output.calls == 1
