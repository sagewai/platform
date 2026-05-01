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

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.resolution import CascadeLevel

from sagewai.sealed.revocation import CleanupResult


class SealedSecretProvider:
    """SecretProvider implementation backed by Sealed cascade resolution."""

    def __init__(
        self,
        audit_writer: AuditWriter,
        *,
        revocation_registry: Any | None = None,
    ) -> None:
        self._audit = audit_writer
        self._revocation_registry = revocation_registry

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
            revocation_registry=self._revocation_registry,
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

    async def cleanup_run(
        self,
        *,
        run_id: str,
        project_id: str,
        sandbox_handle: Any,
        effective_env_keys: list[str],
        effective_secret_keys: list[str],
        security_profile_ref: str | None,
    ) -> CleanupResult:
        """Called by SandboxPool when releasing a sandbox after run completion.

        Returns the env keys for the pool to unset, plus audit metadata.
        Provider owns the audit emission and the revocation registry lookup.
        Pool owns the actual container exec to unset env vars.
        """
        # Look up active revocations for this run's profile/keys (best-effort)
        had_active: list[str] = []
        if (
            self._revocation_registry is not None
            and security_profile_ref
            and effective_secret_keys
        ):
            try:
                actives = await self._revocation_registry.find_active_for_keys(
                    profile_id=security_profile_ref,
                    secret_keys=list(effective_secret_keys),
                )
                had_active = sorted(actives.keys())
            except Exception:
                # Best-effort: cleanup proceeds even if registry lookup fails
                had_active = []

        # Emit pool.sandbox.reset audit
        emitted = False
        try:
            await self._audit.emit(
                event_type="pool.sandbox.reset",
                run_id=run_id,
                project_id=project_id,
                profile_id=security_profile_ref,
                details={
                    "env_keys_to_scrub": sorted(effective_env_keys),
                    "had_active_revocations": had_active,
                },
            )
            emitted = True
        except Exception:
            emitted = False

        return CleanupResult(
            env_keys_to_unset=list(effective_env_keys),
            audit_emitted=emitted,
            had_active_revocations=had_active,
        )

    async def replay_env_for(
        self,
        *,
        project_id: str,
        run_id: str,
        agent_id: str | None,
        snapshot: Any,  # InjectionSnapshot — Any avoids circular import
        identity_from: str | None = None,
        declared_scopes: list[str] | None = None,
        sealed_levels: list[Any] | None = None,
    ) -> dict[str, str]:
        """Inject env for a replay run.

        When ``identity_from`` is ``"current_cascade"``, delegates to
        :meth:`env_for` so the cascade is **re-resolved** fresh (e.g. for a
        ``RestartWithFreshIdentity`` directive action).  All other values
        (``"original_injection"``, ``None``) use the historical snapshot path,
        which is the original Sealed-iii.C behaviour.

        For each secret in the snapshot (default path), validates the current
        backend value's hash against the snapshot. On match, uses the current
        value. On mismatch, uses ``get_secret_at_version`` if the backend
        supports value history; else raises ``RotationDriftError``.

        Emits ``replay.snapshot_loaded`` once at the start; per-secret
        ``replay.rotation_detected``, ``replay.failed_rotation_drift``,
        and ``replay.used_revoked_snapshot`` events as warranted.
        """
        # NEW: fresh cascade re-resolution for identity_from="current_cascade".
        if identity_from == "current_cascade":
            return await self.env_for(
                project_id=project_id,
                run_id=run_id,
                agent_id=agent_id,
                declared_scopes=declared_scopes or [],
                security_profile_ref=(
                    snapshot.security_profile_ref if snapshot is not None else None
                ),
                effective_env_keys=(
                    list(snapshot.effective_env_keys) if snapshot is not None else None
                ),
                effective_secret_keys=(
                    list(snapshot.effective_secret_keys) if snapshot is not None else None
                ),
                sealed_levels=sealed_levels,
            )

        from sagewai.sealed.refs import ProfileRef, resolve_backend
        from sagewai.sealed.replay import (
            RotationDriftError,
            hash_secret_value,
        )

        await self._audit.emit(
            event_type="replay.snapshot_loaded",
            run_id=run_id,
            project_id=project_id,
            profile_id=snapshot.security_profile_ref,
            details={
                "effective_env_keys": list(snapshot.effective_env_keys),
                "effective_secret_keys": list(snapshot.effective_secret_keys),
                "snapshot_captured_at": snapshot.captured_at,
            },
        )

        if not snapshot.security_profile_ref:
            return {}

        ref = ProfileRef.parse(snapshot.security_profile_ref)
        backend = resolve_backend(ref)
        profile = await backend.get_profile(ref.path)

        env: dict[str, str] = {}
        secret_keys = set(snapshot.effective_secret_keys)
        for k in snapshot.effective_env_keys:
            if k not in secret_keys:
                # Plain env key — read from current profile.env.
                env[k] = profile.env.get(k, "")
                continue

            # Secret key — verify hash, handle rotation, check revocation.
            current_value = profile.secrets.get(k, "")
            expected_hash = snapshot.secret_value_hashes.get(k)

            if expected_hash and hash_secret_value(current_value) != expected_hash:
                # Rotation detected — try to recover original value via
                # the backend's value history.
                version_id = snapshot.secret_value_versions.get(k)
                supports_history = await backend.supports_value_history()
                if version_id and supports_history:
                    try:
                        current_value = await backend.get_secret_at_version(
                            ref.path, k, version_id,
                        )
                        await self._audit.emit(
                            event_type="replay.rotation_detected",
                            run_id=run_id,
                            project_id=project_id,
                            profile_id=snapshot.security_profile_ref,
                            secret_key=k,
                            details={
                                "resolved_via": "version_history",
                                "version_id": version_id,
                            },
                        )
                    except Exception as exc:
                        await self._audit.emit(
                            event_type="replay.failed_rotation_drift",
                            run_id=run_id,
                            project_id=project_id,
                            profile_id=snapshot.security_profile_ref,
                            secret_key=k,
                            details={"error": str(exc)},
                        )
                        raise RotationDriftError(ref.path, k) from exc
                else:
                    await self._audit.emit(
                        event_type="replay.failed_rotation_drift",
                        run_id=run_id,
                        project_id=project_id,
                        profile_id=snapshot.security_profile_ref,
                        secret_key=k,
                        details={
                            "version_id": version_id,
                            "supports_history": supports_history,
                        },
                    )
                    raise RotationDriftError(ref.path, k)

            # Revocation snapshot check — if the registry now reports an
            # active revocation that wasn't in the snapshot, warn but
            # proceed using the snapshot value.
            if self._revocation_registry is not None:
                try:
                    actives = await self._revocation_registry.find_active_for_keys(
                        profile_id=ref.path, secret_keys=[k],
                    )
                except Exception:
                    actives = {}
                current_rev = actives.get(k)
                original_rev_id = snapshot.revocations_active_at_step.get(k)
                if current_rev and current_rev.id != original_rev_id:
                    await self._audit.emit(
                        event_type="replay.used_revoked_snapshot",
                        run_id=run_id,
                        project_id=project_id,
                        profile_id=snapshot.security_profile_ref,
                        secret_key=k,
                        details={
                            "current_revocation_id": current_rev.id,
                            "current_revocation_reason": current_rev.reason,
                            "original_revocation_id": original_rev_id,
                        },
                    )

            env[k] = current_value

        return env
