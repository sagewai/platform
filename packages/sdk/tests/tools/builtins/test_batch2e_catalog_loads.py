# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2e catalog entry is wired correctly."""
from sagewai.tools import registry


BATCH_2E_IDS = {"shopify", "magento", "joor_api"}


def test_all_batch2e_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2E_IDS - ids
    assert not missing, f"missing batch-2e entries in api_key tier: {missing}"


def test_all_batch2e_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2E_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"


def test_shopify_sdk_entrypoint_resolves():
    from sagewai.tools.executors.sdk import _resolve
    registry._reset()
    registry.load()
    entry = registry.lookup("shopify")
    assert entry.kind == "sdk", f"shopify must be kind: sdk; got {entry.kind}"
    assert callable(_resolve(entry.exec_["sdk"]["entrypoint"]))


def test_batch2e_http_tools_have_correct_kind():
    registry._reset()
    registry.load()
    for tid in ("magento", "joor_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "http", f"{tid} must be kind: http; got {entry.kind}"


def test_magento_declares_runtime_base_url_field():
    registry._reset()
    registry.load()
    assert registry.lookup("magento").exec_["http"]["runtime_base_url_field"] == "MAGENTO_BASE_URL"


def test_joor_has_no_runtime_base_url_field():
    """JOOR is a single SaaS — fixed base URL, no runtime override."""
    registry._reset()
    registry.load()
    assert "runtime_base_url_field" not in registry.lookup("joor_api").exec_["http"]


def test_all_batch2e_carry_ecommerce_write_scope():
    registry._reset()
    registry.load()
    for tid in BATCH_2E_IDS:
        assert "ecommerce.write" in registry.lookup(tid).scopes, \
            f"{tid} must carry the ecommerce.write scope"
