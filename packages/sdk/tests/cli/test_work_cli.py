# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""CLI coverage for direct local Work lifecycle commands."""

from __future__ import annotations

from importlib import import_module
from types import SimpleNamespace

from click.testing import CliRunner

from sagewai.cli import cli
from sagewai.cli.work import work as work_cli

work_module = import_module("sagewai.cli.work")


def test_work_group_is_registered() -> None:
    result = CliRunner().invoke(cli, ["work", "--help"])

    assert result.exit_code == 0
    assert "start" in result.output
    assert "status" in result.output
    assert "resume" in result.output
    assert "approve" in result.output
    assert "pending" in result.output


def test_work_start_runs_direct_lifecycle(monkeypatch) -> None:
    seen = []

    async def fake_start(description: str):
        seen.append(description)
        return SimpleNamespace(work_id="work-1", status="READY_TO_MERGE")

    monkeypatch.setattr(work_module, "_start_work", fake_start)

    result = CliRunner().invoke(
        work_cli,
        ["start", "local change description"],
    )

    assert result.exit_code == 0, result.output
    assert seen == ["local change description"]
    assert "work-1" in result.output
    assert "READY_TO_MERGE" in result.output
    assert "COMPLETE" not in result.output


def test_work_status_is_project_scoped_and_reports_not_found(monkeypatch) -> None:
    seen = []

    async def fake_status(work_id: str):
        seen.append(work_id)
        return None

    monkeypatch.setattr(work_module, "_status_work", fake_status)

    result = CliRunner().invoke(work_cli, ["status", "missing"])

    assert result.exit_code != 0
    assert seen == ["missing"]
    assert "not found" in result.output.lower()


def test_work_resume_uses_persisted_lifecycle(monkeypatch) -> None:
    seen = []

    async def fake_resume(work_id: str):
        seen.append(work_id)
        return SimpleNamespace(work_id=work_id, status="WORK_BLOCKED")

    monkeypatch.setattr(work_module, "_resume_work", fake_resume)

    result = CliRunner().invoke(work_cli, ["resume", "work-1"])

    assert result.exit_code == 0, result.output
    assert seen == ["work-1"]
    assert "WORK_BLOCKED" in result.output


def test_work_approve_advances_the_named_canonical_gate(monkeypatch) -> None:
    seen = []

    async def fake_approve(work_id: str, gate_id: str):
        seen.append((work_id, gate_id))
        return SimpleNamespace(work_id=work_id, status="READY_TO_DELIVER")

    monkeypatch.setattr(work_module, "_approve_work", fake_approve)

    result = CliRunner().invoke(
        work_cli,
        ["approve", "work-1", "merge:work-1:42"],
    )

    assert result.exit_code == 0, result.output
    assert seen == [("work-1", "merge:work-1:42")]
    assert "READY_TO_DELIVER" in result.output


def test_work_pending_lists_canonical_attention(monkeypatch) -> None:
    async def fake_pending():
        return (
            SimpleNamespace(
                kind=SimpleNamespace(value="GATE_REQUESTED"),
                work_id="work-1",
                attention_id="merge:work-1:42",
                summary="Approve merge of PR #42.",
            ),
        )

    monkeypatch.setattr(work_module, "_pending_work", fake_pending)

    result = CliRunner().invoke(work_cli, ["pending"])

    assert result.exit_code == 0, result.output
    assert "GATE_REQUESTED" in result.output
    assert "work-1" in result.output
    assert "merge:work-1:42" in result.output
    assert "Approve merge of PR #42." in result.output
