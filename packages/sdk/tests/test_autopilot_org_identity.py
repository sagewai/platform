# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""The autopilot instance identity is org-derived, idempotent, and un-farmable.

The blueprint service keys fair, per-organization quota off the instance id, so
the id must be ONE per org and impossible to multiply or reset:

* deterministic — derived from the org's immutable setup identity, not random;
* idempotent — the same org always yields the same id;
* reset-proof — clearing the stored identity re-derives the SAME id (you cannot
  mint a fresh identity, and thus fresh quota, by deleting the identity file);
* org-unique — different orgs get different ids.
"""

from __future__ import annotations

from sagewai.admin.autopilot_state import AdminStateIdentityStore
from sagewai.admin.state_file import AdminStateFile
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity


def _store(tmp_path, org_name="Acme"):
    sf = AdminStateFile(path=tmp_path / f"{org_name}.json")
    sf.complete_setup(org_name=org_name, admin_email="a@b.com", admin_password="pw123456")
    return AdminStateIdentityStore(sf), sf


def test_ensure_is_idempotent(tmp_path):
    store, _ = _store(tmp_path)
    first = store.ensure()
    second = store.ensure()
    assert first.instance_id == second.instance_id
    assert len(first.instance_id) == 32


def test_id_differs_across_organizations(tmp_path):
    store_a, _ = _store(tmp_path, "Acme")
    store_b, _ = _store(tmp_path, "Globex")
    assert store_a.ensure().instance_id != store_b.ensure().instance_id


def test_id_survives_identity_reset(tmp_path):
    """Deleting the stored identity re-derives the same id — no quota reset."""
    store, sf = _store(tmp_path)
    original = store.ensure()
    # Simulate a completed enrollment, then wipe ONLY the identity entry while
    # the org record (org_slug + setup_at) stays intact.
    store.save(
        InstanceIdentity(
            instance_id=original.instance_id, instance_secret="ab" * 32, registered=True
        )
    )
    sf._mutate(lambda d: d.pop("autopilot_identity", None))

    after = store.ensure()
    assert after.instance_id == original.instance_id  # same org → same id
    assert after.registered is False  # fresh: will re-enroll (and get the same key)


def test_enrolled_secret_is_preserved_across_calls(tmp_path):
    store, _ = _store(tmp_path)
    ident = store.ensure()
    store.save(
        InstanceIdentity(
            instance_id=ident.instance_id, instance_secret="cd" * 32, registered=True
        )
    )
    again = store.ensure()
    assert again.instance_id == ident.instance_id
    assert again.instance_secret == "cd" * 32  # enrolled key kept
    assert again.registered is True  # enrollment not repeated
