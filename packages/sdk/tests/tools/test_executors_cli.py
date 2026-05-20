# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
import pytest
from types import SimpleNamespace
from sagewai.tools.executors import cli as cli_exec
from sagewai.tools.registry import CatalogEntry


def _noop_creds(*, project_id, kind, id):
    return {}


def _make_entry() -> CatalogEntry:
    return CatalogEntry(
        id="echo_demo",
        version="0.1.0",
        title="Echo",
        description="x",
        category="test",
        kind="cli",
        sandbox_tier="SANDBOXED",
        exec_={"cli": {"binary": "/bin/echo", "argv_template": ["{message}"]}},
        scopes=frozenset(),
        setup={"auth_complexity": "none", "body": "x"},
    )


@pytest.mark.asyncio
async def test_cli_executor_runs_binary_and_returns_stdout(monkeypatch):
    async def fake_run(argv):
        return SimpleNamespace(stdout=b"hello\n", stderr=b"", returncode=0)

    monkeypatch.setattr(cli_exec, "_run_subprocess", fake_run)
    entry = _make_entry()
    out = await cli_exec.run(
        entry, operation=None, inputs={"message": "hello"},
        project_id="p1", get_credentials=_noop_creds,
    )
    assert out == {"stdout": "hello\n", "stderr": "", "returncode": 0}


@pytest.mark.asyncio
async def test_cli_executor_raises_on_nonzero_exit(monkeypatch):
    async def fake_run(argv):
        return SimpleNamespace(stdout=b"", stderr=b"boom", returncode=2)

    monkeypatch.setattr(cli_exec, "_run_subprocess", fake_run)
    entry = _make_entry()
    with pytest.raises(cli_exec.CliExecutionError):
        await cli_exec.run(
            entry, operation=None, inputs={"message": "x"},
            project_id="p1", get_credentials=_noop_creds,
        )
