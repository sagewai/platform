#!/usr/bin/env python3
# Copyright 2026 Ali Arda Diri, Berlin, Germany.
"""Ensure a local admin exists so single-user fleet recipes can auto-auth.

Idempotent and honors ``SAGEWAI_HOME``. If no admin is configured yet, it completes
first-run setup with a RANDOM password you never need: the gateway, started with
``SAGEWAI_DEV_TRUST_LOCAL=1``, issues short-lived session tokens to localhost callers
via ``POST /api/v1/auth/refresh`` (see ``just fleet-demo-up`` / ``just _fleet-token``).

Loopback dev-trust only — filesystem access to the state file is the "same machine"
trust boundary. Never run this against, or point these recipes at, a shared/production
gateway: the issued tokens are full-admin.

    uv run --package sagewai python scripts/fleet-local-setup.py
"""
from __future__ import annotations

import json
import secrets

from sagewai.admin.state_file import AdminStateFile, default_admin_state_path


def main() -> int:
    path = default_admin_state_path()
    has_admin = path.exists() and bool(json.loads(path.read_text() or "{}").get("admin"))
    if has_admin:
        print(f"admin already configured ({path})")
        return 0
    AdminStateFile().complete_setup(
        org_name="Local",
        admin_email="local@sagewai.dev",
        admin_password=secrets.token_urlsafe(24),  # unused — dev-trust mints tokens
    )
    print(f"admin created ({path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
