# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


def test_injected_provider_store_is_on_app_state(tmp_path):
    sf = AdminStateFile(tmp_path / "s.json")

    class _Stub: ...

    stub = _Stub()
    app = create_admin_serve_app(sf, provider_store=stub)
    assert app.state.resource_stores.provider is stub


def test_single_org_builds_no_active_provider_store(tmp_path, monkeypatch):
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    sf = AdminStateFile(tmp_path / "s.json")
    app = create_admin_serve_app(sf)
    rs = getattr(app.state, "resource_stores", None)
    assert rs is None or rs.provider is None
