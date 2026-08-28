# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Tests for the local content-addressed artifact store."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from sagewai.artifacts import LocalArtifactStore


def test_put_deduplicates_and_independent_store_resolves(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    content = b"durable verification output"
    digest = hashlib.sha256(content).hexdigest()
    store = LocalArtifactStore(root=root)

    first = store.put_bytes(content, media_type="text/plain", created_by="verifier")
    object_path = root / digest[:2] / digest
    first_mtime = object_path.stat().st_mtime_ns
    second = store.put_bytes(content, media_type="text/plain", created_by="verifier")

    assert first.digest == f"sha256:{digest}"
    assert first.storage_ref == f"artifact://sha256:{digest}"
    assert first.size_bytes == len(content)
    assert object_path.read_bytes() == content
    assert object_path.stat().st_mtime_ns == first_mtime
    assert second.storage_ref == first.storage_ref
    assert [path for path in root.rglob("*") if path.is_file()] == [object_path]

    independent = LocalArtifactStore(root=root)
    assert independent.resolve(first.storage_ref) == object_path
    assert independent.read(first.storage_ref) == content


@pytest.mark.parametrize(
    "storage_ref",
    [
        "artifact://sha256:abc",
        "artifact://sha256:" + "A" * 64,
        "artifact://sha1:" + "a" * 64,
        "file:///tmp/object",
        "artifact://sha256:../../escape",
    ],
)
def test_resolve_rejects_malformed_ref(tmp_path: Path, storage_ref: str) -> None:
    with pytest.raises(ValueError, match="artifact reference"):
        LocalArtifactStore(root=tmp_path).resolve(storage_ref)


def test_resolve_rejects_missing_object(tmp_path: Path) -> None:
    storage_ref = "artifact://sha256:" + "a" * 64

    with pytest.raises(FileNotFoundError):
        LocalArtifactStore(root=tmp_path).resolve(storage_ref)


def test_default_root_uses_sagewai_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "sagewai-home"
    monkeypatch.setenv("SAGEWAI_HOME", str(home))
    store = LocalArtifactStore()

    ref = store.put_bytes(b"default", media_type="text/plain", created_by="test")

    assert store.resolve(ref.storage_ref).is_relative_to(home / "objects")
