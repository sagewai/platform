# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Cross-tenant isolation + secret-handling for notification channels/triggers.

Mirrors :mod:`test_mt_resources_isolation`: builds the full multi-tenant admin
app with an injected, seeded ``AdminResourceStore`` and the audit store bound to
the SAME engine (else a successful mutation fail-closes to 503 under
ASGITransport, which skips the lifespan). The tenant master key is pinned to ONE
fixed value for the whole test (``tenant_keys._master_key_source``) so the
encrypt-on-write / decrypt-on-use round-trip is stable.

Proves the STEP-4 security end state for notifications in MULTI mode:

* channels/triggers are durable + project-isolated (PA cannot list/get/delete
  PB's channel; a PB seed is reachable by PB → isolation, not absence);
* a channel's secret is REDACTED on every list/get (raw value never in body);
* the secret is stored ENCRYPTED (the store's ``data`` never holds plaintext);
* a body-supplied secret can't overwrite another project's channel
  (cross-project write → 404/empty, original secret intact);
* a project:viewer is denied writes (403);
* if the tenant key is unavailable at decrypt time, the /test use-path FAILS
  CLOSED (error, never plaintext/ciphertext passthrough).
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport

from sagewai.admin import tenant_keys
from sagewai.admin.admin_resource_store import AdminResourceStore
from sagewai.admin.identity_store import IdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.admin.tenancy import RequestContext, UserRef
from sagewai.admin.tenant_audit import TenantAuditStore
from sagewai.db.engine import create_engine

# One fixed master key for the whole test — a fresh key per call would break the
# encrypt-then-decrypt round-trip the secret invariants depend on.
_FIXED_KEY = Fernet.generate_key()

_PB_WEBHOOK = "https://hooks.slack.com/services/PB/SECRET/aaaaaaaaaaaaaaaaaaaa"


@pytest_asyncio.fixture
async def mt_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SAGEWAI_TENANCY_MODE", "multi")
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))
    monkeypatch.setattr(tenant_keys, "_master_key_source", lambda: (_FIXED_KEY, "test"))

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

    # Seed PB's slack channel THROUGH the store with its secret ENCRYPTED under
    # PB's data key, so a PA actor's 404/empty is isolation (the row truly lives
    # in PB's scope) and a cross-project overwrite can be proven a no-op.
    enc = await tenant_keys.encrypt_for_project(store, oid, pb, _PB_WEBHOOK)
    await res.upsert_for(
        _ctx(pb),
        "notification_channel",
        "ch-pb",
        {
            "id": "ch-pb",
            "channel_type": "slack",
            "webhook_url": enc,
            "project_id": pb,
        },
    )
    await res.upsert_for(
        _ctx(pb),
        "notification_trigger",
        "tr-pb",
        {"id": "tr-pb", "trigger": "budget_warning", "project_id": pb},
    )

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf, identity_store=store, admin_resource_store=res)
    audit = TenantAuditStore(engine=engine)
    await audit.init()
    app.state.tenant_audit = audit
    yield {
        "app": app,
        "store": res,
        "identity": store,
        "oid": oid,
        "pa": pa,
        "pb": pb,
        "ctx_pa": _ctx(pa),
        "ctx_pb": _ctx(pb),
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


# ── Isolation (channels) ─────────────────────────────────────────────


async def test_channel_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_channel_pb_sees_its_own_seeded_row(mt_app):
    # PB's channel was seeded THROUGH the store; a PB-scoped read must surface it
    # (proving the route reads the wired store, not the empty file path for PB).
    r = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert ids == {"ch-pb"}


async def test_channel_delete_cross_project_is_noop(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/notifications/channels/ch-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404
    # PB still has its channel — PA's delete was a cross-scope no-op.
    pb = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert {c["id"] for c in pb.json()} == {"ch-pb"}


async def test_channel_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"channel_type": "slack", "webhook_url": "https://x"},
    )
    assert r.status_code == 403


# ── Secret handling (redaction / encryption / fail-closed) ───────────


async def test_secret_redacted_on_pb_read(mt_app):
    # PB reads its own channel — the raw webhook must NEVER appear; only a marker.
    r = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    body = r.text
    assert _PB_WEBHOOK not in body
    assert "fernet:" not in body  # never leak ciphertext either
    ch = r.json()[0]
    assert ch["webhook_url"] == "***"
    assert ch.get("has_webhook_url") is True


async def test_created_secret_is_redacted_and_stored_encrypted(mt_app):
    secret = "https://hooks.slack.com/services/PA/T0/zzzzzzzzzzzzzzzzzzzz"
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"channel_type": "slack", "webhook_url": secret},
    )
    assert created.status_code == 201
    # Response is redacted — plaintext never echoed back.
    assert secret not in created.text
    assert created.json()["webhook_url"] == "***"
    ch_id = created.json()["id"]

    # Stored data is ENCRYPTED — the plaintext is not in the store row.
    stored = await mt_app["store"].get_for(
        mt_app["ctx_pa"], "notification_channel", ch_id
    )
    assert stored is not None
    assert stored["webhook_url"] != secret
    assert stored["webhook_url"].startswith("fernet:")
    # And it round-trips back to the plaintext under PA's key.
    rt = await tenant_keys.decrypt_for_project(
        mt_app["identity"], mt_app["oid"], mt_app["pa"], stored["webhook_url"]
    )
    assert rt == secret

    # List read also redacts.
    listed = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert secret not in listed.text
    assert "fernet:" not in listed.text


async def test_redaction_placeholder_preserves_existing_secret(mt_app):
    secret = "https://hooks.slack.com/services/PA/T1/keepkeepkeepkeepkeep"
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"channel_type": "slack", "webhook_url": secret},
    )
    ch_id = created.json()["id"]

    # Re-save with the redaction marker (what a UI round-trip sends back).
    await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"id": ch_id, "channel_type": "slack", "webhook_url": "***"},
    )
    stored = await mt_app["store"].get_for(
        mt_app["ctx_pa"], "notification_channel", ch_id
    )
    # The marker did NOT clobber the real (encrypted) secret.
    rt = await tenant_keys.decrypt_for_project(
        mt_app["identity"], mt_app["oid"], mt_app["pa"], stored["webhook_url"]
    )
    assert rt == secret


async def test_cross_project_body_secret_cannot_overwrite(mt_app):
    # PA posts PB's channel id with its own secret — must NOT overwrite PB's
    # channel (different scope) and PB's original secret stays intact.
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"id": "ch-pb", "channel_type": "slack", "webhook_url": "https://evil"},
    )
    # PA either creates its OWN ch-pb row (own scope) or is rejected — but PB's
    # row must be untouched.
    assert created.status_code in (201, 403, 404)
    pb_stored = await mt_app["store"].get_for(
        mt_app["ctx_pb"], "notification_channel", "ch-pb"
    )
    assert pb_stored is not None
    rt = await tenant_keys.decrypt_for_project(
        mt_app["identity"], mt_app["oid"], mt_app["pb"], pb_stored["webhook_url"]
    )
    assert rt == _PB_WEBHOOK


async def test_test_path_fails_closed_without_key(mt_app, monkeypatch):
    # Create a channel (encrypted under the real key), then make the key
    # unavailable. The /test use-path must FAIL CLOSED — never send the stored
    # ciphertext or fall back to plaintext.
    secret = "https://hooks.slack.com/services/PA/T2/failclosedfailclosed"
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"channel_type": "slack", "webhook_url": secret},
    )
    assert created.status_code == 201

    # Swap in a DIFFERENT master key — the project data key can no longer be
    # unwrapped, so decrypt must raise (SecretCorrupted) rather than pass through.
    monkeypatch.setattr(
        tenant_keys, "_master_key_source", lambda: (Fernet.generate_key(), "wrong")
    )
    r = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/test",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"channel_type": "slack"},
    )
    body = r.text
    assert secret not in body  # never the plaintext
    assert "fernet:" not in body  # never the ciphertext
    payload = r.json()
    assert payload.get("sent") is False
    assert payload.get("error")


# ── Isolation (triggers) ─────────────────────────────────────────────


async def test_trigger_list_excludes_cross_project(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 200
    assert r.json() == []


async def test_trigger_pb_sees_its_own_seeded_row(mt_app):
    r = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/triggers",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert r.status_code == 200
    assert {t["id"] for t in r.json()} == {"tr-pb"}


async def test_trigger_delete_cross_project_is_noop(mt_app):
    r = await _req(
        mt_app["app"], "DELETE", "/api/v1/notifications/triggers/tr-pb",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert r.status_code == 404
    pb = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/triggers",
        token=mt_app["sess_member_b"], project=mt_app["pb"],
    )
    assert {t["id"] for t in pb.json()} == {"tr-pb"}


async def test_trigger_viewer_cannot_create_403(mt_app):
    r = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/triggers",
        token=mt_app["sess_viewer"], project=mt_app["pa"],
        json={"trigger": "budget_warning"},
    )
    assert r.status_code == 403


async def test_trigger_own_crud_end_to_end(mt_app):
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"trigger": "workflow_failed", "project_id": mt_app["pb"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == mt_app["pa"]
    tr_id = created.json()["id"]

    listed = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {t["id"] for t in listed.json()} == {tr_id}

    d = await _req(
        mt_app["app"], "DELETE", f"/api/v1/notifications/triggers/{tr_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/triggers",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json() == []


async def test_channel_own_crud_end_to_end(mt_app):
    secret = "https://hooks.slack.com/services/PA/T3/owncrudowncrudowncrud"
    created = await _req(
        mt_app["app"], "POST", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
        json={"channel_type": "slack", "webhook_url": secret, "project_id": mt_app["pb"]},
    )
    assert created.status_code == 201
    assert created.json()["project_id"] == mt_app["pa"]
    ch_id = created.json()["id"]

    listed = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert {c["id"] for c in listed.json()} == {ch_id}

    d = await _req(
        mt_app["app"], "DELETE", f"/api/v1/notifications/channels/{ch_id}",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert d.status_code == 200
    after = await _req(
        mt_app["app"], "GET", "/api/v1/notifications/channels",
        token=mt_app["sess_member"], project=mt_app["pa"],
    )
    assert after.json() == []


# ── Single-org file path is UNCHANGED ────────────────────────────────


@pytest_asyncio.fixture
async def single_app(tmp_path, monkeypatch):
    """A single-org admin app (no tenancy) with NO resource store wired — the
    notification routes must keep their file-backed (``sf``) path verbatim."""
    monkeypatch.delenv("SAGEWAI_TENANCY_MODE", raising=False)
    monkeypatch.setenv("SAGEWAI_HOME", str(tmp_path))

    sf = AdminStateFile(path=tmp_path / "state.json")
    sf.complete_setup(org_name="Solo", admin_email="a@solo.io", admin_password="pw123456")

    from sagewai.admin.serve import create_admin_serve_app

    app = create_admin_serve_app(sf)
    token = sf.validate_login("a@solo.io", "pw123456")["access_token"]
    yield {"app": app, "sf": sf, "token": token}


async def _single_req(app, method, path, *, token, json=None):
    headers = {"authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.request(method, path, headers=headers, json=json)


async def test_single_org_channel_crud_uses_file_store_and_redacts(single_app):
    secret = "https://hooks.slack.com/services/SOLO/T0/singleorgsingleorg"
    created = await _single_req(
        single_app["app"], "POST", "/api/v1/notifications/channels",
        token=single_app["token"],
        json={"channel_type": "slack", "webhook_url": secret},
    )
    assert created.status_code == 201
    # Redacted in the response (unchanged behaviour) ...
    assert created.json()["webhook_url"] == "***"
    assert secret not in created.text
    ch_id = created.json()["id"]

    # ... but stored VERBATIM (plaintext) in the file store — byte-identical to
    # the pre-change single-org path (no tenant-key encryption when no key model).
    stored = single_app["sf"]._read()["notification_channels"]
    assert any(c["id"] == ch_id and c["webhook_url"] == secret for c in stored)

    listed = await _single_req(
        single_app["app"], "GET", "/api/v1/notifications/channels",
        token=single_app["token"],
    )
    assert listed.status_code == 200
    assert secret not in listed.text
    assert {c["id"] for c in listed.json()} == {ch_id}

    d = await _single_req(
        single_app["app"], "DELETE", f"/api/v1/notifications/channels/{ch_id}",
        token=single_app["token"],
    )
    assert d.status_code == 200


async def test_single_org_test_path_reads_file_store_plaintext(single_app):
    # A single-org /test reads the file-store channel and resolves its plaintext
    # secret (decrypt is a no-op on non-fernet values) — the use-path is unchanged.
    await _single_req(
        single_app["app"], "POST", "/api/v1/notifications/channels",
        token=single_app["token"],
        json={"channel_type": "slack", "webhook_url": "https://hooks.slack.com/services/no-net"},
    )
    r = await _single_req(
        single_app["app"], "POST", "/api/v1/notifications/test",
        token=single_app["token"],
        json={"channel_type": "slack"},
    )
    # No network in tests, so the POST raises and we get a clean error dict — the
    # point is it did NOT short-circuit on "no webhook configured" (it FOUND the
    # plaintext webhook in the file store) and did NOT fail-closed on a key.
    assert r.status_code == 200
    payload = r.json()
    assert "No Slack webhook URL configured" not in str(payload.get("error", ""))


@pytest.mark.asyncio
async def test_placeholder_module_marker():
    # Keeps collection stable if all async fixtures are skipped in some envs.
    assert True
