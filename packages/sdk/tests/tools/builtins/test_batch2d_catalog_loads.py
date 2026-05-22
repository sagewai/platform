# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2d catalog change is wired correctly."""
from sagewai.tools import registry


BATCH_2D_IDS = {
    "amplitude_api", "opsgenie_api", "datadog_api", "virustotal_api",
    "snyk_api", "jira_api", "confluence_api", "compass_api",
}


def test_all_batch2d_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2D_IDS - ids
    assert not missing, f"missing batch-2d entries in api_key tier: {missing}"


def test_all_batch2d_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2D_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"


def test_batch2d_sdk_entrypoints_resolve():
    from sagewai.tools.executors.sdk import _resolve
    registry._reset()
    registry.load()
    for tid in ("amplitude_api", "datadog_api", "compass_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "sdk", f"{tid} must be kind: sdk; got {entry.kind}"
        assert callable(_resolve(entry.exec_["sdk"]["entrypoint"]))


def test_batch2d_http_tools_have_correct_kind():
    registry._reset()
    registry.load()
    for tid in ("opsgenie_api", "virustotal_api", "snyk_api", "jira_api", "confluence_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "http", f"{tid} must be kind: http; got {entry.kind}"


def test_jira_confluence_declare_runtime_base_url_field():
    registry._reset()
    registry.load()
    assert registry.lookup("jira_api").exec_["http"]["runtime_base_url_field"] == "JIRA_SITE"
    assert registry.lookup("confluence_api").exec_["http"]["runtime_base_url_field"] == "CONFLUENCE_SITE"


def test_adyen_has_runtime_base_url_field_now():
    """Adyen (modified in batch 2d) now declares runtime_base_url_field."""
    registry._reset()
    registry.load()
    assert registry.lookup("adyen_api").exec_["http"]["runtime_base_url_field"] == "ADYEN_BASE_URL"


def test_opsgenie_uses_geniekey_prefix():
    registry._reset()
    registry.load()
    auth = registry.lookup("opsgenie_api").exec_["http"]["auth"]
    assert auth["prefix"] == "GenieKey "


def test_snyk_uses_token_prefix():
    registry._reset()
    registry.load()
    auth = registry.lookup("snyk_api").exec_["http"]["auth"]
    assert auth["prefix"] == "Token "
