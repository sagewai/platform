# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""End-to-end: revoke during in-flight run → run aborts."""
import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("SAGEWAI_DATABASE_URL"),
    reason="SAGEWAI_DATABASE_URL not set",
)


@pytest.mark.asyncio
async def test_hard_revoke_aborts_in_flight_run(tmp_path, monkeypatch):
    """Full happy path: profile + run + hard revoke → worker abort path runs."""
    from cryptography.fernet import Fernet

    from sagewai.core.stores.postgres import PostgresStore
    from sagewai.core.worker import _check_run_revocation_and_abort
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
    from sagewai.sealed.crypto import Crypto
    from sagewai.sealed.models import ProfileWritePayload
    from sagewai.sealed.refs import _BACKENDS
    from sagewai.sealed.revocation import RevocationRegistry

    store = PostgresStore(database_url=os.environ["SAGEWAI_DATABASE_URL"])
    await store.initialize()
    try:
        # Set up isolated builtin backend with profiles.json under tmp_path
        crypto = Crypto(Fernet.generate_key())
        backend = BuiltinAdminStoreBackend(
            profiles_path=tmp_path / "profiles.json",
            crypto=crypto, audit_writer=None,
        )
        monkeypatch.setitem(_BACKENDS, "builtin", backend)
        await backend.save_profile(ProfileWritePayload(
            id="iii-acme", name="A",
            secrets={"K_E2E": "v"}, env={},
        ))

        # Insert a "running" workflow_run that already injected the key.
        # Use the schema conventions from Task 10/11: id is TEXT, output (not output_data),
        # ON CONFLICT (id) (not run_id). Match the pattern used in
        # tests/test_worker_revocation_abort.py.
        await store._pool.execute(
            """
            INSERT INTO workflow_runs
              (id, workflow_name, run_id, status, security_profile_ref,
               effective_env_keys, effective_secret_keys)
            VALUES ('wf-e2e:r-iii-1', 'wf-e2e', 'r-iii-1', 'running', 'iii-acme',
                    ARRAY['K_E2E'], ARRAY['K_E2E'])
            ON CONFLICT (id) DO UPDATE SET
              status = 'running',
              security_profile_ref = EXCLUDED.security_profile_ref,
              effective_env_keys = EXCLUDED.effective_env_keys,
              effective_secret_keys = EXCLUDED.effective_secret_keys,
              revoked_at = NULL,
              revoke_reason = NULL
            """,
        )

        # Hard revoke
        reg = RevocationRegistry(store, audit_writer=AuditWriter(store))
        [r] = await reg.revoke(
            profile_id="iii-acme", secret_key="K_E2E",
            reason="e2e breach", hard=True, actor_id="e2e-test",
        )
        assert r.hard is True

        # Confirm the run got marked
        row = await store._pool.fetchrow(
            "SELECT revoked_at, revoke_reason FROM workflow_runs WHERE run_id = 'r-iii-1'"
        )
        assert row["revoked_at"] is not None
        assert row["revoke_reason"] == "e2e breach"

        # Simulate the worker's between-step poll
        class _S:
            stopped = False
            async def stop(self):
                self.stopped = True

        sandbox = _S()
        aborted = await _check_run_revocation_and_abort(
            store=store, run_id="r-iii-1", sandbox=sandbox,
        )
        assert aborted is True
        assert sandbox.stopped is True

        # Confirm run is now failed
        row = await store._pool.fetchrow(
            "SELECT status FROM workflow_runs WHERE run_id = 'r-iii-1'"
        )
        assert row["status"] == "failed"

        # Audit events present
        types_in_audit = await store._pool.fetch(
            """SELECT event_type FROM sealed_audit_events
               WHERE run_id = 'r-iii-1' OR
                     (profile_id = 'iii-acme' AND secret_key = 'K_E2E')
               ORDER BY id ASC"""
        )
        observed = [t["event_type"] for t in types_in_audit]
        assert "secret.hard_revoked" in observed
        assert "run.aborted_by_revocation" in observed
    finally:
        await store._pool.execute("DELETE FROM workflow_runs WHERE run_id = 'r-iii-1'")
        await store._pool.execute(
            "DELETE FROM sealed_revocations WHERE profile_id = 'iii-acme'"
        )
        await store._pool.execute(
            "DELETE FROM sealed_audit_events WHERE profile_id = 'iii-acme' OR run_id = 'r-iii-1'"
        )
        await store.close()
