# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sealed CLI commands."""
import json

from click.testing import CliRunner

from sagewai.cli.sealed import sealed_group


def test_status_no_key(monkeypatch, tmp_path):
    monkeypatch.delenv("SAGEWAI_MASTER_KEY", raising=False)
    monkeypatch.setattr("sagewai.sealed.master_key.keyring", None)
    monkeypatch.setattr(
        "sagewai.sealed.master_key.DEFAULT_KEY_PATH",
        tmp_path / "nope.key",
    )
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(tmp_path / "admin-state.json"))
    (tmp_path / "admin-state.json").write_text(json.dumps({}))

    runner = CliRunner()
    result = runner.invoke(sealed_group, ["status"])
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["master_key_configured"] is False
    assert output["master_key_source"] == "none"
