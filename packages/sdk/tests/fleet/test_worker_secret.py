# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Per-worker secret storage + global enrollment-key lookup."""
from __future__ import annotations

import hashlib

import pytest

from sagewai.fleet.models import WorkerCapabilities
from sagewai.fleet.registry import InMemoryFleetRegistry, _hash_key


@pytest.mark.asyncio
async def test_register_worker_stores_secret_hash():
    reg = InMemoryFleetRegistry()
    secret = "s3cr3t-token"
    worker = await reg.register_worker(
        name="w", org_id="orgA",
        capabilities=WorkerCapabilities(models_supported=["gpt-4o"]),
        secret_hash=_hash_key(secret),
    )
    assert worker.secret_hash == hashlib.sha256(secret.encode()).hexdigest()


@pytest.mark.asyncio
async def test_find_enrollment_key_by_hash_is_global():
    reg = InMemoryFleetRegistry()
    rec, raw = await reg.create_enrollment_key(
        org_id="orgA", name="k", created_by="admin",
    )
    found = await reg.find_enrollment_key_by_hash(_hash_key(raw))
    assert found is not None and found.org_id == "orgA"
    assert await reg.find_enrollment_key_by_hash(_hash_key("nope")) is None


def test_worker_record_excludes_secret_hash_from_serialization():
    from datetime import datetime, timezone

    from sagewai.fleet.models import WorkerRecord

    w = WorkerRecord(
        id="w", name="n", org_id="o",
        registered_at=datetime.now(timezone.utc), secret_hash="deadbeef",
    )
    assert w.secret_hash == "deadbeef"  # attribute access still works
    assert "secret_hash" not in w.model_dump(mode="json")  # never serialized


@pytest.mark.asyncio
async def test_postgres_registry_refuses_secret_hash():
    from sagewai.fleet.registry import PostgresFleetRegistry

    reg = PostgresFleetRegistry(pool=None)  # guard fires before any DB access
    with pytest.raises(NotImplementedError):
        await reg.register_worker(
            name="w", org_id="o",
            capabilities=WorkerCapabilities(models_supported=["gpt-4o"]),
            secret_hash="x",
        )
