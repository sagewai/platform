# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Canonical Sagewai home directory.

Single resolver for the on-disk layout used by a local install:

    $SAGEWAI_HOME/            (default ~/.sagewai)
      config/    admin-state.json, connections.json
      db/        sagewai.db   (SQLite: stores + sqlite-vec vectors)
      data/      artifacts, run outputs, scratch
      secrets/   master.key, profiles.json   (0700)

``SAGEWAI_HOME`` overrides the base. Per-file env overrides
(``SAGEWAI_ADMIN_STATE_FILE`` etc.) continue to win at their call sites.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def sagewai_home() -> Path:
    """Resolve the base home dir (``SAGEWAI_HOME`` or ``~/.sagewai``)."""
    override = os.environ.get("SAGEWAI_HOME")
    base = Path(override).expanduser() if override else Path.home() / ".sagewai"
    return base.resolve()


def _subdir(name: str, *, mode: int = 0o755) -> Path:
    path = sagewai_home() / name
    # Pass mode to mkdir so a freshly created secrets/ is private from the
    # start (no world-readable window between create and chmod); the chmod
    # then corrects a directory that already existed with looser perms.
    # umask never strips owner bits, so 0o700 survives mkdir.
    path.mkdir(parents=True, exist_ok=True, mode=mode)
    if mode != 0o755:
        try:
            path.chmod(mode)
        except OSError:  # pragma: no cover - non-POSIX
            pass
    return path


def config_dir() -> Path:
    """Human-readable config (admin-state.json, connections.json)."""
    return _subdir("config")


def db_dir() -> Path:
    """SQLite database directory (sagewai.db)."""
    return _subdir("db")


def data_dir() -> Path:
    """Artifacts, run outputs, caches, scratch."""
    return _subdir("data")


def secrets_dir() -> Path:
    """Sealed master key + encrypted profiles (0700)."""
    return _subdir("secrets", mode=0o700)


# Each entry: filename → (target_dir_fn, override_env_vars_that_pin_a_path)
# When any listed env var is set the operator is managing that file explicitly
# and migrate_home() must not touch it.
_LEGACY_LAYOUT: dict[str, tuple[Callable[[], Path], tuple[str, ...]]] = {
    "admin-state.json": (config_dir, ("SAGEWAI_ADMIN_STATE_FILE", "SAGEWAI_ADMIN_STATE")),
    "connections.json": (config_dir, ("SAGEWAI_CONNECTIONS_FILE",)),
    # master.key: SAGEWAI_MASTER_KEY is a key VALUE, not a file-path override → always migrate.
    "master.key": (secrets_dir, ()),
    "profiles.json": (secrets_dir, ()),
}


def migrate_home() -> list[str]:
    """Move legacy flat files at ``$SAGEWAI_HOME/`` into config/ and secrets/.

    Idempotent: only moves a file when it exists at the legacy root AND the
    new subfolder target does not. Returns the list of basenames moved.

    Per-file path overrides are honored: when an operator has set
    ``SAGEWAI_ADMIN_STATE_FILE``, ``SAGEWAI_ADMIN_STATE``, or
    ``SAGEWAI_CONNECTIONS_FILE``, the corresponding legacy file is **skipped**
    because the operator is managing that path explicitly — migrating it would
    break the pinned override path.
    """
    base = sagewai_home()
    moved: list[str] = []
    for name, (target_dir_fn, override_vars) in _LEGACY_LAYOUT.items():
        # Respect per-file path overrides: if any override env var is set the
        # operator controls that file path — do not touch the legacy copy.
        if any(os.environ.get(v) for v in override_vars):
            continue
        legacy = base / name
        if not legacy.is_file():
            continue
        target_dir = target_dir_fn()
        target = target_dir / name
        if target.is_file():
            continue  # never clobber a newer file
        legacy.replace(target)
        moved.append(name)
    if moved:
        logger.info("Migrated legacy home files into subfolders: %s", ", ".join(sorted(moved)))
    return moved
