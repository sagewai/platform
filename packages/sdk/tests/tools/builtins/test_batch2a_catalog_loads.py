# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2a catalog entry loads and its entrypoint resolves."""
from sagewai.tools import registry


BATCH_2A_IDS = {"post_to_slack", "discord_api", "email_send", "mailchimp_api"}


def test_all_batch2a_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2A_IDS - ids
    assert not missing, f"missing batch-2a entries in api_key tier: {missing}"


def test_all_batch2a_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2A_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"
        for field in creds:
            assert "name" in field
            assert "label" in field
            assert field["type"] in ("password", "text")


def test_batch2a_entrypoints_resolve():
    from sagewai.tools.executors.sdk import _resolve
    registry._reset()
    registry.load()
    # Only sdk-kind tools have entrypoints to resolve via the sdk executor's helper.
    sdk_tools = {"email_send", "mailchimp_api"}
    for tid in sdk_tools:
        entry = registry.lookup(tid)
        assert entry.kind == "sdk"
        assert callable(_resolve(entry.exec_["sdk"]["entrypoint"]))
    # http tools still must load with the right kind
    for tid in {"post_to_slack", "discord_api"}:
        entry = registry.lookup(tid)
        assert entry.kind == "http"
