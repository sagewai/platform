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

    first = store.put_bytes(
        content,
        project_id="project-a",
        media_type="text/plain",
        created_by="verifier",
    )
    object_path = root / digest[:2] / digest
    first_mtime = object_path.stat().st_mtime_ns
    second = store.put_bytes(
        content,
        project_id="project-a",
        media_type="text/plain",
        created_by="verifier",
    )

    assert first.digest == f"sha256:{digest}"
    assert first.project_id == "project-a"
    assert first.storage_ref == f"artifact://sha256:{digest}"
    assert first.size_bytes == len(content)
    assert object_path.read_bytes() == content
    assert object_path.stat().st_mtime_ns == first_mtime
    assert second.storage_ref == first.storage_ref

    independent = LocalArtifactStore(root=root)
    assert independent.resolve(first.storage_ref, project_id="project-a") == object_path
    assert independent.read(first.storage_ref, project_id="project-a") == content


def test_same_content_is_deduplicated_across_project_scopes(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    content = b"shared content"
    store = LocalArtifactStore(root=root)

    project_ref = store.put_bytes(
        content,
        project_id="project-a",
        media_type="text/plain",
        created_by="test",
    )
    other_ref = store.put_bytes(
        content,
        project_id="project-b",
        media_type="text/plain",
        created_by="test",
    )

    assert project_ref.storage_ref == other_ref.storage_ref
    assert store.resolve(project_ref.storage_ref, project_id="project-a") == store.resolve(
        other_ref.storage_ref,
        project_id="project-b",
    )
    stored_files = [path for path in root.rglob("*") if path.is_file()]
    assert len({(path.stat().st_dev, path.stat().st_ino) for path in stored_files}) == 1


def test_resolve_does_not_disclose_object_owned_by_another_scope(tmp_path: Path) -> None:
    store = LocalArtifactStore(root=tmp_path)
    ref = store.put_bytes(
        b"private",
        project_id="project-a",
        media_type="text/plain",
        created_by="test",
    )

    with pytest.raises(FileNotFoundError) as denied:
        store.resolve(ref.storage_ref, project_id="project-b")
    missing_ref = "artifact://sha256:" + "a" * 64
    with pytest.raises(FileNotFoundError) as missing:
        store.resolve(missing_ref, project_id="project-b")

    assert denied.value.args == (ref.storage_ref,)
    assert missing.value.args == (missing_ref,)
    with pytest.raises(FileNotFoundError):
        store.read(ref.storage_ref, project_id="project-b")


def test_global_scope_and_project_named_global_are_distinct(tmp_path: Path) -> None:
    store = LocalArtifactStore(root=tmp_path)
    global_ref = store.put_bytes(
        b"org global",
        project_id=None,
        media_type="text/plain",
        created_by="test",
    )
    project_ref = store.put_bytes(
        b"project global",
        project_id="global",
        media_type="text/plain",
        created_by="test",
    )

    assert store.read(global_ref.storage_ref, project_id=None) == b"org global"
    assert store.read(project_ref.storage_ref, project_id="global") == b"project global"
    with pytest.raises(FileNotFoundError):
        store.read(global_ref.storage_ref, project_id="global")
    with pytest.raises(FileNotFoundError):
        store.read(project_ref.storage_ref, project_id=None)


def test_scope_namespace_is_traversal_safe(tmp_path: Path) -> None:
    root = tmp_path / "objects"
    outside = tmp_path / "escape"
    store = LocalArtifactStore(root=root)

    ref = store.put_bytes(
        b"safe",
        project_id="../../escape",
        media_type="text/plain",
        created_by="test",
    )

    assert store.read(ref.storage_ref, project_id="../../escape") == b"safe"
    assert not outside.exists()


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
        LocalArtifactStore(root=tmp_path).resolve(storage_ref, project_id="project-a")


def test_resolve_rejects_missing_object(tmp_path: Path) -> None:
    storage_ref = "artifact://sha256:" + "a" * 64

    with pytest.raises(FileNotFoundError):
        LocalArtifactStore(root=tmp_path).resolve(storage_ref, project_id="project-a")


def test_default_root_uses_sagewai_home(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "sagewai-home"
    monkeypatch.setenv("SAGEWAI_HOME", str(home))
    store = LocalArtifactStore()

    ref = store.put_bytes(
        b"default",
        project_id=None,
        media_type="text/plain",
        created_by="test",
    )

    assert store.resolve(ref.storage_ref, project_id=None).is_relative_to(home / "objects")
