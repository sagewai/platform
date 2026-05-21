# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Assert every batch-2c catalog change is wired correctly."""
from sagewai.tools import registry


BATCH_2C_IDS = {"stripe_api", "adyen_api", "plaid_api", "braintree_api", "paypal_api"}


def test_all_batch2c_entries_in_api_key_tier():
    registry._reset()
    registry.load()
    ids = {e.id for e in registry.list_by_tier("api_key")}
    missing = BATCH_2C_IDS - ids
    assert not missing, f"missing batch-2c entries in api_key tier: {missing}"


def test_all_batch2c_declare_credential_fields():
    registry._reset()
    registry.load()
    for tid in BATCH_2C_IDS:
        creds = registry.required_credentials(tid)
        assert creds, f"{tid} must declare credential_fields"


def test_batch2c_entrypoints_resolve_for_sdk_tools():
    from sagewai.tools.executors.sdk import _resolve
    registry._reset()
    registry.load()
    for tid in ("plaid_api", "braintree_api", "paypal_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "sdk", f"{tid} must be kind: sdk; got {entry.kind}"
        assert callable(_resolve(entry.exec_["sdk"]["entrypoint"]))


def test_batch2c_http_tools_have_correct_kind():
    registry._reset()
    registry.load()
    for tid in ("stripe_api", "adyen_api"):
        entry = registry.lookup(tid)
        assert entry.kind == "http", f"{tid} must be kind: http; got {entry.kind}"


def test_payments_charge_scope_on_all_batch2c():
    registry._reset()
    registry.load()
    for tid in BATCH_2C_IDS:
        scopes = registry.scopes_for(tid)
        assert "payments.charge" in scopes, f"{tid} missing payments.charge scope; got {sorted(scopes)}"


def test_stripe_has_form_encoded_ops():
    registry._reset()
    registry.load()
    entry = registry.lookup("stripe_api")
    ops = entry.exec_["http"]["operations"]
    for op_name in ("create_payment_intent", "create_customer", "create_refund"):
        assert ops[op_name].get("body_format") == "form", f"{op_name} should use body_format=form"
