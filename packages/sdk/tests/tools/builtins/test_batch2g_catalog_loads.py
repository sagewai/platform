# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Smoke test that all seven batch-2g entries register cleanly."""
import importlib

from sagewai.tools import registry


_BATCH_2G = {
    "terra_api",
    "vital_api",
    "nutritionix_api",
    "usda_fdc_api",
    "openfda_api",
    "rxnorm_api",
    "infermedica_api",
}

_SDK_TOOLS = {"terra_api", "nutritionix_api", "usda_fdc_api", "openfda_api", "infermedica_api"}
_HTTP_TOOLS = {"vital_api", "rxnorm_api"}


def _load_all() -> dict[str, registry.CatalogEntry]:
    registry._reset()
    registry.load()
    return {tid: registry.lookup(tid) for tid in _BATCH_2G}


def test_all_seven_registered():
    registry._reset()
    registry.load()
    missing = _BATCH_2G - registry._entries.keys()
    assert not missing, f"missing catalog entries: {missing}"


def test_all_declare_untrusted_tier():
    entries = _load_all()
    for tid in _BATCH_2G:
        assert entries[tid].sandbox_tier == "UNTRUSTED", (
            f"{tid} expected UNTRUSTED, got {entries[tid].sandbox_tier}"
        )


def test_all_carry_health_read_scope():
    entries = _load_all()
    for tid in _BATCH_2G:
        assert "health.read" in entries[tid].scopes, (
            f"{tid} missing health.read scope"
        )


def test_infermedica_carries_medical_advisory():
    entries = _load_all()
    assert "medical.advisory" in entries["infermedica_api"].scopes


def test_kind_split_matches_design():
    entries = _load_all()
    for tid in _SDK_TOOLS:
        assert entries[tid].kind == "sdk", f"{tid} expected kind: sdk"
    for tid in _HTTP_TOOLS:
        assert entries[tid].kind == "http", f"{tid} expected kind: http"


def test_sdk_entrypoints_resolve():
    entries = _load_all()
    for tid in _SDK_TOOLS:
        ep = entries[tid].exec_["sdk"]["entrypoint"]
        module_path, _, fn = ep.partition(":")
        mod = importlib.import_module(module_path)
        assert callable(getattr(mod, fn)), f"{tid} entrypoint {ep} not callable"


def test_rxnorm_has_no_credential_fields():
    """rxnorm_api is a public NIH service — no credentials. The setup dict
    omits 'credential_fields' entirely; required_credentials() returns []."""
    registry._reset()
    registry.load()
    entry = registry.lookup("rxnorm_api")
    assert "credential_fields" not in entry.setup, (
        "rxnorm_api should declare no credential_fields key in setup"
    )


def test_openfda_has_exactly_one_credential_field_named_openfda_api_key():
    """openfda_api has a single optional credential field OPENFDA_API_KEY.
    Optionality is described in prose only (schema has no required: false)."""
    registry._reset()
    registry.load()
    fields = registry.required_credentials("openfda_api")
    assert len(fields) == 1, f"openfda_api expected 1 credential field, got {len(fields)}"
    assert fields[0]["name"] == "OPENFDA_API_KEY", (
        f"openfda_api credential field name expected OPENFDA_API_KEY, got {fields[0]['name']}"
    )
