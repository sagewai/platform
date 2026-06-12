# Copyright 2026 Ali Arda Diri, Berlin, Germany
#
# This file is part of Sagewai, licensed under the GNU Affero General
# Public License v3.0 or later (AGPL-3.0-or-later). You may use,
# modify, and distribute this file under the terms of the AGPL.
# See the LICENSE file or https://www.gnu.org/licenses/agpl-3.0.html
#
# This file is also available under a commercial license.
# See COMMERCIAL-LICENSE.md for details.
"""Production deployment safety validator.

A single fail-fast gate, run from the admin lifespan **only** when
``SAGEWAI_ENV == "production"``. It refuses to start an unsafe production
deployment by aggregating *every* misconfiguration into one
:class:`RuntimeError` (rather than failing on the first), so the operator fixes
the whole list in one pass.

Outside production (``SAGEWAI_ENV`` unset, or ``development``/``test``/anything
else) it is a **no-op** — dev, test, and single-org local deployments are
completely unaffected.

The checks are deliberately conservative; they catch the deployment mistakes
that would silently weaken isolation or lose data:

* ``SAGEWAI_TENANCY_MODE`` is explicitly set to a recognised value (so the mode
  is a deliberate choice, never the silent ``single`` default in production).
* In multi-tenant mode, a Postgres ``DATABASE_URL`` / ``SAGEWAI_DATABASE_URL``
  is configured (a shared, durable store — not the per-process SQLite default).
* A master key is resolvable (env → keychain → file) — reusing the existing
  :func:`sagewai.sealed.master_key.resolve_master_key` resolution; without it,
  encrypted secrets cannot be decrypted.
* In multi-tenant mode, host execution is OFF
  (``SAGEWAI_ALLOW_HOST_EXEC`` not enabled) — a tenant must never reach host
  bash / NullBackend / stdio MCP.
* CORS origins are explicit (``SAGEWAI_ADMIN_ALLOWED_ORIGINS`` set, not the
  localhost dev default).
* ``SAGEWAI_ADMIN_TLS=1`` (session cookies are issued ``Secure``).
"""
from __future__ import annotations

import os

# Recognised tenancy-mode tokens. ``tenancy_mode()`` resolves anything outside
# the multi aliases to "single", so an explicit-and-valid check has to know the
# accepted single-org spellings too (else a typo would silently run single-org).
_VALID_SINGLE = {"single", "single-org", "singleorg", "single_org"}
_VALID_MULTI = {"multi", "multi-tenant", "multitenant", "mt"}
_TRUE = {"1", "true"}
# The localhost dev default baked into the CORS allowlist — treated as "unset"
# for production purposes (a production deployment must name its real origin).
_LOCALHOST_ORIGINS_DEFAULT = "http://localhost:3008,http://127.0.0.1:3008"


def is_production() -> bool:
    """True when ``SAGEWAI_ENV`` selects the production profile."""
    return os.environ.get("SAGEWAI_ENV", "").strip().lower() == "production"


def _master_key_resolvable() -> bool:
    """Whether a master key resolves *without* auto-provisioning.

    Reuses :func:`sagewai.sealed.master_key.resolve_master_key` (env → keychain
    → file) — the same resolver behind ``sf.require_secret_key_if_encrypted()``
    and ``_require_tenant_provider_key_if_encrypted`` — and treats a
    ``MasterKeyMissing`` as "not resolvable" (we do NOT silently mint one in
    production; the operator must supply a persistent key).
    """
    from sagewai.sealed.master_key import MasterKeyMissing, resolve_master_key

    try:
        resolve_master_key()
    except MasterKeyMissing:
        return False
    except Exception:
        # A malformed key (wrong length/encoding) is also "not safely resolvable".
        return False
    return True


def validate_production_config() -> None:
    """Fail fast unless the deployment is production-safe.

    No-op unless ``SAGEWAI_ENV == "production"``. Otherwise aggregates **all**
    problems into one :class:`RuntimeError` (never failing on the first), so the
    whole list is visible to the operator at once.
    """
    if not is_production():
        return

    problems: list[str] = []
    env = os.environ

    # 1) Tenancy mode must be explicitly set to a recognised value.
    raw_mode = env.get("SAGEWAI_TENANCY_MODE")
    mode_token = (raw_mode or "").strip().lower()
    is_multi = mode_token in _VALID_MULTI
    if raw_mode is None or raw_mode.strip() == "":
        problems.append(
            "SAGEWAI_TENANCY_MODE is not set — set it explicitly to 'single' or "
            "'multi' (production must not rely on the silent single-org default)."
        )
    elif mode_token not in _VALID_SINGLE and mode_token not in _VALID_MULTI:
        problems.append(
            f"SAGEWAI_TENANCY_MODE={raw_mode!r} is not a recognised value — use "
            "'single' or 'multi'."
        )

    # 2) Multi-tenant requires a configured Postgres DATABASE_URL.
    if is_multi:
        db_url = env.get("SAGEWAI_DATABASE_URL") or env.get("DATABASE_URL")
        if not db_url:
            problems.append(
                "multi-tenant production requires a Postgres DATABASE_URL "
                "(set SAGEWAI_DATABASE_URL or DATABASE_URL) — the per-process "
                "SQLite default is not a shared, durable multi-tenant store."
            )
        elif not (
            db_url.startswith("postgres://")
            or db_url.startswith("postgresql://")
            or db_url.startswith("postgresql+")
        ):
            problems.append(
                f"DATABASE_URL={db_url!r} is not a Postgres URL — multi-tenant "
                "production must use Postgres."
            )

    # 3) A master key must be resolvable (reuses the existing resolver).
    if not _master_key_resolvable():
        problems.append(
            "no master key is resolvable — set SAGEWAI_MASTER_KEY or mount a "
            "master.key file on a persistent volume (losing it makes encrypted "
            "secrets unrecoverable)."
        )

    # 4) Multi-tenant must have host execution OFF.
    if is_multi and env.get("SAGEWAI_ALLOW_HOST_EXEC", "").strip().lower() in _TRUE:
        problems.append(
            "SAGEWAI_ALLOW_HOST_EXEC is enabled in multi-tenant mode — host-backed "
            "execution must be OFF (a tenant must never reach host bash / "
            "NullBackend / stdio MCP). Unset SAGEWAI_ALLOW_HOST_EXEC."
        )

    # 5) CORS origins must be explicit (not the localhost dev default).
    raw_origins = env.get("SAGEWAI_ADMIN_ALLOWED_ORIGINS")
    origins = (raw_origins or "").strip()
    if not origins or origins == _LOCALHOST_ORIGINS_DEFAULT:
        problems.append(
            "SAGEWAI_ADMIN_ALLOWED_ORIGINS is not set to an explicit production "
            "origin (it is empty or the localhost dev default) — set it to the "
            "admin UI's real origin(s)."
        )

    # 6) TLS must be on (Secure session cookies).
    if env.get("SAGEWAI_ADMIN_TLS", "").strip().lower() not in _TRUE:
        problems.append(
            "SAGEWAI_ADMIN_TLS is not enabled — set SAGEWAI_ADMIN_TLS=1 so session "
            "cookies are issued Secure (HTTPS-only)."
        )

    if problems:
        bullet = "\n  - ".join(problems)
        raise RuntimeError(
            "Refusing to start: SAGEWAI_ENV=production but the deployment is "
            f"not production-safe ({len(problems)} problem(s)):\n  - {bullet}"
        )
