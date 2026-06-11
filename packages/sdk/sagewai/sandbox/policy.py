# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.

"""Host-execution policy (spec §3, #10B)."""
from __future__ import annotations

import os


def host_exec_allowed() -> bool:
    """Whether host-backed (on-host NullBackend / bash / stdio MCP) execution is permitted.

    Default DENY everywhere; opt in with SAGEWAI_ALLOW_HOST_EXEC=1. This protects
    any deployment (local or container) that is exposed, not just the published image.

    **Multi-tenant mode forces it OFF (W7):** host-backed execution is impossible
    for tenants, regardless of SAGEWAI_ALLOW_HOST_EXEC — a tenant must never reach
    host bash / NullBackend / stdio MCP. The opt-in remains only for the trusted
    single-org self-hosted operator.
    """
    from sagewai.admin.tenancy import is_multi_tenant

    if is_multi_tenant():
        return False
    return os.environ.get("SAGEWAI_ALLOW_HOST_EXEC", "") in {"1", "true"}


# The execution modes that inject per-workload Sealed identity and therefore
# depend on Sealed *runtime* enforcement: Mode 2 (identity), Mode 3 (full),
# Mode 3b (full + JIT callback). Matched on the ExecutionMode string value.
_IDENTITY_EXECUTION_MODES = frozenset({"identity", "full", "full_jit"})


def identity_execution_preview_message(execution_mode) -> str:
    """The standard denial message for a preview-gated identity-mode run."""
    value = getattr(execution_mode, "value", execution_mode)
    return (
        f"Execution mode {value!r} (Sealed identity/full) is preview-only and "
        "disabled for tenants in multi-tenant mode; runtime secret protections "
        "are not yet enforced. Set SAGEWAI_SEALED_PREVIEW=1 to enable."
    )


def is_identity_execution_mode(execution_mode) -> bool:
    """Whether ``execution_mode`` is one of the Sealed identity modes (2/3/3b).

    Accepts an ``ExecutionMode`` enum member or a plain string. Returns True
    for ``identity`` / ``full`` / ``full_jit``; False for ``bare`` /
    ``sandboxed`` and any unrecognised value.
    """
    value = getattr(execution_mode, "value", execution_mode)
    return value in _IDENTITY_EXECUTION_MODES


def identity_execution_allowed() -> bool:
    """Whether Sealed identity execution (Modes 2/3/3b) may run.

    Modes 2/3/3b inject per-workload credentials into a sandbox and rely on
    Sealed's **runtime** enforcement — live secret injection into a running
    sandbox, redaction at the tool-runner RPC boundary, per-key / per-CLI ACL
    filtering, replay-safe injection, and mid-run (hard-revoke) abort. That
    runtime enforcement is **experimental and not wired into the default
    worker path** (``SealedSecretProvider`` is ``None`` by default; the
    redaction/ACL handles are gated on a secret provider the production worker
    does not set). Until it ships, these modes are **preview-only**.

    **Single-org mode → allowed.** The trusted self-hosted operator may run
    identity modes at their own risk; behaviour is unchanged.

    **Multi-tenant mode → forced OFF** unless the operator explicitly opts in
    with ``SAGEWAI_SEALED_PREVIEW=1`` (or ``true``), acknowledging that the
    runtime protections that isolate one tenant's credentials are not yet
    enforced. A tenant must never get an unenforced Sealed identity by
    default.
    """
    from sagewai.admin.tenancy import is_multi_tenant

    if not is_multi_tenant():
        return True
    return os.environ.get("SAGEWAI_SEALED_PREVIEW", "") in {"1", "true"}
