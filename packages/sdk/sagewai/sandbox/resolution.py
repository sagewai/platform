# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
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

from sagewai.sandbox import image_manifest
from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)

logger = logging.getLogger(__name__)

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


class SandboxRequirementsError(RuntimeError):
    """Raised when STRICT mode is on and the cascade hit the SDK hard default."""


def resolve_requirements(
    *,
    explicit_mode: SandboxMode | None = None,
    explicit_image: str | None = None,
    explicit_network_policy: NetworkPolicy | None = None,
    agent_requirements: SandboxRequirements | None = None,
    project_defaults: SandboxRequirements | None = None,
    strict: bool | None = None,
) -> SandboxRequirements:
    """Resolve the cascade (explicit → agent → project → SDK default).

    Each field (mode, image, network_policy) is resolved independently —
    a caller that sets only `mode` still gets `image` + `network_policy`
    from the deeper cascade levels.

    ``strict`` defaults to True if ``SAGEWAI_SANDBOX_STRICT_REQUIREMENTS=1``
    is set in the environment; otherwise False. In strict mode, fallthrough
    to the SDK hard default raises SandboxRequirementsError. Otherwise the
    fallthrough is logged as WARNING (one per fallthrough field).
    """
    if strict is None:
        strict = os.environ.get("SAGEWAI_SANDBOX_STRICT_REQUIREMENTS") == "1"

    fallbacks: list[str] = []

    mode = _resolve_one(
        "sandbox_mode",
        explicit_mode,
        agent_requirements.sandbox_mode if agent_requirements else None,
        project_defaults.sandbox_mode if project_defaults else None,
        _SDK_DEFAULT_MODE,
        fallbacks,
    )
    image = _resolve_one(
        "image",
        explicit_image,
        agent_requirements.image if agent_requirements else None,
        project_defaults.image if project_defaults else None,
        _sdk_default_image(),
        fallbacks,
    )
    network = _resolve_one(
        "network_policy",
        explicit_network_policy,
        agent_requirements.network_policy if agent_requirements else None,
        project_defaults.network_policy if project_defaults else None,
        _SDK_DEFAULT_NETWORK,
        fallbacks,
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

    return SandboxRequirements(
        sandbox_mode=mode,
        image=image,
        variant=image_manifest.lookup_variant(image),
        network_policy=network,
    )


def _resolve_one(name, explicit, agent_val, project_val, sdk_default, fallbacks):
    if explicit is not None:
        return explicit
    if agent_val is not None:
        return agent_val
    if project_val is not None:
        return project_val
    fallbacks.append(name)
    return sdk_default
