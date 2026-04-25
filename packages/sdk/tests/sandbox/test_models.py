"""Tests for sagewai.sandbox.models."""
from sagewai.sandbox.models import (
    BackendHealth,
    NetworkPolicy,
    ResourceLimits,
    SandboxConfig,
    SandboxLifetime,
    SandboxMode,
    SandboxStats,
    ToolCall,
    ToolResult,
)


def test_sandbox_stats_defaults():
    s = SandboxStats()
    assert s.cpu_percent == 0.0
    assert s.mem_bytes == 0
    assert s.disk_bytes == 0
    assert s.pids == 0


def test_sandbox_mode_values():
    assert SandboxMode.NONE.value == "none"
    assert SandboxMode.PER_TOOL.value == "per_tool"
    assert SandboxMode.PER_RUN.value == "per_run"
    assert SandboxMode.PER_WORKER.value == "per_worker"


def test_sandbox_lifetime_values():
    assert SandboxLifetime.PER_TOOL.value == "per_tool"
    assert SandboxLifetime.PER_RUN.value == "per_run"
    assert SandboxLifetime.PER_WORKER.value == "per_worker"


def test_network_policy_values():
    assert {p.value for p in NetworkPolicy} == {"none", "egress_allowlist", "full"}


def test_resource_limits_defaults():
    limits = ResourceLimits()
    assert limits.cpu == 2.0
    assert limits.mem_bytes == 2 * 1024**3
    assert limits.pids == 128
    assert limits.disk_bytes == 5 * 1024**3


def test_sandbox_config_minimal():
    cfg = SandboxConfig()
    assert cfg.mode is None  # unset; resolved later by registry
    assert cfg.backend == "docker"
    assert cfg.default_image == "ghcr.io/sagewai/sandbox-base:dev"
    assert cfg.network_policy == NetworkPolicy.NONE


def test_tool_call_round_trip():
    call = ToolCall(tool="bash", args={"command": "ls"}, call_id="c1", timeout_s=30)
    assert call.tool == "bash"
    assert call.args == {"command": "ls"}


def test_tool_result_ok():
    r = ToolResult(call_id="c1", ok=True, exit_code=0, stdout="hi", stderr="", duration_ms=5)
    assert r.ok


def test_backend_health_repr():
    h = BackendHealth(ok=True, backend="docker", detail="daemon reachable")
    assert h.ok
    assert "docker" in h.backend


def test_sandbox_image_variant_values():
    from sagewai.sandbox.models import SandboxImageVariant

    assert SandboxImageVariant.BASE.value == "base"
    assert SandboxImageVariant.GENERAL.value == "general"
    assert SandboxImageVariant.ML.value == "ml"
    assert SandboxImageVariant.OPS.value == "ops"
    assert SandboxImageVariant.ERP.value == "erp"
    assert SandboxImageVariant.ECOMMERCE.value == "ecommerce"
    assert SandboxImageVariant.API.value == "api"
    # ML_CUDA deferred to Plan 2.1
    assert "ml-cuda" not in {v.value for v in SandboxImageVariant}


def test_sandbox_image_variant_round_trip():
    from sagewai.sandbox.models import SandboxImageVariant

    assert SandboxImageVariant("ml") is SandboxImageVariant.ML


def test_sandbox_image_variant_rejects_unknown():
    import pytest

    from sagewai.sandbox.models import SandboxImageVariant

    with pytest.raises(ValueError):
        SandboxImageVariant("bogus-variant")
