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

from click.testing import CliRunner

from sagewai.cli.fleet import fleet_group


def test_run_help_lists_key_options():
    res = CliRunner().invoke(fleet_group, ["run", "--help"])
    assert res.exit_code == 0
    for opt in ("--name", "--models", "--pool", "--labels", "--max-concurrent",
                "--exec", "--register-only", "--once", "--worker-id", "--enrollment-key"):
        assert opt in res.output


def test_run_register_only_invokes_register(monkeypatch):
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
        ["run", "--name", "w1", "--models", "gpt-4o,ollama/llama3:70b",
         "--labels", "gpu=a100,zone=us", "--register-only"],
    )
    assert res.exit_code == 0, res.output
    assert "wid-123" in res.output
    assert calls["name"] == "w1"
    assert calls["models"] == ["gpt-4o", "ollama/llama3:70b"]
    assert calls["labels"] == {"gpu": "a100", "zone": "us"}


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
