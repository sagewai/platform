# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Verify the v1→v2 back-fill that adds ``kind: inference`` on connections."""
import json

from sagewai.admin.state_file import AdminStateFile


def test_existing_providers_get_kind_inference_backfilled(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "providers": [
            {"id": "p1", "provider_name": "runpod", "data": {"RUNPOD_API_KEY": "rp_x"}},
            {"id": "p2", "provider_name": "modal", "data": {"MODAL_TOKEN_ID": "ak-x"}},
        ],
    }))
    sf = AdminStateFile(p)
    providers = sf.list_providers()
    assert providers, "expected providers list to round-trip"
    assert all(rec.get("kind") == "inference" for rec in providers)


def test_new_tool_kind_record_round_trips(tmp_path):
    p = tmp_path / "state.json"
    sf = AdminStateFile(p)
    sf.upsert_provider({
        "id": "g1",
        "provider_name": "github",
        "kind": "tool",
        "data": {"GITHUB_TOKEN": "ghp_x"},
    })
    providers = sf.list_providers()
    assert any(
        rec["kind"] == "tool" and rec["provider_name"] == "github"
        for rec in providers
    )


def test_backfill_does_not_clobber_existing_kind(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({
        "providers": [
            {"id": "g1", "provider_name": "github", "kind": "tool",
             "data": {"GITHUB_TOKEN": "x"}},
        ],
    }))
    sf = AdminStateFile(p)
    providers = sf.list_providers()
    assert providers[0]["kind"] == "tool"
