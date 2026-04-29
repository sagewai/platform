# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Three-level cascade resolution for security profiles."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sagewai.sealed.models import EffectiveProfile
from sagewai.sealed.refs import ProfileRef, resolve_backend

if TYPE_CHECKING:
    from sagewai.sealed.audit import AuditWriter


@dataclass(frozen=True)
class CascadeLevel:
    name: str                                 # 'system' | 'workflow' | 'user'
    profile_ref: str | None
    overrides: dict[str, str] | None


async def resolve_security_profile(
    *,
    levels: list[CascadeLevel],
    audit_writer: AuditWriter | None = None,
    audit_context: dict | None = None,
    revocation_registry: Any | None = None,
) -> EffectiveProfile:
    """Resolve cascade levels into a single env dict + secret-key set.

    Per-key merge semantics: each level overlays via dict.update().
    Profile refs dereferenced via the backend registry. Inline overrides
    applied AFTER the profile_ref dereference at the same level.

    Empty-string override = tombstone (remove key from effective).

    If *revocation_registry* is provided, every secret_key in the
    effective set is checked against the registry.  Revoked keys raise
    ``SecretRevokedError`` before the cascade.resolved audit event fires.
    """
    effective: dict[str, str] = {}
    secret_keys: set[str] = set()
    cascade_origins: dict[str, str] = {}

    for level in levels:
        if level.profile_ref:
            ref = ProfileRef.parse(level.profile_ref)
            backend = resolve_backend(ref)
            profile = await backend.get_profile(ref.path)

            workflow_name = (audit_context or {}).get("workflow_name")
            if profile.allowed_workflows and workflow_name not in profile.allowed_workflows:
                if audit_writer:
                    await audit_writer.emit(
                        event_type="profile.access_denied",
                        profile_id=profile.id,
                        details={
                            "reason": "workflow_not_in_allowlist",
                            "workflow": workflow_name,
                        },
                        context=audit_context,
                    )
                raise PermissionError(
                    f"profile {profile.id!r} not allowed for workflow {workflow_name!r}"
                )

            for key, value in profile.env.items():
                effective[key] = value
                cascade_origins[key] = level.name
                secret_keys.discard(key)
            for key, value in profile.secrets.items():
                effective[key] = value
                cascade_origins[key] = level.name
                secret_keys.add(key)

        if level.overrides:
            for key, value in level.overrides.items():
                if value == "":
                    effective.pop(key, None)
                    cascade_origins.pop(key, None)
                    secret_keys.discard(key)
                else:
                    effective[key] = value
                    cascade_origins[key] = f"{level.name}_override"
                    # inline overrides NOT auto-tagged as secret
                    secret_keys.discard(key)

    # Sealed-iii.D: per-tool ACL cascade merge — later level wins per tool name.
    acl_effective: dict[str, list[str]] = {}
    for level in levels:
        if not level.profile_ref:
            continue
        ref = ProfileRef.parse(level.profile_ref)
        backend = resolve_backend(ref)
        try:
            level_profile = await backend.get_profile_metadata(ref.path)
        except Exception:
            continue
        for tool_name, allowed in level_profile.acl.items():
            acl_effective[tool_name] = list(allowed)

    # Sealed-iii.A: revocation check
    if revocation_registry is not None and secret_keys:
        from sagewai.sealed.revocation import SecretRevokedError

        # Map secret_key -> profile_id (the last cascading profile that wrote it)
        sk_to_profile: dict[str, str] = {}
        for level in levels:
            if not level.profile_ref:
                continue
            ref_parsed = ProfileRef.parse(level.profile_ref)
            backend = resolve_backend(ref_parsed)
            try:
                level_profile = await backend.get_profile(ref_parsed.path)
            except Exception:
                continue
            for k in level_profile.secrets.keys():
                sk_to_profile[k] = level_profile.id

        # Check each secret_key in the effective set against its source profile
        for sk in secret_keys:
            profile_id = sk_to_profile.get(sk)
            if profile_id is None:
                continue
            try:
                actives = await revocation_registry.find_active_for_keys(
                    profile_id=profile_id, secret_keys=[sk]
                )
            except Exception as exc:
                from sagewai.sealed.revocation import RevocationCheckUnavailableError
                raise RevocationCheckUnavailableError(
                    f"revocation registry unreachable while checking "
                    f"{profile_id!r}/{sk!r}: {exc}"
                ) from exc
            if sk in actives:
                revocation = actives[sk]
                if audit_writer:
                    await audit_writer.emit(
                        event_type="profile.access_denied",
                        profile_id=profile_id,
                        secret_key=sk,
                        details={
                            "reason": "secret_revoked",
                            "revocation_id": revocation.id,
                            "revocation_reason": revocation.reason,
                        },
                        context=audit_context,
                    )
                raise SecretRevokedError(
                    profile_id=profile_id,
                    secret_key=sk,
                    revocation_id=revocation.id,
                    reason=revocation.reason,
                )

    if audit_writer:
        await audit_writer.emit(
            event_type="profile.cascade.resolved",
            profile_id=(levels[-1].profile_ref if levels else None),
            details={
                "effective_env_keys": sorted(effective.keys()),
                "effective_secret_keys": sorted(secret_keys),
                "cascade_origins": cascade_origins,
                "revoked_keys_blocked": [],
            },
            context=audit_context,
        )

    return EffectiveProfile(
        env=effective,
        secret_keys=secret_keys,
        cascade_origins=cascade_origins,
        acl=acl_effective,
    )
