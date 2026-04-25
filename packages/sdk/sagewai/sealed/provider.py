# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""SealedSecretProvider — replaces EnvSecretProvider.

At sandbox-start time, re-resolve the cascade and inject env vars.
Re-resolution catches any rotation that happened between enqueue and start
(emits profile.drift_at_injection if the key set differs)."""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.resolution import CascadeLevel


class SealedSecretProvider:
    """SecretProvider implementation backed by Sealed cascade resolution."""

    def __init__(self, audit_writer: AuditWriter) -> None:
        self._audit = audit_writer

    async def env_for(
        self,
        *,
        project_id: str,
        run_id: str,
        agent_id: str | None,
        declared_scopes: list[str],
        # NEW kwargs from Sealed-i — passed by the sandbox pool from the run row
        security_profile_ref: str | None = None,
        effective_env_keys: list[str] | None = None,
        effective_secret_keys: list[str] | None = None,
        sealed_levels: list[CascadeLevel] | None = None,
    ) -> dict[str, str]:
        if not sealed_levels:
            return {}

        from sagewai.sealed.resolution import resolve_security_profile

        effective = await resolve_security_profile(
            levels=sealed_levels,
            audit_writer=self._audit,
            audit_context={"run_id": run_id, "project_id": project_id, "agent_id": agent_id},
        )

        # Drift detection
        committed_keys = set(effective_env_keys or [])
        current_keys = set(effective.env.keys())
        added = current_keys - committed_keys
        removed = committed_keys - current_keys
        if added or removed:
            await self._audit.emit(
                event_type="profile.drift_at_injection",
                profile_id=security_profile_ref,
                run_id=run_id,
                project_id=project_id,
                details={
                    "added_keys": sorted(added),
                    "removed_keys": sorted(removed),
                },
            )

        await self._audit.emit(
            event_type="profile.injected",
            profile_id=security_profile_ref,
            run_id=run_id,
            project_id=project_id,
            details={
                "env_keys": sorted(effective.env.keys()),
                "secret_keys": sorted(effective.secret_keys),
                "agent_id": agent_id,
            },
        )

        return dict(effective.env)
