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


def test_resolve_explicit_wins():
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_TOOL,
        image="ghcr.io/sagewai/sandbox-base:0.0.0-dev",
        variant=SandboxImageVariant.BASE,
        network_policy=NetworkPolicy.FULL,
    )
    result = resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        explicit_network_policy=NetworkPolicy.NONE,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_RUN
    assert result.image == "ghcr.io/sagewai/sandbox-ml:0.1.5"
    assert result.network_policy is NetworkPolicy.NONE


def test_resolve_agent_beats_project():
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
    result = resolve_requirements(
        agent_requirements=agent,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_WORKER
    assert result.network_policy is NetworkPolicy.EGRESS_ALLOWLIST


def test_resolve_project_beats_sdk_default():
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.PER_RUN,
        image="ghcr.io/sagewai/sandbox-ops:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    result = resolve_requirements(project_defaults=project, strict=False)
    assert result.sandbox_mode is SandboxMode.PER_RUN


def test_resolve_fallthrough_to_sdk_default(caplog):
    with caplog.at_level(logging.WARNING, logger="sagewai.sandbox.resolution"):
        result = resolve_requirements(strict=False)
    assert result.sandbox_mode is SandboxMode.NONE
    assert result.network_policy is NetworkPolicy.NONE
    assert result.image.startswith("ghcr.io/sagewai/sandbox-base:")
    # Three WARN lines — one per fallthrough field.
    warn_messages = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert any("sandbox_mode" in m for m in warn_messages)
    assert any("image" in m for m in warn_messages)
    assert any("network_policy" in m for m in warn_messages)


def test_resolve_strict_raises_on_fallthrough():
    with pytest.raises(SandboxRequirementsError) as ei:
        resolve_requirements(strict=True)
    assert "sandbox_mode" in str(ei.value)
    assert "image" in str(ei.value)
    assert "network_policy" in str(ei.value)


def test_resolve_per_field_independent():
    """Caller sets only mode; image + network fall through to SDK default."""
    project = SandboxRequirements(
        sandbox_mode=SandboxMode.NONE,
        image="ghcr.io/sagewai/sandbox-ops:0.0.0-dev",
        variant=None,
        network_policy=NetworkPolicy.FULL,
    )
    result = resolve_requirements(
        explicit_mode=SandboxMode.PER_WORKER,
        project_defaults=project,
        strict=False,
    )
    assert result.sandbox_mode is SandboxMode.PER_WORKER
    # image + network inherited from project
    assert result.image == "ghcr.io/sagewai/sandbox-ops:0.0.0-dev"
    assert result.network_policy is NetworkPolicy.FULL


def test_resolve_variant_populated_from_image(monkeypatch):
    from sagewai.sandbox import image_manifest
    monkeypatch.setitem(image_manifest.PINNED_DIGESTS, "ml", "sha256:" + "0" * 64)
    result = resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/sagewai/sandbox-ml:0.1.5",
        explicit_network_policy=NetworkPolicy.FULL,
        strict=False,
    )
    assert result.variant is SandboxImageVariant.ML


def test_resolve_variant_none_for_byo(monkeypatch):
    from sagewai.sandbox import image_manifest
    monkeypatch.setattr(image_manifest, "PINNED_DIGESTS", {})
    result = resolve_requirements(
        explicit_mode=SandboxMode.PER_RUN,
        explicit_image="ghcr.io/acme/custom:1.0",
        explicit_network_policy=NetworkPolicy.FULL,
        strict=False,
    )
    assert result.variant is None


def test_resolve_strict_from_env(monkeypatch):
    monkeypatch.setenv("SAGEWAI_SANDBOX_STRICT_REQUIREMENTS", "1")
    with pytest.raises(SandboxRequirementsError):
        resolve_requirements()   # no strict= kwarg → reads env var
