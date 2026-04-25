# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL_LICENSE.md for details.
"""Sandbox data models — enums, resource limits, tool-call schemas."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class SandboxMode(str, Enum):
    """Worker-level sandbox mode selector."""

    NONE = "none"
    PER_TOOL = "per_tool"
    PER_RUN = "per_run"
    PER_WORKER = "per_worker"


class SandboxLifetime(str, Enum):
    """Per-sandbox lifetime hint passed to backend.start()."""

    PER_TOOL = "per_tool"
    PER_RUN = "per_run"
    PER_WORKER = "per_worker"


class NetworkPolicy(str, Enum):
    """Network access policy for a sandbox."""

    NONE = "none"
    EGRESS_ALLOWLIST = "egress_allowlist"  # enforced via proxy (Plan 3)
    FULL = "full"


class SandboxImageVariant(str, Enum):
    """Known Sagewai-published image variants. BYO images skip this enum."""

    BASE = "base"
    GENERAL = "general"
    ML = "ml"
    OPS = "ops"
    ERP = "erp"
    ECOMMERCE = "ecommerce"
    API = "api"
    # ML_CUDA = "ml-cuda" — added in Plan 2.1 when GPU CI ships


class ResourceLimits(BaseModel):
    """Per-sandbox resource limits enforced by the backend."""

    cpu: float = 2.0                       # CPU cores (docker --cpus)
    mem_bytes: int = 2 * 1024**3           # 2 GiB
    pids: int = 128
    disk_bytes: int = 5 * 1024**3          # 5 GiB (tmpfs upper bound)


class SandboxConfig(BaseModel):
    """Worker-level sandbox configuration. Resolved at worker startup."""

    mode: SandboxMode | None = None                           # None → resolve from env
    backend: str = "docker"
    default_image: str = "ghcr.io/sagewai/sandbox-base:dev"   # :dev until Plan 2
    network_policy: NetworkPolicy = NetworkPolicy.NONE
    resource_limits: ResourceLimits = Field(default_factory=ResourceLimits)
    image_variants: list[SandboxImageVariant] | None = None   # None → all manifest variants


class ToolCall(BaseModel):
    """A single tool-call dispatched into a sandbox."""

    tool: str
    args: dict[str, Any]
    call_id: str
    timeout_s: float = 60.0


class ToolResult(BaseModel):
    """Result of a single tool-call."""

    call_id: str
    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: int = 0
    error: str | None = None     # set when ok=False and exit_code irrelevant


class SandboxStats(BaseModel):
    """Runtime stats sample for a live sandbox."""

    cpu_percent: float = 0.0
    mem_bytes: int = 0
    disk_bytes: int = 0
    pids: int = 0


class BackendHealth(BaseModel):
    """Result of backend.health_check()."""

    ok: bool
    backend: str
    detail: str = ""
