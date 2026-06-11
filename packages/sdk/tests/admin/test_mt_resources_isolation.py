# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cross-tenant isolation for the four STEP-3 admin resources on the DURABLE store.

Mirrors :mod:`test_mt_budget_guardrail_isolation`: builds the full multi-tenant
admin app with an injected, seeded ``AdminResourceStore`` and the audit store
bound to the SAME engine (else a successful mutation fail-closes to 503 under
ASGITransport, which skips the lifespan). PB's rows are seeded THROUGH the store
into PB's scope, so a PA actor getting a 404/empty result is isolation (the row
genuinely exists in PB's scope), not absence.

Covers, for each of:

* saved workflows  (kind ``saved_workflow``,       /api/v1/workflow-registry)
* eval datasets    (kind ``eval_dataset``,         /api/v1/eval/datasets)
* connector triggers (kind ``connector_trigger``,  /api/v1/triggers)
* artifact destinations (kind ``artifact_destination``,
                         /api/v1/admin/workflows/{name}/artifact_destination)

the four invariants: PA's list/get does NOT see PB's row; PA cannot
update/delete PB's row (404/hidden); a project:viewer is denied writes (403);
and PA can CRUD its OWN row end-to-end.
"""

import httpx
import pytest_asyncio
from httpx import ASGITransport

from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine


@pytest_asyncio.fixture
async def mt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path / 'id.db'}")
    store = IdentityStore(engine=engine)
    await store.init()
    oid = (await store.bootstrap_org("Acme", "acme"))["id"]
    pa = (await store.create_project(oid, "pa", "PA"))["id"]
    pb = (await store.create_project(oid, "pb", "PB"))["id"]
    member = await store.create_user(oid, "m@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member["id"], "project:member", project_id=pa)
    viewer = await store.create_user(oid, "v@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, viewer["id"], "project:viewer", project_id=pa)
    # A PB member, so a PB-scoped session can confirm its seeded rows are reachable
    # through the wired store (proving the seed landed where the routes read) and
    # survive a cross-project PA write attempt.
    member_b = await store.create_user(oid, "mb@acme.io", password="pw0000", role="org:member")
    await store.add_membership(oid, member_b["id"], "project:member", project_id=pb)

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Acme", admin_email="a@acme.io", admin_password="pw123456")

    res = AdminResourceStore(engine=engine)
    await res.init()

    def _ctx(project_id):
        return RequestContext(
            actor=UserRef("seed", "seed"),
            org_id=oid,
            project_id=project_id,
            roles=frozenset({"project:admin"}),
            scopes=frozenset({"read", "write", "admin"}),
            request_id="seed",
            tenancy_mode="multi",
        )

    # Seed PB's rows THROUGH the store so they live in PB's scope.
    await res.upsert_for(
        _ctx(pb),
        "saved_workflow",
        "wf-pb",
        {"id": "wf-pb", "name": "pb-flow", "yaml_content": "x", "project_id": pb},
    )
    await res.upsert_for(
        _ctx(pb),
        "eval_dataset",
        "ds-pb",
        {"id": "ds-pb", "name": "pb-set", "project_id": pb},
    )
    await res.upsert_for(
        _ctx(pb),
        "connector_trigger",
        "trig-pb",
        {"id": "trig-pb", "kind": "cron", "project_id": pb},
    )
    await res.upsert_for(
        _ctx(pb),
        "artifact_destination",
        "wf-pb",
        {
            "workflow_name": "wf-pb",
            "project_id": pb,
            "destination": {"type": "s3", "target": "pb-bucket/out", "env_keys": []},
        },
        name="wf-pb",
    )

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(
        sf,
        identity_store=store,
        admin_resource_store=res,
    )
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "pa": pa,
        "pb": pb,
        "sess_member": await store.issue_session(oid, member["id"]),
        "sess_viewer": await store.issue_session(oid, viewer["id"]),
        "sess_member_b": await store.issue_session(oid, member_b["id"]),
    }
    await engine.dispose()


async def _req(app, method, path, *, token, project=None, json=None):
    headers = {"authorization": f"Bearer {token}"}
    if project:
        headers["x-project-id"] = project
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


# ── Saved workflows (kind: saved_workflow) ───────────────────────────


async def test_workflow_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json()["items"] == []
    assert r.json()["total"] == 0


async def test_workflow_pb_sees_its_own_seeded_row(mt_app):
    # Discriminator: PB's row was seeded THROUGH the store; a PB-scoped read must
    # surface it (the file store the unwired route reads is empty for PB).
    r = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry/wf-pb",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    assert r.json()["name"] == "pb-flow"


async def test_workflow_get_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry/wf-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_get_by_name_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry/by-name/pb-flow",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_delete_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/workflow-registry/wf-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_workflow_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/workflow-registry",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"name": "mine", "yaml_content": "steps: []"},
    )
    assert r.status_code == 403


async def test_workflow_own_crud_end_to_end(mt_app):
    created = await _req(
        mt_app["app"], "POST", "/api/v1/workflow-registry",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"name": "mine", "yaml_content": "steps: []", "project_id": mt_app["pb"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == mt_app["pa"]
    wf_id = created.json()["id"]

    listed = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {w["id"] for w in listed.json()["items"]} == {wf_id}
    assert listed.json()["total"] == 1

    got = await _req(
        mt_app["app"], "GET", f"/api/v1/workflow-registry/{wf_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert got.status_code == 200
    assert got.json()["name"] == "mine"

    by_name = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry/by-name/mine",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert by_name.status_code == 200
    assert by_name.json()["id"] == wf_id

    d = await _req(
        mt_app["app"], "DELETE", f"/api/v1/workflow-registry/{wf_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/workflow-registry",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json()["items"] == []


# ── Eval datasets (kind: eval_dataset) ───────────────────────────────


async def test_eval_dataset_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/eval/datasets",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_eval_dataset_pb_sees_its_own_seeded_row(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/eval/datasets/ds-pb",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    assert r.json()["name"] == "pb-set"


async def test_eval_dataset_get_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/eval/datasets/ds-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_eval_dataset_delete_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/eval/datasets/ds-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_eval_dataset_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/eval/datasets",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"name": "mine"},
    )
    assert r.status_code == 403


async def test_eval_dataset_own_crud_end_to_end(mt_app):
    created = await _req(
        mt_app["app"], "POST", "/api/v1/eval/datasets",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"name": "mine", "project_id": mt_app["pb"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == mt_app["pa"]
    ds_id = created.json()["id"]

    listed = await _req(
        mt_app["app"], "GET", "/api/v1/eval/datasets",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {d["id"] for d in listed.json()} == {ds_id}

    got = await _req(
        mt_app["app"], "GET", f"/api/v1/eval/datasets/{ds_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert got.status_code == 200
    assert got.json()["name"] == "mine"

    d = await _req(
        mt_app["app"], "DELETE", f"/api/v1/eval/datasets/{ds_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/eval/datasets",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json() == []


# ── Connector triggers (kind: connector_trigger) ─────────────────────


async def test_trigger_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_trigger_pb_sees_its_own_seeded_row(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/triggers",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    assert {t["id"] for t in r.json()} == {"trig-pb"}


async def test_trigger_delete_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/triggers/trig-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_trigger_enable_cross_project_404(mt_app):
    r = await _req(
        mt_app["app"], "PATCH", "/api/v1/triggers/trig-pb/enable",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_trigger_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/triggers",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"kind": "cron"},
    )
    assert r.status_code == 403


async def test_trigger_own_crud_end_to_end(mt_app):
    created = await _req(
        mt_app["app"], "POST", "/api/v1/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"kind": "cron", "project_id": mt_app["pb"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == mt_app["pa"]
    trig_id = created.json()["id"]

    listed = await _req(
        mt_app["app"], "GET", "/api/v1/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {t["id"] for t in listed.json()} == {trig_id}

    en = await _req(
        mt_app["app"], "PATCH", f"/api/v1/triggers/{trig_id}/enable",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert en.status_code == 200
    assert en.json()["enabled"] is True

    dis = await _req(
        mt_app["app"], "PATCH", f"/api/v1/triggers/{trig_id}/disable",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert dis.status_code == 200
    assert dis.json()["enabled"] is False

    d = await _req(
        mt_app["app"], "DELETE", f"/api/v1/triggers/{trig_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json() == []


# ── Artifact destinations (kind: artifact_destination) ───────────────

_DEST = {"type": "s3", "target": "pa-bucket/out", "env_keys": ["AWS_KEY"]}


async def test_artifact_dest_get_cross_project_404(mt_app):
    # PB's destination exists in PB's scope; PA must not see it.
    r = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/wf-pb/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404


async def test_artifact_dest_pb_sees_its_own_seeded_row(mt_app):
    # Discriminator: the seed went THROUGH the store, so a PB-scoped read must
    # surface it — proving the route reads the wired store, not the file path.
    r = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/wf-pb/artifact_destination",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    assert r.json()["target"] == "pb-bucket/out"


async def test_artifact_dest_delete_cross_project_does_not_remove_pb(mt_app):
    # A PA delete of PB's destination must not destroy PB's row (write-scope miss).
    await _req(
        mt_app["app"], "DELETE", "/api/v1/admin/workflows/wf-pb/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    # PB still sees its own destination (PA's delete was a no-op cross-scope).
    pb_get = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/wf-pb/artifact_destination",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert pb_get.status_code == 200
    assert pb_get.json()["target"] == "pb-bucket/out"


async def test_artifact_dest_viewer_cannot_write_403(mt_app):
    r = await _req(
        mt_app["app"], "PUT", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json=_DEST,
    )
    assert r.status_code == 403


async def test_artifact_dest_own_crud_end_to_end(mt_app):
    # Unset -> 404.
    miss = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert miss.status_code == 404

    # Set.
    put = await _req(
        mt_app["app"], "PUT", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json=_DEST,
    )
    assert put.status_code == 200
    assert put.json()["target"] == "pa-bucket/out"

    # Read back.
    got = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert got.status_code == 200
    assert got.json()["target"] == "pa-bucket/out"

    # Clear -> back to 404.
    d = await _req(
        mt_app["app"], "DELETE", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 204
    gone = await _req(
        mt_app["app"], "GET", "/api/v1/admin/workflows/mine/artifact_destination",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert gone.status_code == 404
