# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Sandbox requirement resolution — the cascade consumed by every enqueue path.

Callers (workflow.enqueue, CLI, autopilot MissionDriver) pass whatever
they have; this module fills in the rest from agent spec, project
defaults, and finally the SDK hard default. Every resolved field is
concrete (mode/image/network_policy never None); variant is None only
for BYO/unknown refs.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sagewai.admin.state_file import AdminStateFile
    from sagewai.sealed.audit import AuditWriter
    from sagewai.sealed.resolution import CascadeLevel

from sagewai.artifacts.models import ArtifactDestination
from sagewai.artifacts.resolution import (
    ArtifactDestinationLevels,
    resolve_artifact_destination,
)
from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)

logger = logging.getLogger(__name__)


class SandboxResolutionOrigin(str, Enum):
    """Per-field cascade origin for resolution previews.

    Backward-compat note: ``AGENT`` is the generic origin for any value
    that came from the ``agent_requirements`` layer. The Plan 3b-i preview
    endpoint re-tags this as ``ADMIN_OVERRIDE`` or ``BLUEPRINT`` based on
    where ``resolve_agent_requirements()`` actually sourced the value.
    """

    EXPLICIT = "explicit"
    AGENT = "agent"
    ADMIN_OVERRIDE = "admin_override"
    BLUEPRINT = "blueprint"
    PROJECT_DEFAULT = "project_default"
    SDK_DEFAULT = "sdk_default"

_SDK_DEFAULT_MODE = SandboxMode.NONE
_SDK_DEFAULT_NETWORK = NetworkPolicy.NONE


def _sdk_default_image() -> str:
    """Hard-default image reference scoped to the current SDK version."""
    return f"ghcr.io/sagewai/sandbox-base:{image_manifest.SDK_VERSION}"


@dataclass(frozen=True)
class SandboxRequirements:
    """Resolved sandbox requirements persisted on a run row."""

    sandbox_mode: SandboxMode
    image: str
    variant: SandboxImageVariant | None
    network_policy: NetworkPolicy
    # ── sealed-i cascade output (Task 12 populates these) ──
    security_profile_ref: str | None = None
    effective_env_keys: tuple[str, ...] = ()
    effective_secret_keys: tuple[str, ...] = ()
    # ── plan ART (artifact destination) — None unless cascade resolved ──
    artifact_destination: ArtifactDestination | None = None


class SandboxRequirementsError(RuntimeError):
    """Raised when STRICT mode is on and the cascade hit the SDK hard default."""


async def resolve_requirements(
    *,
    explicit_mode: SandboxMode | None = None,
    explicit_image: str | None = None,
    explicit_network_policy: NetworkPolicy | None = None,
    agent_requirements: SandboxRequirements | None = None,
    project_defaults: SandboxRequirements | None = None,
    strict: bool | None = None,
    with_origins: bool = False,
    # NEW in Sealed-i — optional; absent means no cascade resolution
    sealed_levels: list[CascadeLevel] | None = None,
    audit_writer: AuditWriter | None = None,
    audit_context: dict | None = None,
    revocation_registry: Any | None = None,  # NEW in Sealed-iii.A
    # NEW in Plan ART — optional; absent means no destination resolution
    artifact_destination_levels: ArtifactDestinationLevels | None = None,
) -> SandboxRequirements | tuple[SandboxRequirements, dict[str, SandboxResolutionOrigin]]:
    """Resolve the cascade (explicit → agent → project → SDK default).

    Each field (mode, image, network_policy) is resolved independently —
    a caller that sets only `mode` still gets `image` + `network_policy`
    from the deeper cascade levels.

    ``strict`` defaults to True if ``SAGEWAI_SANDBOX_STRICT_REQUIREMENTS=1``
    is set in the environment; otherwise False. In strict mode, fallthrough
    to the SDK hard default raises SandboxRequirementsError. Otherwise the
    fallthrough is logged as WARNING (one per fallthrough field).

    When ``with_origins=True``, returns a ``(SandboxRequirements,
    dict[str, SandboxResolutionOrigin])`` tuple where each key is a field
    name and the value records which cascade layer supplied it. When False
    (default), returns only ``SandboxRequirements`` — existing callers are
    unaffected.

    ``sealed_levels``, ``audit_writer``, and ``audit_context`` are optional
    Sealed-i cascade args. When ``sealed_levels`` is provided, the sealed
    profile cascade is resolved and the result is stored on the returned
    ``SandboxRequirements`` as ``security_profile_ref``,
    ``effective_env_keys``, and ``effective_secret_keys``.
    """
    if strict is None:
        strict = os.environ.get("SAGEWAI_SANDBOX_STRICT_REQUIREMENTS") == "1"

    fallbacks: list[str] = []
    origins: dict[str, SandboxResolutionOrigin] | None = {} if with_origins else None

    mode = _resolve_one(
        "sandbox_mode",
        explicit_mode,
        agent_requirements.sandbox_mode if agent_requirements else None,
        project_defaults.sandbox_mode if project_defaults else None,
        _SDK_DEFAULT_MODE,
        fallbacks,
        origins,
    )
    image = _resolve_one(
        "image",
        explicit_image,
        agent_requirements.image if agent_requirements else None,
        project_defaults.image if project_defaults else None,
        _sdk_default_image(),
        fallbacks,
        origins,
    )
    network = _resolve_one(
        "network_policy",
        explicit_network_policy,
        agent_requirements.network_policy if agent_requirements else None,
        project_defaults.network_policy if project_defaults else None,
        _SDK_DEFAULT_NETWORK,
        fallbacks,
        origins,
    )

    if fallbacks and strict:
        raise SandboxRequirementsError(
            f"SAGEWAI_SANDBOX_STRICT_REQUIREMENTS=1 and no value set for: "
            f"{', '.join(fallbacks)} — declare them explicitly at enqueue, "
            f"in the agent spec, or in the project defaults."
        )
    for field in fallbacks:
        logger.warning(
            "sandbox resolution: field %s fell through to SDK default — "
            "add an explicit value on the enqueue call, agent spec, or "
            "project default",
            field,
        )

    # Sealed-i cascade resolution (optional)
    if sealed_levels:
        from sagewai.sealed.resolution import resolve_security_profile
        effective_profile = await resolve_security_profile(
            levels=sealed_levels,
            audit_writer=audit_writer,
            audit_context=audit_context,
            revocation_registry=revocation_registry,  # NEW in Sealed-iii.A
        )
        security_profile_ref_value = next(
            (lv.profile_ref for lv in reversed(sealed_levels) if lv.profile_ref),
            None,
        )
        effective_env_keys_value = tuple(sorted(effective_profile.env.keys()))
        effective_secret_keys_value = tuple(sorted(effective_profile.secret_keys))
    else:
        security_profile_ref_value = None
        effective_env_keys_value = ()
        effective_secret_keys_value = ()

    # Plan ART cascade resolution (optional, runs after Sealed cascade)
    if artifact_destination_levels is not None:
        artifact_destination_value = resolve_artifact_destination(
            artifact_destination_levels,
            effective_secret_keys_value,
        )
    else:
        artifact_destination_value = None

    resolved = SandboxRequirements(
        sandbox_mode=mode,
        image=image,
        variant=image_manifest.lookup_variant(image),
        network_policy=network,
        security_profile_ref=security_profile_ref_value,
        effective_env_keys=effective_env_keys_value,
        effective_secret_keys=effective_secret_keys_value,
        artifact_destination=artifact_destination_value,
    )

    if with_origins:
        return resolved, origins  # type: ignore[return-value]
    return resolved


async def resolve_agent_requirements(
    agent_name: str,
    *,
    blueprint_requirements: SandboxRequirements | None,
    admin_state: AdminStateFile | None = None,
) -> SandboxRequirements | None:
    """Merge admin-state override (if any) with Blueprint declaration.

    All-or-nothing: if admin override is set in admin-state, it fully
    replaces the Blueprint values. Returns None when neither admin nor
    Blueprint provides requirements.

    Plan 3b-i adds a new layer to the resolution cascade between
    explicit (level 1) and project default (level 4):
      - level 2: admin override (this function — from admin-state.json)
      - level 3: Blueprint (this function — from autopilot code)

    The lazy ``AdminStateFile`` import avoids any potential circular
    dependency between admin and sandbox subpackages.
    """
    from sagewai.admin.state_file import AdminStateFile

    state = admin_state or AdminStateFile()
    agent = state.get_agent(agent_name)
    override_dict = (agent or {}).get("sandbox_requirements_override")
    if override_dict:
        return SandboxRequirements(
            sandbox_mode=SandboxMode(override_dict["sandbox_mode"]),
            image=override_dict["image"],
            variant=image_manifest.lookup_variant(override_dict["image"]),
            network_policy=NetworkPolicy(override_dict["network_policy"]),
        )
    return blueprint_requirements


def _resolve_one(
    name,
    explicit,
    agent_val,
    project_val,
    sdk_default,
    fallbacks,
    origins: dict[str, SandboxResolutionOrigin] | None = None,
):
    """Resolve one field via the cascade. Records origin if provided."""
    if explicit is not None:
        if origins is not None:
            origins[name] = SandboxResolutionOrigin.EXPLICIT
        return explicit
    if agent_val is not None:
        if origins is not None:
            origins[name] = SandboxResolutionOrigin.AGENT
        return agent_val
    if project_val is not None:
        if origins is not None:
            origins[name] = SandboxResolutionOrigin.PROJECT_DEFAULT
        return project_val
    fallbacks.append(name)
    if origins is not None:
        origins[name] = SandboxResolutionOrigin.SDK_DEFAULT
    return sdk_default
