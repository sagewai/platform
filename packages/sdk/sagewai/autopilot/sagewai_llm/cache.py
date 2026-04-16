# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""File-backed blueprint cache with TTL-based expiry.

Each cached blueprint is stored as one JSON file in the cache directory,
named ``<sanitized-key>.json``. The value envelope is::

    {"stored_at": <epoch>, "blueprint_json": "<serialized blueprint>"}

TTL is enforced on read: entries older than ``ttl_seconds`` are treated
as misses and removed. The clock is injectable to make tests
deterministic.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from pathlib import Path

_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]+$")


class BlueprintCache:
    """TTL-bounded JSON-file cache for blueprints and small envelopes."""

    def __init__(
        self,
        directory: Path | str,
        *,
        ttl_seconds: int,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._dir = Path(directory)
        self._ttl = ttl_seconds
        self._clock = clock
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        if not _SAFE_KEY_RE.match(key):
            raise ValueError(f"invalid cache key: {key!r}")
        return self._dir / f"{key}.json"

    def get(self, key: str) -> str | None:
        path = self._path_for(key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        stored_at = envelope.get("stored_at")
        blueprint_json = envelope.get("blueprint_json")
        if not isinstance(stored_at, (int, float)) or not isinstance(blueprint_json, str):
            return None
        if self._clock() - stored_at > self._ttl:
            # Expired — silently evict.
            try:
                path.unlink()
            except OSError:
                pass
            return None
        return blueprint_json

    def put(self, key: str, blueprint_json: str) -> None:
        path = self._path_for(key)
        envelope = {
            "stored_at": self._clock(),
            "blueprint_json": blueprint_json,
        }
        path.write_text(json.dumps(envelope), encoding="utf-8")

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        try:
            path.unlink()
        except FileNotFoundError:
            return

    def clear(self) -> None:
        for path in self._dir.glob("*.json"):
            try:
                path.unlink()
            except OSError:
                pass
