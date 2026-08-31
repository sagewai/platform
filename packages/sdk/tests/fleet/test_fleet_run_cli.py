# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CLI smoke for `sagewai fleet run` option parsing + --register-only."""
from __future__ import annotations

import pytest
from click.testing import CliRunner

from sagewai.cli.fleet import fleet_group


def test_run_help_lists_key_options():
    res = CliRunner().invoke(fleet_group, ["run", "--help"])
    assert res.exit_code == 0
    for opt in (
        "--name",
        "--models",
        "--pool",
        "--labels",
        "--capabilities",
        "--max-concurrent",
        "--claude-analysis-model",
        "--claude-analysis-effort",
        "--claude-analysis-max-budget-usd",
        "--claude-review-model",
        "--claude-review-effort",
        "--claude-review-max-budget-usd",
        "--codex-model",
        "--codex-reasoning-effort",
        "--exec",
        "--exec-timeout",
        "--env",
        "--env-file",
        "--image",
        "--docker-arg",
        "--register-only",
        "--once",
        "--worker-id",
        "--enrollment-key",
    ):
        assert opt in res.output


def test_run_help_lists_worker_secret_options():
    res = CliRunner().invoke(fleet_group, ["run", "--help"])
    assert res.exit_code == 0
    assert "--worker-secret" in res.output and "--creds-file" in res.output


def test_enqueue_help_lists_key_options():
    res = CliRunner().invoke(fleet_group, ["enqueue", "--help"])
    assert res.exit_code == 0
    for opt in ("--agent", "--message", "--model", "--pool"):
        assert opt in res.output


def test_enqueue_posts_task(monkeypatch):
    import httpx

    calls = {}

    class _Response:
        status_code = 201
        text = '{"run_id":"run-123"}'

        def json(self):
            return {"run_id": "run-123"}

    def fake_post(url, json, headers, timeout):
        calls.update({"url": url, "json": json, "headers": headers, "timeout": timeout})
        return _Response()

    monkeypatch.setattr(httpx, "post", fake_post)
    res = CliRunner().invoke(
        fleet_group,
        ["enqueue", "--agent", "helper", "-m", "hi", "--model", "gpt-4o"],
    )
    assert res.exit_code == 0, res.output
    assert calls["url"].endswith("/api/v1/fleet/tasks")
    assert calls["json"]["payload"] == {
        "agent": "helper",
        "message": "hi",
        "model": "gpt-4o",
    }
    assert calls["json"]["model"] == "gpt-4o"
    assert "run-123" in res.output


def test_run_register_only_invokes_register(monkeypatch, tmp_path):
    repository = tmp_path / "repository"
    repository.mkdir()
    calls = {}

    class _FakeRunner:
        def __init__(self, **kw):
            calls.update(kw)

        async def register(self):
            return "wid-123", "pending"

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "w1",
            "--models",
            "gpt-4o,ollama/llama3:70b",
            "--labels",
            "gpu=a100,zone=us",
            "--capabilities",
            "runtime.claude,cli.git",
            "--project",
            "project-a",
            "--work-repository",
            str(repository),
            "--register-only",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "wid-123" in res.output
    assert calls["name"] == "w1"
    assert calls["models"] == ["gpt-4o", "ollama/llama3:70b"]
    assert calls["labels"] == {"gpu": "a100", "zone": "us"}
    assert calls["capability_names"] == ["runtime.claude", "cli.git"]
    assert calls["project"] == "project-a"
    assert calls["task_handler"] is not None


def test_run_register_only_configures_worker_local_native_runtimes(
    monkeypatch,
    tmp_path,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    calls = {}

    class _FakeRunner:
        def __init__(self, **kw):
            calls.update(kw)

        async def register(self):
            return "wid-123", "pending"

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "native-worker",
            "--models",
            "advertised-model",
            "--capabilities",
            "runtime.claude,runtime.codex,filesystem.write",
            "--project",
            "project-a",
            "--work-repository",
            str(repository),
            "--claude-analysis-model",
            "claude-analysis",
            "--claude-analysis-effort",
            "medium",
            "--claude-analysis-max-budget-usd",
            "1.25",
            "--claude-review-model",
            "claude-review",
            "--claude-review-effort",
            "xhigh",
            "--claude-review-max-budget-usd",
            "2.50",
            "--codex-model",
            "gpt-5.6-sol",
            "--codex-reasoning-effort",
            "ultra",
            "--register-only",
        ],
    )

    assert res.exit_code == 0, res.output
    assert calls["capability_names"] == [
        "runtime.claude",
        "runtime.codex",
        "filesystem.write",
    ]
    assert calls["models"] == ["advertised-model"]
    handler = calls["task_handler"]
    assert handler._claude_analysis_runtime is not handler._claude_review_runtime
    assert handler._claude_analysis_runtime._model == "claude-analysis"
    assert handler._claude_analysis_runtime._effort == "medium"
    assert handler._claude_analysis_runtime._max_budget_usd == "1.25"
    assert handler._claude_review_runtime._model == "claude-review"
    assert handler._claude_review_runtime._effort == "xhigh"
    assert handler._claude_review_runtime._max_budget_usd == "2.50"
    assert handler._codex_runtime._model == "gpt-5.6-sol"
    assert handler._codex_runtime._reasoning_effort == "ultra"


def test_run_rejects_runtime_options_without_native_capability(monkeypatch):
    runner_called = False

    class _FakeRunner:
        def __init__(self, **kw):
            nonlocal runner_called
            runner_called = True

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "w",
            "--models",
            "gpt-4o",
            "--codex-model",
            "gpt-5-codex",
            "--register-only",
        ],
    )

    assert res.exit_code != 0
    assert "native runtime options require" in res.output
    assert runner_called is False


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("--claude-analysis-effort", "extreme"),
        ("--claude-review-effort", "extreme"),
        ("--codex-reasoning-effort", "extreme"),
    ],
)
def test_run_rejects_invalid_effort_before_worker_runner(
    monkeypatch,
    tmp_path,
    option,
    value,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    runner_called = False

    class _FakeRunner:
        def __init__(self, **kw):
            nonlocal runner_called
            runner_called = True

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "w",
            "--models",
            "gpt-4o",
            "--capabilities",
            "runtime.claude,runtime.codex,filesystem.write",
            "--project",
            "project-a",
            "--work-repository",
            str(repository),
            option,
            value,
            "--register-only",
        ],
    )

    assert res.exit_code != 0
    assert "Invalid value" in res.output
    assert runner_called is False


@pytest.mark.parametrize("budget", ["abc", "0", "-1"])
def test_run_rejects_invalid_budget_before_worker_runner(
    monkeypatch,
    tmp_path,
    budget,
):
    repository = tmp_path / "repository"
    repository.mkdir()
    runner_called = False

    class _FakeRunner:
        def __init__(self, **kw):
            nonlocal runner_called
            runner_called = True

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--name",
            "w",
            "--models",
            "gpt-4o",
            "--capabilities",
            "runtime.claude,filesystem.read",
            "--project",
            "project-a",
            "--work-repository",
            str(repository),
            "--claude-analysis-max-budget-usd",
            budget,
            "--register-only",
        ],
    )

    assert res.exit_code != 0
    assert "positive number" in res.output
    assert runner_called is False


def test_run_register_only_passes_task_isolation_options(monkeypatch):
    calls = {}

    class _FakeRunner:
        def __init__(self, **kw):
            calls.update(kw)

        async def register(self):
            return "wid-123", "pending"

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FakeRunner)
    res = CliRunner().invoke(
        fleet_group,
        [
            "run",
            "--register-only",
            "--name",
            "w",
            "--models",
            "gpt-4o",
            "--env",
            "A=1",
            "--image",
            "img",
            "--docker-arg",
            "--network=none",
        ],
    )
    assert res.exit_code == 0, res.output
    assert calls["task_env"] == {"A": "1"}
    assert calls["image"] == "img"
    assert calls["docker_args"] == ["--network=none"]


def test_run_surfaces_registration_401_with_token_hint(monkeypatch):
    from sagewai.fleet.runner import RegistrationError

    class _FailRunner:
        def __init__(self, **kw):
            pass

        async def register(self):
            raise RegistrationError(401, "unauthorized")

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FailRunner)
    res = CliRunner().invoke(
        fleet_group, ["run", "--name", "w", "--models", "gpt-4o", "--register-only"]
    )
    assert res.exit_code != 0
    assert "registration failed" in res.output.lower()
    assert "SAGEWAI_ADMIN_TOKEN" in res.output


def test_run_surfaces_gateway_connection_failure_without_traceback(monkeypatch):
    import httpx

    class _FailRunner:
        def __init__(self, **kw):
            pass

        async def run(self):
            raise httpx.ConnectError("connection refused")

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _FailRunner)
    res = CliRunner().invoke(fleet_group, ["run", "--worker-id", "w1"])
    assert res.exit_code != 0
    assert "Could not connect to Sagewai gateway" in res.output
    assert "Traceback" not in res.output


def test_run_daemon_terminal_auth_exits_2(monkeypatch):
    from sagewai.fleet.runner import TerminalAuthError

    class _TermRunner:
        def __init__(self, **kw):
            pass

        async def run(self):
            raise TerminalAuthError("worker revoked")

        async def aclose(self):
            pass

    monkeypatch.setattr("sagewai.cli.fleet.WorkerRunner", _TermRunner)
    # Daemon path (no --once / --register-only) with a reused, now-revoked worker.
    res = CliRunner().invoke(fleet_group, ["run", "--worker-id", "w-rev"])
    assert res.exit_code == 2
    assert "stopped" in res.output.lower() or "revoked" in res.output.lower()
