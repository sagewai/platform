# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for sagewai admin directives CLI commands."""
from __future__ import annotations

import json

from click.testing import CliRunner

from sagewai.cli.directives import directives_group


class _FakeResponse:
    def __init__(self, *, status_code: int = 200, payload: dict | None = None) -> None:
        self.status_code = status_code
        self._payload = payload or {}
        self.text = json.dumps(self._payload)

    def json(self) -> dict:
        return self._payload


def test_list_policies_calls_admin_api(monkeypatch):
    captured: dict = {}

    def fake_get(path, **kwargs):
        captured["path"] = path
        return _FakeResponse(
            payload={
                "system_policies": [],
                "project_policies": {},
                "workflow_policies": {},
                "profile_suggestions": {},
                "evaluator_settings": {},
            },
        )

    monkeypatch.setattr("sagewai.cli.directives._get", fake_get)
    runner = CliRunner()
    result = runner.invoke(directives_group, ["list-policies"])
    assert result.exit_code == 0, result.output
    assert "/api/v1/admin/directives/policies" in captured["path"]


def test_set_policy_from_file(tmp_path, monkeypatch):
    body = {
        "system_policies": [],
        "project_policies": {},
        "workflow_policies": {},
        "profile_suggestions": {},
        "evaluator_settings": {},
    }
    f = tmp_path / "policies.json"
    f.write_text(json.dumps(body))

    captured: dict = {}

    def fake_put(path, json_body, **kwargs):
        captured["path"] = path
        captured["body"] = json_body
        return _FakeResponse(payload={"ok": True})

    monkeypatch.setattr("sagewai.cli.directives._put", fake_put)
    result = CliRunner().invoke(directives_group, ["set-policy", "--from-file", str(f)])
    assert result.exit_code == 0, result.output
    assert captured["body"] == body


def test_approve_calls_post(monkeypatch):
    captured: dict = {}

    def fake_post(path, json_body, **kwargs):
        captured["path"] = path
        captured["body"] = json_body
        return _FakeResponse(payload={"status": "approved"})

    monkeypatch.setattr("sagewai.cli.directives._post", fake_post)
    result = CliRunner().invoke(
        directives_group, ["approve", "dec-1", "--actor", "ops", "--note", "ok"],
    )
    assert result.exit_code == 0, result.output
    assert captured["path"].endswith("/dec-1/approve")
    assert captured["body"] == {"actor": "ops", "note": "ok"}


def test_deny_propagates_server_error(monkeypatch):
    def fake_post(path, json_body, **kwargs):
        return _FakeResponse(status_code=409, payload={"error": "already_decided"})

    monkeypatch.setattr("sagewai.cli.directives._post", fake_post)
    result = CliRunner().invoke(directives_group, ["deny", "dec-1"])
    assert result.exit_code != 0
    assert "409" in result.output
