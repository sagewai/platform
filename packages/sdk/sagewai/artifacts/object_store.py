# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Local content-addressed artifact object storage."""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from sagewai._project_scope import project_scope_key
from sagewai.artifacts.models import ArtifactRef
from sagewai.home import objects_dir

_STORAGE_REF_RE = re.compile(r"artifact://sha256:([0-9a-f]{64})")


class ArtifactStore(Protocol):
    def put_bytes(
        self,
        content: bytes,
        *,
        project_id: str | None,
        media_type: str,
        created_by: str,
    ) -> ArtifactRef: ...

    def read(self, storage_ref: str, *, project_id: str | None) -> bytes: ...


class LocalArtifactStore:
    """Store immutable bytes under their SHA-256 digest."""

    def __init__(self, *, root: Path | None = None) -> None:
        self._root = (root if root is not None else objects_dir()).resolve()

    def put_bytes(
        self,
        content: bytes,
        *,
        project_id: str | None,
        media_type: str,
        created_by: str,
    ) -> ArtifactRef:
        """Store bytes once and return their immutable content reference."""
        digest = hashlib.sha256(content).hexdigest()
        path = self._object_path(digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                    temporary = Path(handle.name)
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        ownership_path = self._ownership_path(project_id, digest)
        ownership_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, ownership_path)
        except FileExistsError:
            pass
        return ArtifactRef(
            project_id=project_id,
            digest=f"sha256:{digest}",
            media_type=media_type,
            size_bytes=len(content),
            storage_ref=f"artifact://sha256:{digest}",
            created_at=datetime.now(timezone.utc),
            created_by=created_by,
        )

    def resolve(self, storage_ref: str, *, project_id: str | None) -> Path:
        """Resolve a valid reference to an existing local object path."""
        match = _STORAGE_REF_RE.fullmatch(storage_ref)
        if match is None:
            raise ValueError(f"invalid artifact reference: {storage_ref}")
        digest = match.group(1)
        path = self._object_path(digest)
        if not self._ownership_path(project_id, digest).is_file() or not path.is_file():
            raise FileNotFoundError(storage_ref)
        return path

    def read(self, storage_ref: str, *, project_id: str | None) -> bytes:
        """Read one existing object by its immutable reference."""
        return self.resolve(storage_ref, project_id=project_id).read_bytes()

    def _object_path(self, digest: str) -> Path:
        return self._root / digest[:2] / digest

    def _ownership_path(self, project_id: str | None, digest: str) -> Path:
        scope_digest = hashlib.sha256(project_scope_key(project_id).encode()).hexdigest()
        return self._root / ".ownership" / scope_digest / digest[:2] / digest
