# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""`/api/v1/health/detailed` must report REAL component status, not a hardcoded
'healthy' with an empty services list (which claimed health while checking
nothing). It is a PUBLIC endpoint, so the checks are cheap + in-process only —
no live DB probe (DoS) and no leaked error detail; a deep authenticated
readiness probe is a separate follow-up."""

import httpx
import pytest
from httpx import ASGITransport

from sagewai.admin.serve import create_admin_serve_app
from sagewai.admin.state_file import AdminStateFile


def _app(tmp_path):
    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")
    return create_admin_serve_app(sf), sf


async def _get(app, path):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.get(path)


@pytest.mark.asyncio
async def test_health_detailed_reports_real_services(tmp_path):
    app, _sf = _app(tmp_path)
    r = await _get(app, "/api/v1/health/detailed")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "healthy"
    services = {s["name"]: s for s in body["services"]}
    assert services, "services must not be empty — it claimed health while checking nothing"
    assert services["state_file"]["status"] == "ok"
    assert services["database"]["status"] in ("configured", "not_configured")
    assert services["tenancy"]["status"] in ("single", "multi")
    # public endpoint must not leak internal paths/secrets in its body
    assert "password" not in r.text.lower()
