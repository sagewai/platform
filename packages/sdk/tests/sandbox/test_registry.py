# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Tests for sandbox mode and image resolution."""
from sagewai.sandbox.models import SandboxConfig, SandboxMode
from sagewai.sandbox.registry import (
    mode_rank,
    resolve_mode,
    resolve_sandbox_image,
)


def test_mode_rank_ordering():
    assert mode_rank(SandboxMode.NONE) < mode_rank(SandboxMode.PER_TOOL)
    assert mode_rank(SandboxMode.PER_TOOL) < mode_rank(SandboxMode.PER_RUN)
    assert mode_rank(SandboxMode.PER_RUN) < mode_rank(SandboxMode.PER_WORKER)


def test_mode_rank_accepts_string():
    assert mode_rank("per_run") == mode_rank(SandboxMode.PER_RUN)


def test_resolve_mode_cli_wins():
    cfg = SandboxConfig(mode=None)
    assert resolve_mode(
        cli_flag=SandboxMode.PER_RUN,
        config=cfg,
        project_environment="development",
    ) is SandboxMode.PER_RUN


def test_resolve_mode_config_beats_env():
    cfg = SandboxConfig(mode=SandboxMode.PER_TOOL)
    assert resolve_mode(
        cli_flag=None,
        config=cfg,
        project_environment="production",
    ) is SandboxMode.PER_TOOL


def test_resolve_mode_production_default():
    cfg = SandboxConfig()
    assert resolve_mode(None, cfg, "production") is SandboxMode.PER_RUN


def test_resolve_mode_staging_default():
    cfg = SandboxConfig()
    assert resolve_mode(None, cfg, "staging") is SandboxMode.PER_TOOL


def test_resolve_mode_dev_default():
    cfg = SandboxConfig()
    assert resolve_mode(None, cfg, "development") is SandboxMode.NONE


def test_resolve_mode_unknown_env_hard_default():
    cfg = SandboxConfig()
    assert resolve_mode(None, cfg, None) is SandboxMode.NONE


def test_resolve_sandbox_image_run_override_wins():
    img = resolve_sandbox_image(
        run_image="custom/run-image:1",
        agent_image="custom/agent-image:1",
        project_image="custom/project-image:1",
        worker_default="sagewai/sandbox-general:1",
    )
    assert img == "custom/run-image:1"


def test_resolve_sandbox_image_agent_over_project():
    img = resolve_sandbox_image(
        run_image=None,
        agent_image="custom/agent-image:1",
        project_image="custom/project-image:1",
        worker_default="sagewai/sandbox-general:1",
    )
    assert img == "custom/agent-image:1"


def test_resolve_sandbox_image_falls_back_to_worker_default():
    img = resolve_sandbox_image(None, None, None, "sagewai/sandbox-general:1")
    assert img == "sagewai/sandbox-general:1"


def test_resolve_sandbox_image_hard_default():
    img = resolve_sandbox_image(None, None, None, None)
    assert img == "ghcr.io/sagewai/sandbox-general:latest"


def test_network_policy_rank_ordering():
    from sagewai.sandbox.models import NetworkPolicy
    from sagewai.sandbox.registry import network_policy_rank

    assert network_policy_rank(NetworkPolicy.NONE) == 0
    assert network_policy_rank(NetworkPolicy.EGRESS_ALLOWLIST) == 1
    assert network_policy_rank(NetworkPolicy.FULL) == 2
    assert (
        network_policy_rank(NetworkPolicy.NONE)
        < network_policy_rank(NetworkPolicy.EGRESS_ALLOWLIST)
        < network_policy_rank(NetworkPolicy.FULL)
    )


def test_network_policy_rank_accepts_string():
    from sagewai.sandbox.registry import network_policy_rank

    assert network_policy_rank("none") == 0
    assert network_policy_rank("egress_allowlist") == 1
    assert network_policy_rank("full") == 2


def test_network_policy_rank_rejects_unknown():
    import pytest

    from sagewai.sandbox.registry import network_policy_rank

    with pytest.raises(ValueError):
        network_policy_rank("bogus")
