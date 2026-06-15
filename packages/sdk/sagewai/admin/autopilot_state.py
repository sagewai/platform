# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
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

import hashlib
import secrets
from typing import TYPE_CHECKING, Any, Callable

from sagewai.autopilot.sagewai_llm.client import _default_base_url
from sagewai.autopilot.sagewai_llm.identity import InstanceIdentity

#: Domain-separation prefix for the org-derived instance id. Bump only if the
#: derivation basis changes (it would re-key every install).
_INSTANCE_ID_BASIS = "sagewai-autopilot-instance-v1"

if TYPE_CHECKING:
    from sagewai.admin.state_file import AdminStateFile


# ── Default autopilot config ──────────────────────────────────────────

# NOTE: ``base_url`` is deliberately NOT here. It is infrastructure config, not
# a user choice — it is resolved at read time from SAGEWAI_LLM_BASE_URL or the
# built-in default (see get_autopilot_config) and is never written to per-install
# state. Persisting it would freeze whatever default shipped at setup time into
# the state file, so a later default change wouldn't take effect.
_DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "tier": "anonymous",
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
        return InstanceIdentity(
            instance_id=iid,
            instance_secret=sec,
            registered=bool(raw.get("registered", False)),
        )

    def save(self, identity: InstanceIdentity) -> None:
        """Persist the identity to the state file."""

        def _mutate(data: dict[str, Any]) -> None:
            data["autopilot_identity"] = {
                "instance_id": identity.instance_id,
                "instance_secret": identity.instance_secret,
                "registered": identity.registered,
            }

        self._sf._mutate(_mutate)

    @staticmethod
    def _derive_instance_id(data: dict[str, Any]) -> str:
        """Deterministically derive the instance id from the organization.

        One autopilot key per organization, so quota is fair and un-farmable:
        the id is a hash of the org's stable, immutable setup identity
        (``org_slug`` + ``setup_at``), NOT a random value. It is therefore the
        same for the life of the org regardless of identity-file resets or
        container redeploys — you cannot mint a second identity (or reset quota)
        from the same install without wiping the org itself.
        """
        slug = data.get("org_slug") or data.get("org_name") or "default"
        setup_at = data.get("setup_at") or ""
        basis = f"{_INSTANCE_ID_BASIS}:{slug}:{setup_at}"
        return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:32]

    def ensure(self) -> InstanceIdentity:
        """Return the org-derived instance identity, idempotently.

        The id is derived from the org (see :meth:`_derive_instance_id`); the
        secret is a placeholder until enrollment adopts the server-derived key.
        A previously-enrolled secret (and the ``registered`` flag) is preserved
        whenever the stored id still matches the org — so enrollment runs once,
        not on every call. If the stored identity is missing or belongs to a
        different org (e.g. the file was reset), a fresh unenrolled identity with
        the SAME derived id is minted, and enrollment re-fetches the same secret.
        """
        data = self._sf._read()
        derived_id = self._derive_instance_id(data)
        raw = data.get("autopilot_identity")
        if isinstance(raw, dict) and raw.get("instance_id") == derived_id:
            sec = raw.get("instance_secret")
            if isinstance(sec, str):
                return InstanceIdentity(
                    instance_id=derived_id,
                    instance_secret=sec,
                    registered=bool(raw.get("registered", False)),
                )
        identity = InstanceIdentity(
            instance_id=derived_id,
            instance_secret=secrets.token_hex(32),  # placeholder until enrolled
            registered=False,
        )
        self.save(identity)
        return identity


# ── Config helpers ────────────────────────────────────────────────────


def get_autopilot_config(sf: AdminStateFile) -> dict[str, Any]:
    """Return the current autopilot config, merged with defaults.

    Never raises — missing keys are filled from :data:`_DEFAULT_CONFIG`.
    """
    data = sf._read()
    stored = data.get("autopilot", {})
    config = {**_DEFAULT_CONFIG, **stored}
    # base_url is infrastructure config, NOT per-install state: always resolve it
    # from SAGEWAI_LLM_BASE_URL (self-hosted override) or the built-in default.
    # Any value lingering in `stored` is ignored, so a default change always
    # takes effect and there is no stale URL to "migrate".
    config["base_url"] = _default_base_url()
    return config


def set_autopilot_config(sf: AdminStateFile, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge *patch* into the stored autopilot config and persist.

    Returns the new merged config. ``base_url`` is never persisted (it is
    resolved at read time — see :func:`get_autopilot_config`).
    """
    allowed_keys = set(_DEFAULT_CONFIG)  # excludes base_url by construction

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        current = {**_DEFAULT_CONFIG, **data.get("autopilot", {})}
        current.pop("base_url", None)  # drop any value frozen in by older builds
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

#: Default values for the Plan-H execution-trace fields.  Keys absent in
#: a legacy mission record are filled with these values by
#: :func:`migrate_mission_record`.
MISSION_NEW_FIELDS: dict[str, Any] = {
    "run_id": None,
    "started_at": None,
    "finished_at": None,
    "total_cost_usd": 0.0,
    "step_count": 0,
    "last_event_at": None,
    "trace": [],
    "error": None,
}


def migrate_mission_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *raw* with Plan-H execution-trace fields filled in.

    Existing values are never overwritten.  The input dict is not mutated.
    Calling this function multiple times on the same dict is idempotent.
    """
    result = dict(raw)
    for key, default in MISSION_NEW_FIELDS.items():
        if key not in result:
            # Use a fresh list for each record so callers can't share state.
            result[key] = [] if isinstance(default, list) else default
    return result


def list_missions(
    sf: AdminStateFile,
    *,
    project_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return stored autopilot missions, optionally filtered by project.

    Each returned record is passed through :func:`migrate_mission_record`
    so callers always see the full Plan-H schema regardless of when the
    record was originally created.
    """
    data = sf._read()
    missions: list[dict[str, Any]] = data.get("autopilot_missions", [])
    if project_id is not None:
        missions = [m for m in missions if m.get("project_id") == project_id]
    return [migrate_mission_record(m) for m in missions]


def get_mission(sf: AdminStateFile, mission_id: str) -> dict[str, Any] | None:
    """Return the mission record for *mission_id*, or ``None`` if not found.

    The returned record is passed through :func:`migrate_mission_record`
    so callers always see the full Plan-H schema.
    """
    data = sf._read()
    for m in data.get("autopilot_missions", []):
        if m.get("mission_id") == mission_id:
            return migrate_mission_record(m)
    return None


def save_mission(sf: AdminStateFile, mission: dict[str, Any]) -> dict[str, Any]:
    """Append *mission* to the stored missions list and persist."""

    def _mutate(data: dict[str, Any]) -> dict[str, Any]:
        missions = data.setdefault("autopilot_missions", [])
        missions.append(mission)
        return mission

    return sf._mutate(_mutate)


def update_mission(
    sf: AdminStateFile,
    mission_id: str,
    mutator: Callable[[dict[str, Any]], None],
) -> dict[str, Any]:
    """Find a mission by *mission_id*, apply *mutator* in place, persist, return it.

    Parameters
    ----------
    sf:
        The admin state file store.
    mission_id:
        The mission to update.
    mutator:
        A callable that receives the mission dict and mutates it in place.
        The return value of *mutator* is ignored.

    Returns
    -------
    dict
        The updated (and migrated) mission record.

    Raises
    ------
    KeyError
        If no mission with *mission_id* exists.
    """

    def _inner(data: dict[str, Any]) -> dict[str, Any]:
        missions: list[dict[str, Any]] = data.get("autopilot_missions", [])
        for mission in missions:
            if mission.get("mission_id") == mission_id:
                mutator(mission)
                return dict(mission)
        raise KeyError(f"mission '{mission_id}' not found")

    return sf._mutate(_inner)
