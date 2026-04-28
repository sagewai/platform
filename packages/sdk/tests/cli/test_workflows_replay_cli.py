# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later).
"""Sealed-iii.C — CLI: workflows replay + replay-status."""
from __future__ import annotations

from click.testing import CliRunner

from sagewai.cli.workflow import workflow as workflow_cli


def test_replay_command_yes_skips_preview_and_calls_commit(monkeypatch):
    seen: list[tuple[str, dict]] = []

    def fake_post(path, body=None):
        seen.append(("POST", path))
        if path.endswith("/preview"):
            return {"warnings": [], "blockers": []}
        return {"new_run_id": "new-1", "replay_of_run_id": "r-orig"}

    monkeypatch.setattr("sagewai.cli._api_post", fake_post)

    runner = CliRunner()
    result = runner.invoke(
        workflow_cli,
        ["replay", "r-orig", "--from-step", "2", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "new-1" in result.output
    # With --yes the preview is skipped, only one POST.
    assert len(seen) == 1
    assert seen[0][1].endswith("/r-orig/replay")


def test_replay_command_aborts_on_blockers(monkeypatch):
    def fake_post(path, body=None):
        if path.endswith("/preview"):
            return {
                "warnings": [],
                "blockers": [
                    {"type": "workflow_version_mismatch"}
                ],
            }
        raise AssertionError("commit should not be reached")

    monkeypatch.setattr("sagewai.cli._api_post", fake_post)
    runner = CliRunner()
    result = runner.invoke(
        workflow_cli,
        ["replay", "r-orig"],
    )
    assert result.exit_code != 0
    assert "Cannot replay" in result.output
    assert "workflow_version_mismatch" in result.output


def test_replay_command_warns_and_proceeds_after_confirm(monkeypatch):
    seen: list[str] = []

    def fake_post(path, body=None):
        seen.append(path)
        if path.endswith("/preview"):
            return {
                "warnings": [
                    {"type": "key_now_revoked", "secret_key": "OPENAI_API_KEY"}
                ],
                "blockers": [],
            }
        return {"new_run_id": "new-2", "replay_of_run_id": "r-orig"}

    monkeypatch.setattr("sagewai.cli._api_post", fake_post)
    runner = CliRunner()
    result = runner.invoke(
        workflow_cli,
        ["replay", "r-orig"],
        input="y\n",
    )
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" in result.output
    assert "new-2" in result.output
    assert any(p.endswith("/replay") for p in seen)


def test_replay_status_command_renders_table(monkeypatch):
    monkeypatch.setattr(
        "sagewai.cli._api_get",
        lambda path: {
            "replays": [
                {
                    "run_id": "r-replay-1234567890",
                    "replay_from_step": 0,
                    "status": "completed",
                    "started_at": 12345.0,
                }
            ]
        },
    )
    runner = CliRunner()
    result = runner.invoke(
        workflow_cli, ["replay-status", "r-orig"],
    )
    assert result.exit_code == 0, result.output
    assert "r-replay-1234567" in result.output
    assert "completed" in result.output


def test_replay_status_empty(monkeypatch):
    monkeypatch.setattr(
        "sagewai.cli._api_get",
        lambda path: {"replays": []},
    )
    runner = CliRunner()
    result = runner.invoke(workflow_cli, ["replay-status", "r-orig"])
    assert result.exit_code == 0
    assert "No replays" in result.output
