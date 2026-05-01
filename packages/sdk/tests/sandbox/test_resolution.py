# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sandbox requirement resolution cascade."""
import logging

import pytest

from sagewai.sandbox.models import (
    NetworkPolicy,
    SandboxImageVariant,
    SandboxMode,
)
from sagewai.sandbox.resolution import (
    SandboxRequirements,
    SandboxRequirementsError,
    resolve_requirements,
)


@pytest.mark.asyncio
async def test_resolve_explicit_wins():
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_TOOL,
        image="ghcr.io/sagewai/sandbox-base:0.0.0-dev",
        variant=SandboxImageVariant.BASE,
        network_policy=NetworkPolicy.FULL,
    )
    result = await resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        explicit_network_policy=NetworkPolicy.NONE,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_RUN
    assert result.image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert result.network_policy is NetworkPolicy.NONE


@pytest.mark.asyncio
async def test_resolve_agent_beats_project():
    agent = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_WORKER,
        image="ghcr.io/sagewai/sandbox-general:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.EGRESS_ALLOWLIST,
    )
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.NONE,
        image="ghcr.io/sagewai/sandbox-base:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    result = await resolve_requirements(
        agent_requirements=agent,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_WORKER
    assert result.network_policy is NetworkPolicy.EGRESS_ALLOWLIST


@pytest.mark.asyncio
async def test_resolve_project_beats_sdk_default():
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_RUN,
        image="ghcr.io/sagewai/sandbox-ops:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    result = await resolve_requirements(project_defaults=project, strict=False)
    assert result.sandbox_mode is SandboxMode.PER_RUN


@pytest.mark.asyncio
async def test_resolve_fallthrough_to_sdk_default(caplog):
    with caplog.at_level(logging.WARNING, logger="sagewai.sandbox.resolution"):
        result = await resolve_requirements(strict=False)
    assert result.sandbox_mode is SandboxMode.NONE
    assert result.network_policy is NetworkPolicy.NONE
    assert result.image.startswith("ghcr.io/sagewai/sandbox-base:")
    # Three WARN lines — one per fallthrough field.
    warn_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("sandbox_mode" in m for m in warn_messages)
    assert any("image" in m for m in warn_messages)
    assert any("network_policy" in m for m in warn_messages)


@pytest.mark.asyncio
async def test_resolve_strict_raises_on_fallthrough():
    with pytest.raises(SandboxRequirementsError) as ei:
        await resolve_requirements(strict=True)
    assert "sandbox_mode" in str(ei.value)
    assert "image" in str(ei.value)
    assert "network_policy" in str(ei.value)


@pytest.mark.asyncio
async def test_resolve_per_field_independent():
    """Caller sets only mode; image + network fall through to SDK default."""
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.NONE,
        image="ghcr.io/sagewai/sandbox-ops:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    result = await resolve_requirements(
        explicit_mode=SandboxMode.PER_WORKER,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_WORKER
    # image + network inherited from project
    assert result.image == "ghcr.io/sagewai/sandbox-ops:0.0.0-dev"
    assert result.network_policy is NetworkPolicy.FULL


@pytest.mark.asyncio
async def test_resolve_variant_populated_from_image(monkeypatch):
    from sagewai.sandbox import image_manifest
    monkeypatch.setitem(image_manifest.PINNED_DIGESTS, "ml", "sha256:" + "0" * 64)
    result = await resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        explicit_network_policy=NetworkPolicy.FULL,
        strict=False,
    )
    assert result.variant is SandboxImageVariant.ML


@pytest.mark.asyncio
async def test_resolve_variant_none_for_byo(monkeypatch):
    from sagewai.sandbox import image_manifest
    monkeypatch.setattr(image_manifest, "PINNED_DIGESTS", {})
    result = await resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/acme/custom:1.0",
        explicit_network_policy=NetworkPolicy.FULL,
        strict=False,
    )
    assert result.variant is None


@pytest.mark.asyncio
async def test_resolve_strict_from_env(monkeypatch):
    monkeypatch.setenv("SAGEWAI_SANDBOX_STRICT_REQUIREMENTS", "1")
    with pytest.raises(SandboxRequirementsError):
        await resolve_requirements()   # no strict= kwarg → reads env var


@pytest.mark.asyncio
async def test_resolve_requirements_with_origins_per_field():
    """with_origins=True returns (resolved, origins) with per-field origin."""
    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        SandboxResolutionOrigin,
        resolve_requirements,
    )

    project_default = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_TOOL,
        image="ghcr.io/sagewai/sandbox-base:1.0",
        variant=None,
        network_policy=NetworkPolicy.NONE,
    )

    resolved, origins = await resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,        # explicit wins for mode
        project_defaults=project_default,         # project wins for image + network
        with_origins=True,
    )

    assert resolved.sandbox_mode is SandboxMode.PER_RUN
    assert origins["sandbox_mode"] is SandboxResolutionOrigin.EXPLICIT
    assert origins["image"] is SandboxResolutionOrigin.PROJECT_DEFAULT
    assert origins["network_policy"] is SandboxResolutionOrigin.PROJECT_DEFAULT


@pytest.mark.asyncio
async def test_resolve_requirements_with_origins_sdk_default():
    """When everything falls through, origin is SDK_DEFAULT."""
    from sagewai.sandbox.resolution import (
        SandboxResolutionOrigin,
        resolve_requirements,
    )

    _, origins = await resolve_requirements(strict=False, with_origins=True)
    assert origins["sandbox_mode"] is SandboxResolutionOrigin.SDK_DEFAULT
    assert origins["image"] is SandboxResolutionOrigin.SDK_DEFAULT
    assert origins["network_policy"] is SandboxResolutionOrigin.SDK_DEFAULT


@pytest.mark.asyncio
async def test_resolve_requirements_without_origins_backwards_compat():
    """Default with_origins=False returns plain SandboxRequirements (existing callers)."""
    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import SandboxRequirements, resolve_requirements

    result = await resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/sagewai/sandbox-base:1.0",
        explicit_network_policy=NetworkPolicy.NONE,
    )
    assert isinstance(result, SandboxRequirements)
    # Single return value, no tuple unpacking — existing callers see no change.


@pytest.mark.asyncio
async def test_resolve_requirements_with_origins_agent_layer():
    """Agent requirements surface as AGENT origin (preview endpoint re-tags)."""
    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        SandboxResolutionOrigin,
        resolve_requirements,
    )

    agent_reqs = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_RUN,
        image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    _, origins = await resolve_requirements(
        agent_requirements=agent_reqs,
        with_origins=True,
    )
    assert origins["sandbox_mode"] is SandboxResolutionOrigin.AGENT
    assert origins["image"] is SandboxResolutionOrigin.AGENT


@pytest.mark.asyncio
async def test_resolve_agent_requirements_admin_wins(tmp_path, monkeypatch):
    """Admin override fully replaces Blueprint when set."""
    import json

    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        resolve_agent_requirements,
    )

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({
        "agents": [{
            "name": "writer",
            "sandbox_requirements_override": {
                "sandbox_mode": "per_run",
                "image": "ghcr.io/sagewai/sandbox-ml:0.1.5",
                "network_policy": "full",
                "required_secret_scopes": [],
            },
        }]
    }))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    blueprint = SandboxRequirements(
        sandbox_mode=SandboxMode.NONE,
        image="ghcr.io/sagewai/sandbox-base:1.0",
        variant=None,
        network_policy=NetworkPolicy.NONE,
    )
    result = await resolve_agent_requirements("writer", blueprint_requirements=blueprint)
    assert result is not None
    assert result.sandbox_mode is SandboxMode.PER_RUN
    assert result.image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert result.network_policy is NetworkPolicy.FULL


@pytest.mark.asyncio
async def test_resolve_agent_requirements_falls_through_to_blueprint(tmp_path, monkeypatch):
    """No admin override → returns Blueprint."""
    import json

    from sagewai.sandbox.models import NetworkPolicy, SandboxMode
    from sagewai.sandbox.resolution import (
        SandboxRequirements,
        resolve_agent_requirements,
    )

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"agents": [{"name": "writer", "model": "gpt-4o"}]}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    blueprint = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_TOOL,
        image="ghcr.io/sagewai/sandbox-base:1.0",
        variant=None,
        network_policy=NetworkPolicy.NONE,
    )
    result = await resolve_agent_requirements("writer", blueprint_requirements=blueprint)
    assert result is blueprint   # same object, not a copy


@pytest.mark.asyncio
async def test_resolve_agent_requirements_both_none(tmp_path, monkeypatch):
    """No admin override, no Blueprint → None."""
    import json

    from sagewai.sandbox.resolution import resolve_agent_requirements

    state_file = tmp_path / "admin-state.json"
    state_file.write_text(json.dumps({"agents": []}))
    monkeypatch.setenv("SAGEWAI_ADMIN_STATE_FILE", str(state_file))

    result = await resolve_agent_requirements("writer", blueprint_requirements=None)
    assert result is None
