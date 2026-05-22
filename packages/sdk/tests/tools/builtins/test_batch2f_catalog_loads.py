# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2f catalog entry is wired correctly."""
from sagewai.tools import registry


BATCH_2F_IDS = {"duffel_api", "liteapi", "transitland_api", "marinetraffic_api"}


def test_all_batch2f_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2F_IDS - ids
    assert not missing, f"missing batch-2f entries in api_key tier: {missing}"


def test_all_batch2f_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2F_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"


def test_batch2f_sdk_entrypoints_resolve():
    from sagewai.tools.executors.sdk import _resolve
    registry._reset()
    registry.load()
    for tid in ("duffel_api", "marinetraffic_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "sdk", f"{tid} must be kind: sdk; got {entry.kind}"
        assert callable(_resolve(entry.exec_["sdk"]["entrypoint"]))


def test_batch2f_http_tools_have_correct_kind():
    registry._reset()
    registry.load()
    for tid in ("liteapi", "transitland_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "http", f"{tid} must be kind: http; got {entry.kind}"


def test_transitland_uses_apikey_header():
    registry._reset()
    registry.load()
    auth = registry.lookup("transitland_api").exec_["http"]["auth"]
    assert auth["kind"] == "api_key"
    assert auth["header"] == "apikey"


def test_liteapi_uses_x_api_key_header():
    registry._reset()
    registry.load()
    auth = registry.lookup("liteapi").exec_["http"]["auth"]
    assert auth["header"] == "X-API-Key"


def test_all_batch2f_carry_travel_search_scope():
    registry._reset()
    registry.load()
    for tid in BATCH_2F_IDS:
        assert "travel.search" in registry.lookup(tid).scopes, \
            f"{tid} must carry the travel.search scope"
