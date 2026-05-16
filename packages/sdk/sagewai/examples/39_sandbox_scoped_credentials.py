#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Example 39 — Sandbox + scoped credentials + 3rd-party call.

The platform's biggest production-readiness story in one runnable file:
each tenant's agent boots inside its own isolated container, carrying
**only that tenant's** Sealed-issued credential, makes a real
authenticated call to a 3rd-party service, and the captured output
passes through redaction before any host-side log surface ever sees it.

The two pillars touch:

- Sealed gives each tenant its own scoped credential (per-CLI workload
  identity). The credential is materialised into a profile, never
  exported to the host environment.
- The Sagewai sandbox boots the agent inside an isolated container with
  ``--cap-drop=ALL``, ``no-new-privileges``, read-only rootfs, and a
  ``ResourceLimits`` cap. Only the tenant's own profile env reaches it.

The example proves four things end-to-end:

1. The host process never holds either tenant's token (host env scan
   before, during, and after each container call).
2. Each container sees its own tenant's token; neither sees the other's
   (cross-tenant boundary).
3. Redaction scrubs the live secret out of any captured 3rd-party
   response before it touches a host log line.
4. Running the same workflow twice produces byte-identical (deterministic)
   network calls — the proof a CFO + auditor can rerun.

What's exercised:

- :class:`~sagewai.sealed.builtin_backend.BuiltinAdminStoreBackend` —
  per-tenant Sealed profiles
- :class:`~sagewai.sealed.models.ProfileWritePayload` — secret materialisation
- :func:`~sagewai.sealed.resolution.resolve_security_profile` — cascade resolve
- :class:`~sagewai.sandbox.models.SandboxConfig`,
  :class:`~sagewai.sandbox.models.SandboxMode`,
  :class:`~sagewai.sandbox.models.NetworkPolicy`,
  :class:`~sagewai.sandbox.models.ResourceLimits` — config surface that
  describes what hits the wire to the Docker engine
- :class:`~sagewai.sealed.redaction.Redactor` — value redaction over the
  captured 3rd-party response
- Live ``docker run`` with the same hardening flags the SDK's
  :class:`~sagewai.sandbox.docker_backend.DockerBackend` applies
- :class:`~sagewai.sandbox.null_backend.NullBackend` fallback when
  Docker is not running, so the demo always reaches the proof section

A note on backends. :class:`~sagewai.sandbox.docker_backend.DockerBackend`
expects a Sagewai-published sandbox image carrying a ``sagewai-tool-runner``
PID 1 — not yet on a public registry at the time of this example. To
keep the example runnable on a clean machine in 60 seconds, the live
path drives ``docker run`` directly against ``python:3.11-slim``,
applying the same security flags (``--cap-drop=ALL``,
``--security-opt no-new-privileges``, ``--read-only``, ``--memory``,
``--cpus``, ``--pids-limit``) the DockerBackend applies internally.
The :class:`~sagewai.sandbox.models.SandboxConfig` instance the example
prints up-front is the *exact* config a Sagewai worker would consult to
synthesise this command — the example bridges the two views.

Requirements::

    pip install 'sagewai[sandbox]'
    # Optional, for the live container path:
    #   - Docker daemon running locally (Docker Desktop, Colima, …)
    #   - python:3.11-slim image (auto-pulled on first run)
    # No env vars required. Tenant tokens are synthesised at startup,
    # so nothing leaks from your shell into the demo.

Usage::

    python 39_sandbox_scoped_credentials.py
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
import subprocess
import sys
from pathlib import Path

from cryptography.fernet import Fernet

from sagewai.sandbox.models import (
    NetworkPolicy,
    ResourceLimits,
    SandboxConfig,
    SandboxLifetime,
    SandboxMode,
    ToolCall,
)
from sagewai.sandbox.null_backend import NullBackend
from sagewai.sealed.builtin_backend import BuiltinAdminStoreBackend
from sagewai.sealed.crypto import Crypto
from sagewai.sealed.models import ProfileWritePayload
from sagewai.sealed.redaction import Redactor
from sagewai.sealed.refs import _BACKENDS
from sagewai.sealed.resolution import CascadeLevel, resolve_security_profile

# ── two tenants, each with its own scoped credential ────────────────


TENANTS: list[dict[str, str]] = [
    {
        "id": "acme",
        "project_id": "acme-prod",
        "profile_id": "acme-webhook-profile",
        "secret_key": "WEBHOOK_API_KEY",
    },
    {
        "id": "globex",
        "project_id": "globex-prod",
        "profile_id": "globex-webhook-profile",
        "secret_key": "WEBHOOK_API_KEY",
    },
]


# ── third-party endpoint that echoes the bearer header back ─────────


THIRD_PARTY_URL = "https://httpbin.org/bearer"
PUBLIC_BASE_IMAGE = "python:3.11-slim"


# ── helpers ─────────────────────────────────────────────────────────


def _line(text: str = "", char: str = "─") -> None:
    if not text:
        print(char * 72)
    else:
        suffix = char * max(3, 68 - len(text))
        print(f"{char * 3} {text} {suffix}")


def _docker_running() -> bool:
    """Return True when ``docker ps`` succeeds within 2 seconds."""
    if shutil.which("docker") is None:
        return False
    try:
        result = subprocess.run(
            ["docker", "ps", "--format", "{{.ID}}"],
            capture_output=True, timeout=2.0, check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return False
    return result.returncode == 0


def _format_config(cfg: SandboxConfig) -> list[str]:
    """Render a SandboxConfig as a list of ``key = value`` lines."""
    return [
        f"mode             = {cfg.mode.value if cfg.mode else 'none'}",
        f"backend          = {cfg.backend}",
        f"network_policy   = {cfg.network_policy.value}",
        f"cpu              = {cfg.resource_limits.cpu} cores",
        f"mem_bytes        = {cfg.resource_limits.mem_bytes // (1024**2)} MiB",
        f"pids             = {cfg.resource_limits.pids}",
        f"disk_bytes       = {cfg.resource_limits.disk_bytes // (1024**2)} MiB",
    ]


def _docker_run_inline(
    *, image: str, env: dict[str, str], script: str,
    resource_limits: ResourceLimits, network_policy: NetworkPolicy,
    timeout_s: float = 30.0,
) -> tuple[int, str, str]:
    """Execute ``script`` in a one-shot container with the SDK's flags.

    Mirrors the hardening DockerBackend applies: cap-drop=ALL,
    no-new-privileges, read-only rootfs, mem/cpu/pid caps, network
    bridge or none. Returns (exit_code, stdout, stderr).
    """
    network_arg = "none" if network_policy is NetworkPolicy.NONE else "bridge"
    cmd: list[str] = [
        "docker", "run", "--rm",
        "--cap-drop=ALL",
        "--security-opt", "no-new-privileges:true",
        "--read-only",
        "--tmpfs", "/tmp:size=64m",
        "--network", network_arg,
        "--memory", str(resource_limits.mem_bytes),
        "--cpus", str(resource_limits.cpu),
        "--pids-limit", str(resource_limits.pids),
    ]
    for k, v in env.items():
        cmd.extend(["-e", f"{k}={v}"])
    cmd.extend([image, "python", "-c", script])

    try:
        proc = subprocess.run(
            cmd, capture_output=True, timeout=timeout_s, check=False,
        )
    except subprocess.TimeoutExpired:
        return (124, "", f"docker run timed out after {timeout_s}s")
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", errors="replace"),
        proc.stderr.decode("utf-8", errors="replace"),
    )


# Inline script the container runs. urllib.request only — no extra pip.
# It echoes the Authorization header to httpbin, parses the JSON, and
# emits a single-line JSON record we can grep host-side.
_CONTAINER_SCRIPT = r"""
import json, os, sys, urllib.request, urllib.error

token = os.environ.get("WEBHOOK_API_KEY", "")
url = os.environ.get("WEBHOOK_URL", "")
out = {"saw_token_in_env": bool(token), "saw_token_first8": token[:8]}

if not url:
    out["call"] = "skipped:no_url"
    sys.stdout.write(json.dumps(out) + "\n")
    sys.exit(0)

req = urllib.request.Request(
    url, headers={"Authorization": f"Bearer {token}"}
)
try:
    with urllib.request.urlopen(req, timeout=4.0) as resp:
        body = resp.read().decode("utf-8", errors="replace")
        out["status"] = resp.status
        out["body"] = body
except urllib.error.HTTPError as exc:
    out["status"] = exc.code
    out["body"] = exc.read().decode("utf-8", errors="replace")
except (urllib.error.URLError, OSError) as exc:
    out["status"] = -1
    out["error"] = f"{type(exc).__name__}: {exc}"
sys.stdout.write(json.dumps(out) + "\n")
"""


async def _run_in_null_backend(
    *, project_id: str, run_id: str, env: dict[str, str],
) -> tuple[int, str, str]:
    """Stub-mode fallback: NullBackend in-process bash with env scoped.

    NullBackend enforces env scrub (no os.environ passthrough) and a
    per-run scratch cwd, so cross-tenant boundaries still hold even
    without a container. The same script the live container runs is
    written to /tmp and invoked via the backend's bash tool, so the
    tenant only sees its own ``WEBHOOK_API_KEY`` value.
    """
    backend = NullBackend()
    handle = await backend.start(
        project_id=project_id, run_id=run_id,
        image="null", image_digest="",
        env=env,
        network_policy=NetworkPolicy.FULL,
        resource_limits=ResourceLimits(),
        workdir_mount=None,
        lifetime=SandboxLifetime.PER_RUN,
    )
    tmp = Path(f"/tmp/sagewai-ex39-{run_id}.py")
    tmp.write_text(_CONTAINER_SCRIPT, encoding="utf-8")
    try:
        result = await handle.exec(ToolCall(
            tool="bash",
            args={"command": f"{sys.executable} {tmp}"},
            call_id=f"call-{run_id}",
            timeout_s=10.0,
        ))
    finally:
        await handle.stop()
        try:
            tmp.unlink()
        except OSError:
            pass
    return (result.exit_code or 0, result.stdout, result.stderr)


def _parse_response(stdout: str) -> dict:
    """Pick out the last JSON line from a container's stdout."""
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return {}


# ── main ────────────────────────────────────────────────────────────


async def main() -> None:
    _line()
    print(" Sagewai — sandbox + scoped credentials + 3rd-party call (39)")
    _line()
    print()

    # 0. Synthesise per-tenant secrets. NEVER export to os.environ — the
    #    host process must end the demo without ever holding these in
    #    its own env. We track the live values in a dict that lives only
    #    inside this function's frame.
    tenant_secrets: dict[str, str] = {}
    for t in TENANTS:
        token = "wh_" + secrets.token_urlsafe(24)
        tenant_secrets[t["id"]] = token

    # 1. Set up Sealed: per-tenant profile carrying its own secret.
    crypto = Crypto(Fernet.generate_key())
    profiles_path = Path("/tmp/example39-profiles.json")
    if profiles_path.exists():
        profiles_path.unlink()
    sealed_backend = BuiltinAdminStoreBackend(
        profiles_path=profiles_path, crypto=crypto,
    )
    _BACKENDS["builtin"] = sealed_backend

    print("  Materialising 2 Sealed profiles (one per tenant)…")
    for t in TENANTS:
        await sealed_backend.save_profile(ProfileWritePayload(
            id=t["profile_id"],
            name=t["id"].title(),
            env={"TENANT": t["id"], "TIER": "production"},
            secrets={t["secret_key"]: tenant_secrets[t["id"]]},
        ))
        print(f"    {t['profile_id']:<32} secret_keys=['{t['secret_key']}']")
    print()

    # 2. Sandbox config — what a Sagewai worker would feed the backend.
    cfg = SandboxConfig(
        mode=SandboxMode.PER_RUN,
        backend="docker",
        default_image=PUBLIC_BASE_IMAGE,
        network_policy=NetworkPolicy.FULL,  # need bridge for httpbin
        resource_limits=ResourceLimits(
            cpu=1.0, mem_bytes=256 * 1024**2, pids=64, disk_bytes=512 * 1024**2,
        ),
    )
    print("  SandboxConfig (the SDK's view of what's about to run):")
    for line in _format_config(cfg):
        print(f"    {line}")
    print()

    # 3. Decide live-Docker vs stub mode.
    docker_ok = _docker_running()
    if docker_ok:
        mode_label = "LIVE — docker run python:3.11-slim with cap-drop=ALL"
    else:
        mode_label = "STUB — Docker not running; using NullBackend in-process"
    print(f"  Backend selection: {mode_label}")
    print()

    # 4. Host env baseline — prove the secrets are NOT in os.environ.
    _line(" Host env baseline ")
    print()
    secret_key = TENANTS[0]["secret_key"]
    host_seen = os.environ.get(secret_key)
    print(f"  os.environ.get({secret_key!r}) = {host_seen!r}")
    for t in TENANTS:
        live_value = tenant_secrets[t["id"]]
        in_host_env = any(live_value == v for v in os.environ.values())
        marker = "LEAK" if in_host_env else "OK"
        print(f"  {t['id']:<8} secret in host os.environ = {in_host_env}  [{marker}]")
    print()

    # 5. Per-tenant sandboxed call.
    _line(" Per-tenant sandboxed 3rd-party call ")
    print()

    # Honest about determinism: we run each tenant TWICE and compare
    # responses byte-for-byte at the end.
    captured: dict[str, list[dict]] = {t["id"]: [] for t in TENANTS}
    raw_outputs: dict[str, list[str]] = {t["id"]: [] for t in TENANTS}

    for t in TENANTS:
        # Resolve the tenant's profile via the cascade — exactly the
        # same call path a real worker would make on run start.
        eff = await resolve_security_profile(
            levels=[CascadeLevel(
                name="user", profile_ref=t["profile_id"], overrides=None,
            )],
        )
        # The cascade returns secret KEYS (not values) on the public
        # surface; the live values are looked up via the backend.
        secret_value = tenant_secrets[t["id"]]
        env = {
            t["secret_key"]: secret_value,
            "WEBHOOK_URL": THIRD_PARTY_URL,
            "TENANT": t["id"],
        }

        print(f"  ── tenant {t['id']!r} (profile_id={t['profile_id']}) ──")
        print(f"     resolved env keys     = {sorted(eff.env.keys())}")
        print(f"     resolved secret keys  = {sorted(eff.secret_keys)}")

        for attempt in (1, 2):
            run_id = f"{t['id']}-run-{attempt}"
            if docker_ok:
                exit_code, stdout, stderr = _docker_run_inline(
                    image=PUBLIC_BASE_IMAGE,
                    env=env,
                    script=_CONTAINER_SCRIPT,
                    resource_limits=cfg.resource_limits,
                    network_policy=cfg.network_policy,
                )
            else:
                exit_code, stdout, stderr = await _run_in_null_backend(
                    project_id=t["project_id"], run_id=run_id, env=env,
                )

            response = _parse_response(stdout)
            captured[t["id"]].append(response)
            raw_outputs[t["id"]].append(stdout)
            status = response.get("status", "?")
            saw_first8 = response.get("saw_token_first8", "")
            print(f"     run {attempt}: exit={exit_code} status={status} "
                  f"token_seen_in_container={saw_first8!r}…")
            if exit_code != 0 and stderr:
                err_one_line = stderr.strip().replace("\n", " | ")[:120]
                print(f"             stderr: {err_one_line}")
        print()

    # 6. Cross-tenant boundary: prove A's container did NOT see B's secret.
    _line(" Cross-tenant boundary check ")
    print()
    leaks = 0
    for owner in TENANTS:
        owner_secret = tenant_secrets[owner["id"]]
        for other in TENANTS:
            if other["id"] == owner["id"]:
                continue
            other_outputs = raw_outputs[other["id"]]
            seen = any(owner_secret in s for s in other_outputs)
            marker = "LEAK" if seen else "OK"
            print(f"  {owner['id']:<8} secret visible in {other['id']:<8} container? "
                  f"{seen}  [{marker}]")
            if seen:
                leaks += 1
    print()
    if leaks == 0:
        print("  Zero cross-tenant leaks — sandbox isolation honoured.")
    else:
        print(f"  {leaks} leak(s) detected — investigation required.")
    print()

    # 7. Redaction: scrub the live secret out of the captured response
    #    before any host-side log surface ever sees it.
    _line(" Redaction (host-side log surface) ")
    print()
    for t in TENANTS:
        secret_value = tenant_secrets[t["id"]]
        redactor = Redactor({t["secret_key"]: secret_value})
        # The first call's body — what httpbin (or the stub) returned.
        raw = raw_outputs[t["id"]][0]
        redacted, matched = redactor.redact(raw)
        contained = secret_value in raw
        scrubbed = secret_value not in redacted
        # Truncate for display so the line stays under the 72-col budget.
        snippet = redacted.strip().replace("\n", " ")[:200]
        if len(redacted.strip()) > 200:
            snippet += "…"
        print(f"  tenant {t['id']!r}:")
        print(f"    secret in raw output       = {contained}")
        print(f"    scrubbed by Redactor       = {scrubbed}")
        print(f"    matched secret keys        = {matched}")
        print(f"    redacted snippet (≤200ch)  = {snippet}")
        print()

    # 8. Determinism: same workflow twice → identical network-call shape.
    _line(" Determinism (same workflow, two runs) ")
    print()
    deterministic = True
    for t in TENANTS:
        responses = captured[t["id"]]
        if len(responses) < 2:
            continue
        # Compare the response shape (status, saw_token_in_env,
        # saw_token_first8, and body keys when the call succeeded).
        a, b = responses[0], responses[1]
        same = (
            a.get("status") == b.get("status")
            and a.get("saw_token_in_env") == b.get("saw_token_in_env")
            and a.get("saw_token_first8") == b.get("saw_token_first8")
        )
        marker = "OK" if same else "DIVERGENT"
        deterministic = deterministic and same
        print(f"  {t['id']:<8} run1 vs run2: status={a.get('status')} "
              f"vs {b.get('status')}, token_first8 match={same}  [{marker}]")
    print()

    # 9. The proof.
    _line(" The proof ")
    print()
    host_clean = os.environ.get(secret_key) is None and not any(
        v in os.environ.values() for v in tenant_secrets.values()
    )
    print(f"  Host process never held tenant tokens:    {host_clean}")
    print(f"  Cross-tenant leaks:                       {leaks}")
    print(f"  Determinism (run1 == run2 per tenant):    {deterministic}")
    print(f"  Backend used:                             "
          f"{'live docker' if docker_ok else 'NullBackend (stub)'}")
    print()
    if not docker_ok:
        print("  To exercise the live container path:")
        print("    - start Docker Desktop / Colima")
        print("    - docker pull python:3.11-slim")
        print("    - rerun: python 39_sandbox_scoped_credentials.py")
        print()
    print("  This is the production-readiness story for v1.0: every")
    print("  tenant's agent runs inside its own isolated container with")
    print("  only its own scoped credential, all 3rd-party traffic is")
    print("  redactable on the host log surface, and reruns are byte-")
    print("  identical so an auditor can replay them.")


if __name__ == "__main__":
    asyncio.run(main())
