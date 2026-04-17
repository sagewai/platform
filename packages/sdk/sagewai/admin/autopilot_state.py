# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Autopilot state helpers for the admin state file.

This module provides:

* :class:`AdminStateIdentityStore` — an :class:`InstanceIdentityStore`
  implementation backed by :class:`AdminStateFile`.  It stores the
  autopilot instance identity (``instance_id`` + ``instance_secret``)
  inside the existing admin state JSON under the ``autopilot_identity``
  key so that a single file holds all persistent state.

* Helpers :func:`get_autopilot_config` / :func:`set_autopilot_config`
  and :func:`get_autopilot_identity` / :func:`set_autopilot_identity`
  that follow the same read-mutate-write pattern used throughout
  :mod:`sagewai.admin.state_file`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity

if TYPE_CHECKING:
    from sagewai.admin.state_file import AdminStateFile


# ── Default autopilot config ──────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "tier": "anonymous",
    "base_url": "https://llm.sagewai.ai",
    "confidence_high": 0.85,
    "confidence_low": 0.65,
    "cache_ttl_seconds": 3600,
}


# ── AdminStateIdentityStore ───────────────────────────────────────────


class AdminStateIdentityStore:
    """InstanceIdentityStore backed by :class:`AdminStateFile`.

    The identity is stored under the ``autopilot_identity`` key in the
    admin state JSON:

    .. code-block:: json

        {
          "autopilot_identity": {
            "instance_id": "...",
            "instance_secret": "..."
          }
        }

    This replaces the default :class:`FileIdentityStore` for admin-panel
    deployments so that a single file holds all persistent state.
    """

    def __init__(self, sf: AdminStateFile) -> None:
        self._sf = sf

    def load(self) -> InstanceIdentity | None:
        """Load the identity from the state file, or return ``None``."""
        data = self._sf._read()
        raw = data.get("autopilot_identity")
        if not isinstance(raw, dict):
            return None
        iid = raw.get("instance_id")
        sec = raw.get("instance_secret")
        if not (isinstance(iid, str) and isinstance(sec, str)):
            return None
        return InstanceIdentity(instance_id=iid, instance_secret=sec)

    def save(self, identity: InstanceIdentity) -> None:
        """Persist the identity to the state file."""

        def _mutate(data: dict[str, Any]) -> None:
            data["autopilot_identity"] = {
                "instance_id": identity.instance_id,
                "instance_secret": identity.instance_secret,
            }

        self._sf._mutate(_mutate)


# ── Config helpers ────────────────────────────────────────────────────


def get_autopilot_config(sf: AdminStateFile) -> dict[str, Any]:
    """Return the current autopilot config, merged with defaults.

    Never raises — missing keys are filled from :data:`_DEFAULT_CONFIG`.
    """
    data = sf._read()
    stored = data.get("autopilot", {})
    return {**_DEFAULT_CONFIG, **stored}


def set_autopilot_config(sf: AdminStateFile, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge *patch* into the stored autopilot config and persist.

    Returns the new merged config.
    """
    allowed_keys = set(_DEFAULT_CONFIG)

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        current = {**_DEFAULT_CONFIG, **data.get("autopilot", {})}
        for k, v in patch.items():
            if k in allowed_keys:
                current[k] = v
        data["autopilot"] = current
        return current

    return sf._mutate(_mutate)


# ── Identity helpers ──────────────────────────────────────────────────


def get_autopilot_identity(sf: AdminStateFile) -> InstanceIdentity | None:
    """Return the stored :class:`InstanceIdentity`, or ``None``."""
    store = AdminStateIdentityStore(sf)
    return store.load()


def set_autopilot_identity(sf: AdminStateFile, identity: InstanceIdentity) -> None:
    """Persist *identity* into the admin state file."""
    store = AdminStateIdentityStore(sf)
    store.save(identity)


# ── Mission helpers ───────────────────────────────────────────────────


def list_missions(
    sf: AdminStateFile,
    *,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return stored autopilot missions, optionally filtered by project."""
    data = sf._read()
    missions: list[dict[str, Any]] = data.get("autopilot_missions", [])
    if project_id is not None:
        missions = [m for m in missions if m.get("project_id") == project_id]
    return list(missions)


def save_mission(sf: AdminStateFile, mission: dict[str, Any]) -> dict[str, Any]:
    """Append *mission* to the stored missions list and persist."""

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        missions = data.setdefault("autopilot_missions", [])
        missions.append(mission)
        return mission

    return sf._mutate(_mutate)
